from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from twilio.rest import Client
from home_page.models import NotificationPreference, SentNotification
from home_page.services.calendar_service import GoogleCalendarService
import logging
from django.db.models import Q
from home_page.services.ai_agent import AIAgent
from concurrent.futures import ThreadPoolExecutor
from django.core.cache import cache

logger = logging.getLogger(__name__)

import json # Ensure json is imported

def send_whatsapp_message(to_number, body=None, content_sid=None, content_variables=None):
    """
    Sends a WhatsApp message using Twilio.
    Supports raw body OR Content Templates (content_sid + content_variables).
    """
    try:
        account_sid = getattr(settings, 'TWILIO_ACCOUNT_SID', None)
        auth_token = getattr(settings, 'TWILIO_AUTH_TOKEN', None)
        # Check for TWILIO_WHATSAPP_NUMBER or TWILIO_PHONE_NUMBER
        from_number = getattr(settings, 'TWILIO_WHATSAPP_NUMBER', None) or getattr(settings, 'TWILIO_PHONE_NUMBER', None)

        if not all([account_sid, auth_token, from_number]):
            logger.error("Twilio credentials missing. Cannot send WhatsApp message.")
            return False

        # Ensure from_number is in whatsapp format
        if not from_number.startswith('whatsapp:'):
            from_number = f"whatsapp:{from_number}"

        client = Client(account_sid, auth_token)
        
        # Ensure to_number is in whatsapp format
        if not to_number.startswith('whatsapp:'):
            to_number = f"whatsapp:{to_number}"

        if content_sid:
             message = client.messages.create(
                from_=from_number,
                to=to_number,
                content_sid=content_sid,
                content_variables=content_variables
            )
        else:
            message = client.messages.create(
                from_=from_number,
                body=body,
                to=to_number
            )
        logger.info(f"WhatsApp message sent to {to_number}: {message.sid}")
        return True
    except Exception as e:
        logger.error(f"Failed to send WhatsApp message to {to_number}: {e}")
        return False

def check_and_send_reminders():
    """
    Polls for upcoming events (next 30 mins) and sends reminders.
    Intended to be called periodically (e.g. every minute).
    """
    logger.info("Checking for reminders...")
    
    # 1. Get all users who have notification preferences enabled
    preferences = NotificationPreference.objects.filter(
        Q(whatsapp_enabled=True) | Q(email_enabled=True)
    ).select_related('user')

    # Use ThreadPoolExecutor for concurrent processing
    with ThreadPoolExecutor(max_workers=5) as executor:
        for pref in preferences:
            executor.submit(process_user_reminders, pref)

def process_user_reminders(pref):
    """
    Process reminders for a single user with their specific preferences.
    """
    user = pref.user
    
    # Use user's specific lookahead time (default 30 mins)
    lead_time = getattr(pref, 'reminder_lead_time', 30)
    
    now = timezone.now()
    lookahead = now + timedelta(minutes=lead_time)
    
    logger.info(f"Checking events for user: {user.username} (Lookahead: {lead_time}m)")

    try:
        try:
            cal_service = GoogleCalendarService(user)
        except Exception as e:
            logger.warning(f"Could not init calendar service for {user.username}: {e}")
            return

        # List events
        events = cal_service.list_events(
            time_min=now.isoformat(),
            time_max=lookahead.isoformat()
        )
        
        if not events:
            return
        
        # Initialize AI Agent only if used
        ai_agent = AIAgent(user)

        for event in events:
            try:
                event_id = event['id']
                summary = event.get('summary', '(No Title)')
                start_raw = event.get('start', {}).get('dateTime', event.get('start', {}).get('date'))
                
                # Deduplication logic
                already_notified_whatsapp = False
                already_notified_email = False
                
                if pref.whatsapp_enabled:
                    already_notified_whatsapp = SentNotification.objects.filter(
                        user=user, event_id=event_id, notification_type='whatsapp', status='sent'
                    ).exists()
                    
                    # Check for active snooze (if not already found as sent)
                    if not already_notified_whatsapp:
                        last_snooze = SentNotification.objects.filter(
                            user=user, event_id=event_id, notification_type='whatsapp', status='snoozed'
                        ).order_by('-timestamp').first()
                        
                        if last_snooze:
                            # If snoozed less than 10 mins ago, treat as active (don't send yet)
                            if timezone.now() < last_snooze.timestamp + timedelta(minutes=10):
                                already_notified_whatsapp = True
                    # Also check failure count? If failed 3 times, treat as "done" (gave up)
                    failed_count_wa = SentNotification.objects.filter(
                         user=user, event_id=event_id, notification_type='whatsapp', status='failed'
                    ).count()
                    if failed_count_wa >= 3:
                        logger.info(f"Skipping WA for {event_id}: too many failures.")
                        already_notified_whatsapp = True
                    
                if pref.email_enabled:
                     already_notified_email = SentNotification.objects.filter(
                        user=user, event_id=event_id, notification_type='email', status='sent'
                    ).exists()
                     failed_count_email = SentNotification.objects.filter(
                         user=user, event_id=event_id, notification_type='email', status='failed'
                    ).count()
                     if failed_count_email >= 3:
                         logger.info(f"Skipping Email for {event_id}: too many failures.")
                         already_notified_email = True

                # If both notified (or disabled), skip
                if (not pref.whatsapp_enabled or already_notified_whatsapp) and \
                   (not pref.email_enabled or already_notified_email):
                    continue

                # Generate AI Message
                
                ai_message = ai_agent.generate_reminder_message(summary, start_raw, user.username)
                
                # --- WhatsApp ---
                if pref.whatsapp_enabled and pref.whatsapp_number and not already_notified_whatsapp:
                    # Append interactive footer
                    wa_body = f"{ai_message}\n\nReply:\n- *SNOOZE 10* to snooze 10m\n- *OFF* to disable reminders"
                    
                    success = send_whatsapp_message(pref.whatsapp_number, wa_body)
                    status_val = 'sent' if success else 'failed'
                    
                    # Log attempt
                    SentNotification.objects.create(
                        user=user,
                        event_id=event_id,
                        notification_type='whatsapp',
                        status=status_val
                    )
                    
                # --- Email ---
                if pref.email_enabled and not already_notified_email:
                    to_email = user.email
                    subject = f"Reminder: {summary}"
                    # Common body content
                    email_body_text = f"{ai_message}\n\nBest,\nReminder Agent"
                    
                    success_email = False
                    try:
                        # Check if SMTP is configured
                        if settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD:
                            from django.core.mail import send_mail
                            send_mail(
                                subject=subject,
                                message=email_body_text,
                                from_email=f"Reminder Agent <{settings.EMAIL_HOST_USER}>",
                                recipient_list=[to_email],
                                fail_silently=False,
                            )
                            logger.info(f"SMTP Email sent to {to_email} for event {summary}")
                            success_email = True
                        else:
                            # Fallback to OAuth (User's own Gmail)
                            cal_service.send_email(to_email, subject, email_body_text)
                            logger.info(f"OAuth Email sent to {to_email} for event {summary}")
                            success_email = True
                            
                    except Exception as e:
                        logger.error(f"Failed to send email to {to_email}: {e}")
                        success_email = False
                    
                    status_val = 'sent' if success_email else 'failed'
                    SentNotification.objects.create(
                        user=user,
                        event_id=event_id,
                        notification_type='email',
                        status=status_val
                    )
                    
            except Exception as ev_e:
                 logger.error(f"Error processing event {event.get('id')}: {ev_e}")

    except Exception as e:
        logger.error(f"Error processing user {user.username}: {e}")

def check_and_send_morning_briefings():
    """
    Checks if it's time to send morning briefings for users.
    Should be called periodically (e.g., every minute).
    """
    # logger.info("Checking for morning briefings...") # Verbose
    now = timezone.now()
    current_time = now.time()
    
    # Filter users with morning briefing enabled
    preferences = NotificationPreference.objects.filter(morning_briefing_enabled=True).select_related('user')
    
    for pref in preferences:
        briefing_time = pref.morning_briefing_time
        # Check if current hour/minute matches (handling minute granularity)
        if briefing_time.hour == current_time.hour and briefing_time.minute == current_time.minute:
             today_str = now.strftime("%Y-%m-%d")
             cache_key = f"morning_briefing_{pref.user.id}_{today_str}"
             
             if not cache.get(cache_key):
                 logger.info(f"Sending morning briefing for {pref.user.username}")
                 try:
                     # 1. Fetch today's events
                     try:
                        cal_service = GoogleCalendarService(pref.user)
                     except Exception as e:
                        logger.warning(f"Skipping briefing for {pref.user.username}, calendar service error: {e}")
                        continue

                     start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
                     end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)
                     
                     events = cal_service.list_events(
                         time_min=start_of_day.isoformat(),
                         time_max=end_of_day.isoformat()
                     )
                     
                     # 2. Generate briefing
                     ai_agent = AIAgent(pref.user)
                     briefing_msg = ai_agent.generate_morning_briefing(events, pref.user.first_name or pref.user.username)
                     
                     # 3. Send via WhatsApp
                     if pref.whatsapp_number:
                         send_whatsapp_message(pref.whatsapp_number, briefing_msg)
                         logger.info(f"Sent morning briefing to {pref.user.username}")
                     else:
                         logger.warning(f"User {pref.user.username} has no WhatsApp number for briefing.")
                     
                     # 4. Mark as sent
                     cache.set(cache_key, True, timeout=86400) # 24h
                     
                 except Exception as e:
                     logger.error(f"Failed to send briefing to {pref.user.username}: {e}")
