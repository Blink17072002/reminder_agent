from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse # Added for webhook response
from .models import NotificationPreference, SentNotification # Added for webhook logic
from django.utils import timezone # Added for snooze logic
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse, Http404
from .services.calendar_service import GoogleCalendarService
from .services.ai_agent import AIAgent
from allauth.socialaccount.models import SocialToken
from django.contrib import messages
from .models import Conversation, Message
import json
import uuid
import os
from django.conf import settings
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
import logging
import urllib.parse
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
import traceback
from datetime import datetime, timedelta
from django.utils.timezone import get_current_timezone, make_aware
import re as _re
# Create your views here.



logger = logging.getLogger(__name__)

def _parse_simple_date(val: str, tz=None):
    """
    Parses a simple date string (YYYY-MM-DD, 'today', 'tomorrow', 'next friday', etc.)
    into a datetime.date object.
    """
    if not val:
        return None
    
    if tz is None:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(getattr(settings, 'TIME_ZONE', 'UTC') or 'UTC')
        except Exception:
            tz = get_current_timezone()

    s = str(val).strip().lower()
    
    # YYYY-MM-DD
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.fromisoformat(s + 'T00:00:00').date()
        except Exception:
            return None
            
    today = datetime.now(tz).date()
    if s == 'today':
        return today
    if s == 'tomorrow':
        return today + timedelta(days=1)
        
    weekdays = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6
    }
    
    parts = s.split()
    prefix_next = (len(parts) == 2 and parts[0] == 'next' and parts[1] in weekdays)
    
    if prefix_next or s in weekdays:
        target_idx = weekdays[parts[1]] if prefix_next else weekdays[s]
        delta = (target_idx - today.weekday()) % 7
        if delta == 0 and prefix_next:
            delta = 7
        if delta < 0:
            delta += 7
        # If user says "Friday" and today is Friday, they usually mean next Friday unless they say "this Friday"
        # But for safety, if delta is 0 (today), we'll assume today.
        # If they meant next week, they usually say "next Friday".
        # However, if today is Tuesday and they say "Monday", they might mean next Monday.
        # Let's stick to the logic:
        # "Friday" -> next upcoming Friday (could be today if today is Friday)
        # But if today is Friday, delta is 0.
        if delta == 0 and not prefix_next:
             # If today is the day, assume today.
             pass
        elif delta == 0 and prefix_next:
             delta = 7
             
        return today + timedelta(days=delta)
        
    return None



@login_required
def assistant(request, convo_id=None, is_placeholder=False):
    user = request.user
    # Fetch conversations ordered by creation date, newest first
    conversations = Conversation.objects.filter(user=user).order_by('-created_at')

    
    # Check if this is a freshly created conversation to trigger animation
    # We can detect this if the conversation has exactly 1 message and it's from the agent (our welcome msg)
    # AND if we pass a specific flag (e.g. from redirect) or just infer it.
    # Simpler: just check if there is only 1 message.
    # But wait, persistence means it's just a message. The USER wants it to "type out" if they just created it.
    # We can pass a flag if referer was 'new_conversation' or via session.
    
    # Let's use the 'just_created' query param approach or similar if needed.
    # Actually, the user said "continue whether I'm present... resume when I go back".
    # This implies standard message rendering but with animation for the latest one if it's "fresh".
    # Since we persist it, it's just a message.
    # We'll leave standard rendering. If the user wants the *first* message to always animate on *first load*,
    # we can check if len(messages) == 1 and sender=agent.
    
    # Remove prematurely defined context and logic block

    if conversations.filter(id=convo_id).exists():
        msg_count = Message.objects.filter(conversation_id=convo_id).count()
        # Initial check, but we will refine this after fetching messages
        
    convo = None # Initialize current conversation object
    messages_to_render = [] # Initialize messages list to pass to template
    welcome_message_for_frontend = None # Initialize welcome message text for frontend

    # Define the welcome message text (can be defined once outside the view function if preferred)
    welcome_message_text_content = "Hi! I'm your professional calendar assistant. I can help you manage your schedule, create and update events, find optimal meeting times, and provide scheduling suggestions. What would you like me to help you with today?"


    # Handle GET requests (loading the page)
    if request.method == "GET":
        if is_placeholder:
            # Logic for the new chat: Create actual conversation immediately to persist welcome message
            print("GET request for new chat. Creating persistent conversation with welcome message.")
            
            # Create new conversation
            new_convo = Conversation.objects.create(user=user, title="New Chat")
            
            # Generate AI welcome message
            try:
                ai_agent = AIAgent(user)
                welcome_text = ai_agent.generate_welcome_message(user.first_name or "there")
                
                # Persist the welcome message
                Message.objects.create(
                    conversation=new_convo,
                    sender='agent',
                    text=welcome_text,
                    message_type='text'
                )
            except Exception as e:
                print(f"Error generating welcome message: {e}")
                # Fallback to a simple message if AI fails (though generate_welcome_message has its own fallback)
                Message.objects.create(
                    conversation=new_convo,
                    sender='agent',
                    text="Hi! I'm your calendar assistant. How can I help you today?",
                    message_type='text'
                )

            # Redirect to the new conversation
            return redirect('home_page:assistant', convo_id=new_convo.id)
            
        elif convo_id:
            # --- Logic for loading an existing conversation (by ID in URL) ---
            try:
                # Get the conversation object, including the user for the ownership check
                convo = get_object_or_404(Conversation.objects.select_related('user'), id=convo_id, user=user)
                print(f"GET request for conversation ID: {convo.id}. Loading existing conversation.")

                # Fetch all messages for this conversation (including structured ones)
                messages_to_render = list(convo.messages.order_by('timestamp'))

                # by default, don't animate existing chats
                is_new_conversation_page = False

                # BUT if this is the database-persisted initial welcome message (single agent msg),
                # allow animation so it feels like a fresh start.
                if len(messages_to_render) == 1 and messages_to_render[0].sender == 'agent':
                    is_new_conversation_page = True
                    # No need to set welcome_message_for_frontend as it's in the message list
                
                # Fallback for empty (legacy)
                elif len(messages_to_render) == 0:
                    is_new_conversation_page = True
                    welcome_message_for_frontend = welcome_message_text_content

                else:
                    print(f"GET request for existing conversation with {len(messages_to_render)} messages.")


            except (ValueError, uuid.UUID, Http404): # Catch errors for invalid UUID format or non-existent UUID
                print(f"GET request with invalid or non-existent convo ID: {convo_id}. Redirecting to latest or initial state.")
                messages.error(request, "Invalid or non-existent conversation ID.")
                # Attempt to redirect to the latest conversation if one exists
                latest_convo = conversations.first()
                if latest_convo:
                     # Redirect to the assistant view with the latest conversation's ID
                     return redirect('home_page:assistant', convo_id=latest_convo.id)
                else:
                     # If no latest convo, fall through to render the initial empty state
                     #convo_id = None # Set convo_id to None to trigger the next block
                     print("No existing convos to redirect to. Redirecting to new conversation.")
        
        else:
            # handles the base /agent/assistant/ URL without an ID
            if conversations.exists():
                latest_convo = conversations.first()
                print("GET request to base URL with existing convos. Redirecting to latest.")
                return redirect('home_page:assistant', convo_id=latest_convo.id)
            else:
                print("GET request to base URL with no existing convos. Redirecting to new conversation.")
                return redirect('home_page:new_conversation')


        # --- Logic for the true initial empty state (no convo_id in URL AND no existing conversations) ---
        # This block handles the very first time a user visits the page and has no conversations at all.
        # In this case, there is no convo_id in the URL.
        # if not convo_id and not conversations.exists():
        #     print("GET request, no ID in URL, no existing conversations. Showing initial empty state.")
        #     is_new_conversation_page = True # Set this to True for the very first visit
        #     convo = None # Ensure current convo is None
        #     messages_to_render = [] # Ensure no messages are rendered initially
        #     # Pass the welcome message text to the frontend context for animation
        #     welcome_message_for_frontend = welcome_message_text_content

        # Note: If there are existing conversations but no convo_id in the URL (e.g., user manually goes to /agent/assistant/),
        # the page will render with the recents list but no selected conversation in the chat panel.
        # is_new_conversation_page will be False in this scenario.
        # The frontend might need adjustments to handle this state gracefully (e.g., display a message "Select a conversation or start a new one").


    # If it's a POST request to this view, it's an error as POSTs should go to chat_process
    if request.method == "POST":
        raise Http404("POST requests to /agent/assistant/ are not allowed. Use /agent/chat/process/.")


    # Prepare messages for rendering - serialize JSON content for JavaScript
    messages_with_json = []
    for msg in messages_to_render:
        msg_dict = {
            'id': msg.id,
            'sender': msg.sender,
            'text': msg.text,
            'message_type': msg.message_type,
            'content': msg.content,
            'content_json': json.dumps(msg.content) if msg.content else None,
            'timestamp': msg.timestamp,
        }
        messages_with_json.append(msg_dict)

    # Prepare the context data to pass to the template
    context = {
        "conversations": conversations, # List of all recent conversations
        "current_convo": convo, # The currently selected conversation object (or None)
        "messages": messages_with_json, # Messages for the current_convo (or empty list)
        "is_new_conversation_page": is_new_conversation_page, # Flag for frontend animation
        # Pass welcome message text only when the flag is True
        "welcome_message_text": welcome_message_for_frontend if is_new_conversation_page else None,
        "active_convo_id": str(convo.id) if convo else None,
        "google_calendar_icon_url": os.path.join(settings.STATIC_URL, 'home_page/images/google_calendar_icon.svg') # Assuming this is needed
    }

    print(f"Rendering assistant.html with is_new_conversation_page={is_new_conversation_page}, {len(messages_with_json)} messages, current_convo={convo.id if convo else 'None'}.")
    return render(request, "home_page/assistant.html", context)









# Helper functions for proactive conflict detection
def events_overlap(event_start, event_end, proposed_start, proposed_end):
    """Check if two time ranges overlap"""
    return event_start < proposed_end and proposed_start < event_end

def check_conflicts_proactively(start_dt, end_dt, gcal):
    """
    Check for actual conflicting events (not just free/busy ranges).
    Returns list of conflicting event objects with details.
    """
    try:
        # Query the entire day to catch all events
        day_start = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = start_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Get all events for that day
        events = gcal.list_events(
            time_min=day_start.isoformat(),
            time_max=day_end.isoformat()
        )
        
        # Filter for actual conflicts
        conflicts = []
        for event in events:
            event_start_str = event.get('start', {}).get('dateTime')
            event_end_str = event.get('end', {}).get('dateTime')
            
            if not event_start_str or not event_end_str:
                continue
            
            # Parse event times
            event_start = datetime.fromisoformat(event_start_str.replace('Z', '+00:00'))
            event_end = datetime.fromisoformat(event_end_str.replace('Z', '+00:00'))
            
            # Check for overlap
            if events_overlap(event_start, event_end, start_dt, end_dt):
                conflicts.append({
                    'summary': event.get('summary', 'Untitled Event'),
                    'start': event_start_str,
                    'end': event_end_str,
                    'id': event.get('id')
                })
        
        return conflicts
    except Exception as e:
        logger.error(f"Error checking conflicts: {e}")
        return []

def find_alternative_times(requested_dt, duration_minutes, gcal, count=3):
    """
    Find alternative free time slots on the same day.
    Returns up to 'count' alternative slots.
    """
    try:
        # Get all events for that day
        day_start = requested_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = requested_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        events = gcal.list_events(
            time_min=day_start.isoformat(),
            time_max=day_end.isoformat()
        )
        
        # Define business hours (9 AM - 6 PM)
        business_start = requested_dt.replace(hour=9, minute=0, second=0)
        business_end = requested_dt.replace(hour=18, minute=0, second=0)
        
        # Create time slots (30-minute intervals)
        alternatives = []
        current_time = business_start
        
        while current_time < business_end and len(alternatives) < count:
            slot_end = current_time + timedelta(minutes=duration_minutes)
            
            # Check if this slot conflicts with any event
            has_conflict = False
            for event in events:
                event_start_str = event.get('start', {}).get('dateTime')
                event_end_str = event.get('end', {}).get('dateTime')
                
                if event_start_str and event_end_str:
                    event_start = datetime.fromisoformat(event_start_str.replace('Z', '+00:00'))
                    event_end = datetime.fromisoformat(event_end_str.replace('Z', '+00:00'))
                    
                    if events_overlap(event_start, event_end, current_time, slot_end):
                        has_conflict = True
                        break
            
            # If no conflict, add as alternative
            if not has_conflict:
                alternatives.append({
                    'start': current_time.isoformat(),
                    'end': slot_end.isoformat()
                })
            
            # Move to next slot (30-minute intervals)
            current_time += timedelta(minutes=30)
        
        return alternatives
    except Exception as e:
        logger.error(f"Error finding alternatives: {e}")
        return []

# Or ensure CsrfViewMiddleware is active and JS sends the token in header (as done above)
@csrf_exempt
def whatsapp_reply(request):
    """
    Handle incoming WhatsApp messages (webhooks).
    Supports:
    - OFF: Disable WhatsApp notifications
    - SNOOZE: Snooze the last reminder for 10 minutes
    """
    if request.method == 'POST':
        # 0. Validate Twilio Signature (Security)
        # Skip validation only if DEBUG is True AND we accept it (optional, but good for local dev without ngrok auth)
        if not settings.DEBUG:
            from twilio.request_validator import RequestValidator
            validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
            
            # The URL Twilio thinks it hit (must match exactly what you configured)
            # request.build_absolute_uri() might return http depending on proxy headers
            signature = request.META.get('HTTP_X_TWILIO_SIGNATURE', '')
            url = request.build_absolute_uri()
            
            # Convert QueryDict to dict for validator
            post_vars = request.POST.dict()

            if not validator.validate(url, post_vars, signature):
                return HttpResponse('Forbidden', status=403)

        # Twilio sends 'From' as 'whatsapp:+123456789'
        from_number = request.POST.get('From', '').replace('whatsapp:', '')
        body = request.POST.get('Body', '').strip().upper()
        
        try:
            # Find user by whatsapp number
            # Try exact match, then with/without +
            prefs = NotificationPreference.objects.filter(whatsapp_number=from_number).first()
            if not prefs:
                if from_number.startswith('+'):
                     prefs = NotificationPreference.objects.filter(whatsapp_number=from_number[1:]).first()
                else:
                     prefs = NotificationPreference.objects.filter(whatsapp_number=f"+{from_number}").first()
            
            if not prefs:
                logger.warning(f"WhatsApp reply from unknown number: {from_number}")
                return HttpResponse('User not found', status=200)
            
            user = prefs.user
            
            if body == 'OFF':
                prefs.whatsapp_enabled = False
                prefs.save()
                return HttpResponse('Disabled', status=200)
            
            elif body.startswith('SNOOZE'):
                # Find the last sent reminder for this user
                last_notif = SentNotification.objects.filter(
                    user=user, 
                    notification_type='whatsapp',
                    status='sent'
                ).order_by('-timestamp').first()
                
                if last_notif:
                    # Update status to snoozed and timestamp to NOW (start of snooze period)
                    last_notif.status = 'snoozed'
                    last_notif.timestamp = timezone.now()
                    last_notif.save()
                    logger.info(f"Snoozed reminder for {user.username}")
                
                return HttpResponse('Snoozed', status=200)
                
        except Exception as e:
            print(f"Error handling WhatsApp reply: {e}")
            
    return HttpResponse('OK', status=200)

@csrf_exempt # <--- Add this decorator temporarily for testing JSON post (remove in production and handle CSRF properly)
# Or better, handle CSRF token check manually if not using CsrfViewMiddleware globally
# Or ensure CsrfViewMiddleware is active and JS sends the token in header (as done above)
def chat_process(request):
    # Ensure it's a POST request
    if request.method != "POST":
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        # Load JSON data from the request body
        # Ensure CsrfViewMiddleware is active OR add @csrf_exempt for testing
        data = json.loads(request.body)
        user_input = data.get("message", "").strip()
        convo_id = data.get("convo_id") # Get convo_id from JSON data
        # Client-reported IANA timezone (e.g., "Europe/London")
        client_tz_name = data.get("client_tz")
        confirmation_data = data.get("confirmation_data")
        message_id = data.get("message_id")

        if not user_input and not confirmation_data:
             # Handle empty message appropriately, maybe return existing messages or an error
             return JsonResponse({'error': 'Empty message received'}, status=400)

        user = request.user
        convo = None

        # ... (rest of your chat_process logic for handling convo_id,
        # fetching/creating conversation, creating user message) ...

        if convo_id:
            try:
                convo = get_object_or_404(Conversation, id=convo_id, user=user)
            except (ValueError, uuid.UUID):
                 return JsonResponse({'error': 'Invalid conversation ID'}, status=400)
            except Http404:
                 return JsonResponse({'error': 'Conversation not found'}, status=404)
        else:
             # Create a new conversation if no convo_id is provided
            convo = Conversation.objects.create(user=user, title="New Chat")
            # Mark as first message if it's the first in this newly created convo
            # is_first_actual_message = True # This logic needs adjustment if convo is new here

        # Now, message history logic needs the actual convo object
        # Title generation check: If the title is still the default "New Chat", we should generate a title
        is_first_actual_message = (convo.title == "New Chat") if convo else True
        
        if user_input:
            user_message = Message.objects.create(
                conversation=convo,
                sender='user',
                text=user_input,
                message_type='text',
                content=None,
            )

        # Get agent response using AIAgent
        # Pass the convo object to the AIAgent handle method
        ai_agent = AIAgent(user)

        # Check for textual confirmation of deletion
        if user_input and convo:
            last_message = convo.messages.order_by('-timestamp').first()
            if last_message and last_message.sender == 'agent' and last_message.message_type == 'event_deletion_confirmation':
                # Check if user said yes
                affirmative_responses = ['yes', 'y', 'sure', 'ok', 'okay', 'confirm', 'please do', 'go ahead']
                if user_input.lower().strip() in affirmative_responses:
                    print("User confirmed deletion via text.")
                    # Construct confirmation data from the last message's content
                    confirmation_data = last_message.content
                    # Ensure action is set to delete
                    if confirmation_data:
                        confirmation_data['action'] = 'delete'
                        # Proceed to the confirmation handling block below...
                
                # Check if user said no
                negative_responses = ['no', 'n', 'cancel', 'stop', 'don\'t', 'do not']
                if user_input.lower().strip() in negative_responses:
                     return JsonResponse({
                        'type': 'text',
                        'response': "Deletion cancelled.",
                        'content': {},
                        'intent': 'calendar',
                        'convo_id': str(convo.id)
                    })

        # Check for confirmation_data to bypass AI and create event directly
        # confirmation_data is already extracted above
        if confirmation_data:
            print(f"Confirmation data received: {confirmation_data}")
            if not AIAgent(request.user).is_google_connected():
                 return JsonResponse({
                     'type': 'needs_connection',
                     'response': 'Please connect your Google account to confirm this event.',
                     'content': {
                         'content_url': reverse('home_page:connect_google') + f"?next={reverse('home_page:assistant', args=[convo.id])}",
                         'email': request.user.email
                     }
                 })
            
            try:
                gcal = GoogleCalendarService(request.user)
                # Delete the draft message if ID is provided
                if message_id:
                    try:
                        Message.objects.filter(id=message_id, conversation=convo).delete()
                    except Exception as e:
                        print(f"Failed to delete draft message {message_id}: {e}")

                # Check if this is a deletion confirmation
                if confirmation_data.get('action') == 'delete' or confirmation_data.get('action') == 'delete_bulk':
                    event_id = confirmation_data.get('event_id')
                    calendar_id = confirmation_data.get('calendar_id', 'primary')
                    
                    # Check if this is bulk deletion (comma-separated IDs)
                    if ',' in event_id:
                        event_ids = event_id.split(',')
                        deleted_count = 0
                        for eid in event_ids:
                            try:
                                gcal.delete_event(calendar_id, eid.strip())
                                deleted_count += 1
                            except Exception as e:
                                print(f"Failed to delete event {eid}: {e}")
                        
                        success_msg = f"{deleted_count} events have been removed from your calendar."
                    else:
                        # Single event deletion
                        gcal.delete_event(calendar_id, event_id)
                        success_msg = "The event has been removed from your calendar."
                    
                    # Delete the draft message if ID is provided
                    if message_id:
                        try:
                            Message.objects.filter(id=message_id, conversation=convo).delete()
                        except Exception as e:
                            print(f"Failed to delete draft message {message_id}: {e}")
                    
                    # Persist success message
                    Message.objects.create(
                        conversation=convo,
                        sender='agent',
                        text=success_msg,
                        message_type='event_deleted',
                        content={'event_id': event_id}
                    )

                    return JsonResponse({
                        'type': 'event_deleted',
                        'response': success_msg,
                        'content': {'event_id': event_id},
                        'intent': 'calendar',
                        'convo_id': str(convo.id),
                        'convo_title': convo.title,
                        'user_message_text': user_input,
                    })
                
                # Check if this is a cancellation
                elif confirmation_data.get('action') == 'cancel':
                    summary = confirmation_data.get('summary', 'the event')
                    
                    # Delete the draft message if ID is provided
                    if message_id:
                        try:
                            Message.objects.filter(id=message_id, conversation=convo).delete()
                        except Exception as e:
                            print(f"Failed to delete draft message {message_id}: {e}")
                    
                    # Use AI to generate a contextual cancellation message
                    cancellation_msg = f"Okay, I've cancelled the deletion of '{summary}'."
                    
                    # Persist cancellation message
                    Message.objects.create(
                        conversation=convo,
                        sender='agent',
                        text=cancellation_msg,
                        message_type='text'
                    )
                    
                    return JsonResponse({
                        'type': 'text',
                        'response': cancellation_msg,
                        'intent': 'calendar',
                        'convo_id': str(convo.id),
                        'convo_title': convo.title,
                        'user_message_text': user_input,
                    })
                
                # Check if this is an update confirmation
                elif confirmation_data.get('action') == 'update':
                    event_id = confirmation_data.get('event_id')
                    calendar_id = confirmation_data.get('calendar_id', 'primary')
                    original = confirmation_data.get('original', {})
                    updated = confirmation_data.get('updated', {})
                    
                    try:
                        # Build the updated event body for Google Calendar API
                        # Ensure start/end are in the correct format
                        start_data = updated.get('start')
                        if isinstance(start_data, str):
                            start_data = {'dateTime': start_data}
                        
                        end_data = updated.get('end')
                        if isinstance(end_data, str):
                            end_data = {'dateTime': end_data}
                        
                        # Ensure timeZone is present
                        if 'timeZone' not in start_data:
                            start_data['timeZone'] = client_tz_name or 'UTC'
                        if 'timeZone' not in end_data:
                            end_data['timeZone'] = client_tz_name or 'UTC'
                        
                        event_body = {
                            'summary': updated.get('summary'),
                            'start': start_data,
                            'end': end_data,
                        }
                        
                        # CRITICAL: Preserve recurrence rules for recurring events
                        # If we don't include recurrence when updating a recurring event,
                        # Google Calendar API will convert it to a single event!
                        try:
                            # Fetch the current event to get its recurrence rules
                            current_event = gcal.get_event(calendar_id, event_id)
                            if current_event.get('recurrence'):
                                # Preserve the recurrence rules
                                event_body['recurrence'] = current_event['recurrence']
                        except Exception as e:
                            print(f"Warning: Could not fetch event recurrence: {e}")
                        
                        # Update the event in Google Calendar
                        gcal.update_event(calendar_id, event_id, event_body)
                        
                        # Delete the draft message if ID is provided
                        if message_id:
                            try:
                                Message.objects.filter(id=message_id, conversation=convo).delete()
                            except Exception as e:
                                print(f"Failed to delete draft message {message_id}: {e}")
                        
                        # Get user's timezone
                        try:
                            from zoneinfo import ZoneInfo
                            user_tz = ZoneInfo(client_tz_name or getattr(settings, 'TIME_ZONE', 'UTC') or 'UTC')
                        except Exception:
                            from django.utils.timezone import get_current_timezone
                            user_tz = get_current_timezone()
                        
                        # Helper to format for display
                        def _fmt_time_iso(iso_str):
                            try:
                                dt_utc = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
                                dt_local = dt_utc.astimezone(user_tz)
                                t = dt_local.strftime('%I:%M %p')
                                return t.lstrip('0').replace('AM', 'am').replace('PM', 'pm')
                            except: return iso_str
                        
                        def _fmt_date_iso(iso_str_or_dict):
                            try:
                                # Handle both string and dict formats
                                if isinstance(iso_str_or_dict, dict):
                                    iso_str = iso_str_or_dict.get('dateTime') or iso_str_or_dict.get('date')
                                else:
                                    iso_str = iso_str_or_dict
                                
                                if not iso_str:
                                    return ''
                                
                                dt_utc = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
                                dt_local = dt_utc.astimezone(user_tz)
                                return dt_local.strftime('%A, %B %d').replace(' 0', ' ')
                            except: return str(iso_str_or_dict)
                        
                        # Generate success message highlighting what changed
                        original_summary = original.get('summary', '')
                        updated_summary = updated.get('summary', '')
                        
                        changes = []
                        if original_summary != updated_summary:
                            changes.append(f"title to '{updated_summary}'")
                        
                        original_start = original.get('start', {})
                        updated_start = updated.get('start', {})
                        if original_start != updated_start:
                            if updated_start.get('dateTime'):
                                new_time = _fmt_time_iso(updated_start['dateTime'])
                                new_date = _fmt_date_iso(updated_start)
                                changes.append(f"time to {new_time} on {new_date}")
                            elif updated_start.get('date'):
                                new_date = _fmt_date_iso(updated_start)
                                changes.append(f"date to {new_date}")
                        
                        if changes:
                            change_desc = " and ".join(changes)
                            success_msg = f"✓ Updated '{original_summary}' — changed {change_desc}."
                        else:
                            success_msg = f"✓ Updated '{original_summary}'."
                        
                        # Persist success message
                        Message.objects.create(
                            conversation=convo,
                            sender='agent',
                            text=success_msg,
                            message_type='event_updated',
                            content={'event_id': event_id, 'original': original, 'updated': updated}
                        )
                        
                        return JsonResponse({
                            'type': 'event_updated',
                            'response': success_msg,
                            'content': {'event_id': event_id, 'original': original, 'updated': updated},
                            'intent': 'calendar',
                            'convo_id': str(convo.id),
                            'convo_title': convo.title,
                            'user_message_text': user_input,
                        })
                    
                    except Exception as e:
                        print(f"Error updating event: {e}")
                        error_msg = f"Sorry, I failed to update the event: {e}"
                        
                        Message.objects.create(
                            conversation=convo,
                            sender='agent',
                            text=error_msg,
                            message_type='text'
                        )
                        
                        return JsonResponse({
                            'type': 'text',
                            'response': error_msg,
                            'content': {},
                            'intent': 'calendar',
                            'convo_id': str(convo.id)
                        })

                # Ensure start/end are in the correct format (dict with dateTime)
                start_data = confirmation_data.get('start')
                if isinstance(start_data, str):
                    start_data = {'dateTime': start_data}
                
                end_data = confirmation_data.get('end')
                if isinstance(end_data, str):
                    end_data = {'dateTime': end_data}

                # Sanitize the event body to remove extra fields like 'conflicts' or 'agent_message'
                # Ensure timeZone is present (required for recurring events)
                if 'timeZone' not in start_data:
                    start_data['timeZone'] = client_tz_name or 'UTC'
                if 'timeZone' not in end_data:
                    end_data['timeZone'] = client_tz_name or 'UTC'

                event_body = {
                    'summary': confirmation_data.get('summary'),
                    'start': start_data,
                    'end': end_data,
                    'attendees': confirmation_data.get('attendees', []),
                }
                # Add description or location if they exist in confirmation_data
                if 'description' in confirmation_data:
                    event_body['description'] = confirmation_data['description']
                if 'location' in confirmation_data:
                    event_body['location'] = confirmation_data['location']
                
                # Add recurrence if present
                if confirmation_data.get('recurrence'):
                    # Google Calendar API expects recurrence as a list of strings
                    recurrence_val = confirmation_data['recurrence']
                    
                    # Sanitize UNTIL date in RRULE (remove hyphens if present)
                    # Example: UNTIL=2026-02-29 -> UNTIL=20260229
                    if isinstance(recurrence_val, str):
                        if 'UNTIL=' in recurrence_val and '-' in recurrence_val.split('UNTIL=')[1]:
                            import re
                            recurrence_val = re.sub(r'(UNTIL=)(\d{4})-(\d{2})-(\d{2})', r'\1\2\3\4', recurrence_val)
                        event_body['recurrence'] = [recurrence_val]
                    elif isinstance(recurrence_val, list):
                        # Sanitize each rule in the list
                        sanitized_rules = []
                        for rule in recurrence_val:
                            if 'UNTIL=' in rule and '-' in rule.split('UNTIL=')[1]:
                                import re
                                rule = re.sub(r'(UNTIL=)(\d{4})-(\d{2})-(\d{2})', r'\1\2\3\4', rule)
                            sanitized_rules.append(rule)
                        event_body['recurrence'] = sanitized_rules

                ev = gcal.create_event('primary', event_body)
                
                # Parse the returned event to get the actual link and ID
                start_dt_iso = ev.get('start', {}).get('dateTime') or ev.get('start', {}).get('date')
                end_dt_iso = ev.get('end', {}).get('dateTime') or ev.get('end', {}).get('date')
                summary = ev.get('summary')
                
                # Get user's timezone
                try:
                    from zoneinfo import ZoneInfo
                    user_tz = ZoneInfo(client_tz_name or getattr(settings, 'TIME_ZONE', 'UTC') or 'UTC')
                except Exception:
                    from django.utils.timezone import get_current_timezone
                    user_tz = get_current_timezone()
                
                # Helper to format for display (convert from UTC to user's timezone)
                def _fmt_time_iso(iso_str):
                    try:
                        # Parse as UTC datetime
                        dt_utc = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
                        # Convert to user's timezone
                        dt_local = dt_utc.astimezone(user_tz)
                        t = dt_local.strftime('%I:%M %p')
                        return t.lstrip('0').replace('AM', 'am').replace('PM', 'pm')
                    except: return iso_str
                
                def _fmt_date_iso(iso_str):
                    try:
                        # Parse as UTC datetime
                        dt_utc = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
                        # Convert to user's timezone
                        dt_local = dt_utc.astimezone(user_tz)
                        return dt_local.strftime('%A, %B %d')
                    except: return iso_str

                # Generate AI success message
                recurrence_info = ""
                if event_body.get('recurrence'):
                     recurrence_info = f"Recurrence: {event_body['recurrence'][0]}"

                success_prompt = (
                    f"You just successfully created a calendar event. "
                    f"Event: '{summary}'"
                    f"Date: {_fmt_date_iso(start_dt_iso)}"
                    f"Time: {_fmt_time_iso(start_dt_iso)} to {_fmt_time_iso(end_dt_iso)}"
                    f"{recurrence_info}"
                    f"Write a brief, friendly confirmation message (1 sentence) letting the user know the event was created."
                    f"If it is recurring, mention the recurrence pattern naturally (e.g. 'every Monday')."
                )
                try:
                    agent_response_text = ai_agent._get_claude_chat_response(
                        [{"role": "user", "content": success_prompt}],
                        system_prompt="You are a helpful calendar assistant. Be concise and friendly.",
                        temperature=0.7,
                        max_tokens=100
                    )
                except Exception as e:
                    print(f"Failed to generate AI success message: {e}")
                    agent_response_text = (
                        f"I created '{summary}' on "
                        f"{_fmt_date_iso(start_dt_iso)} from {_fmt_time_iso(start_dt_iso)} to {_fmt_time_iso(end_dt_iso)}."
                    )
                
                # Persist the success message as structured event card
                event_success_content = {
                    'event_title': summary,
                    'event_link': ev.get('htmlLink'),
                    'event_id': ev.get('id'),
                }
                
                Message.objects.create(
                    conversation=convo,
                    sender='agent',
                    text=agent_response_text,
                    message_type='event_success',
                    content=event_success_content,
                )

                return JsonResponse({
                    'type': 'event_success',
                    'response': agent_response_text,
                    'content': event_success_content,
                    'intent': 'calendar',
                    'convo_id': str(convo.id),
                    'convo_title': convo.title,
                    'user_message_text': user_input,
                    'is_first_actual_message': is_first_actual_message,
                })

            except Exception as e:
                print(f"Error creating confirmed event: {e}")
                error_msg = f"Sorry, I failed to create the event: {e}"
                
                # Check for specific Google API errors
                if "Invalid recurrence rule" in str(e):
                    error_msg = "Sorry, the recurrence pattern was invalid. Please try again with a simpler repetition (e.g., 'every Monday')."

                Message.objects.create(
                    conversation=convo,
                    sender='agent',
                    text=error_msg,
                    message_type='text'
                )
                return JsonResponse({
                    'type': 'text',
                    'response': error_msg,
                    'content': {},
                    'intent': 'calendar',
                    'convo_id': str(convo.id)
                })

        result = ai_agent.handle(user_input, conversation=convo)

        agent_response_text = result.get("response") # Assuming 'response' key for text
        response_type = result.get("type", "text") # Get the type, default to text
        response_content = result.get("content", {}) # Get content for calendar actions
        intent = result.get("intent", "general") # Extract intent from result, default to general
        if response_type == 'needs_connection':
            next_url = reverse('home_page:assistant', args=[convo.id])
            connect_url = reverse('home_page:connect_google') + f'?next={next_url}'
            connect_url_to_add = connect_url
            email_to_add = request.user.email
        else:
            connect_url_to_add = None
            email_to_add = None

        if agent_response_text and str(agent_response_text).strip():
            # Persist plain text replies from the agent
            if response_type == 'text':
                Message.objects.create(
                    conversation=convo,
                    sender='agent',
                    text=agent_response_text,
                    message_type='text',
                    content=None,
                )

        # Generate title for first message
        if is_first_actual_message and convo.title == "New Chat": # Check if title is default "New Chat"
             # Use the AIAgent to generate the title
             title_result = ai_agent.handle(f"Generate a very short and concise title (max 5 words) for a chat based on the user message: '{user_input}'. Only provide the title text.", is_title_generation=True) # <--- Use the agent for title generation
             new_title = title_result.get("response") # Assuming title generation returns type 'text' and key 'response'

             if new_title:
                 new_title = new_title.strip().strip('"').strip("'")
                 if new_title:
                     convo.title = new_title[:120]
                     convo.save()
             # If title generation fails or is empty, keep the default or use user input snippet
             if convo.title == "New Chat":
                 convo.title = user_input[:40] + "..." if len(user_input) > 40 else user_input or "New Chat"
                 convo.save()


        # Prepare the JSON response for the frontend
        response_data = {
            'type': response_type, # Include the response type
            'response': agent_response_text, # Main text response
            'content': response_content, # Additional content for calendar actions etc.
            'intent': intent,  # Add this
            'convo_id': str(convo.id),
            'convo_title': convo.title,
            'user_message_text': user_input,
            # Don't send the agent message text back here, the frontend JS
            # will display based on 'type' and 'response'/'content'
            # 'agent_message_text': agent_response_text, # Remove this
            'is_first_actual_message': is_first_actual_message,
        }

        if response_type == 'needs_connection' and connect_url_to_add:
            response_data['content']['content_url'] = connect_url_to_add
            response_data['content']['email'] = email_to_add

        # If it's a calendar action request needing connection, add the connect URL
        if response_type == 'calendar_action_request' and response_content.get('needs_connection'):
             # You need a way to get the Google auth connect URL here
             # This might involve django-allauth's flow or a custom URL
             # Example (conceptual):
             # from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
             # from allauth.socialaccount.providers.oauth2.views import OAuth2LoginView
             # connect_url = reverse('google_connect') # Assuming you have a URL named 'google_connect' for the connect flow
             # response_data['content']['connect_url'] = connect_url

             # A simpler way for now is to hardcode or generate the connect URL here if possible
             # Based on logs, it looks like the connect URL pattern is /accounts/google/login/?process=connect&next=/agent/assistant/convo_id/
             # Need the current conversation ID in the 'next' parameter
             next_url = reverse('home_page:assistant', args=[convo.id]) # URL after successful connection
             connect_url = reverse('google_login') + f'?process=connect&next={next_url}' # Use reverse for the base login URL
             response_data['content']['connect_url'] = connect_url
            # Tell frontend who to display in the button
             response_data['content']['email'] = request.user.email


        # After OAuth redirect with ?resume=true, do not short-circuit. Allow normal
        # handling below so that the prior user message is processed.

        if response_type == 'calendar_action_request' and AIAgent(request.user).is_google_connected():
            try:
                action  = response_content['action']
                params  = response_content['params']

                # Ensure a token row exists; if not, ask user to reconnect to issue tokens
                if not SocialToken.objects.filter(account__user=request.user, account__provider='google').exists():
                    response_data.update({
                        'type': 'needs_connection',
                        'response': None,
                        'content': {
                            'message_for_user': 'Please connect your Google account to continue.',
                            'email': request.user.email,
                            'content_url': reverse('home_page:connect_google') + f"?next={reverse('home_page:assistant', args=[convo.id])}",
                            'needs_connection': True
                        }
                    })
                    return JsonResponse(response_data)

                gcal    = GoogleCalendarService(request.user)

                if action == 'find_free_slots':
                    # Normalize AI params to expected API
                    norm = dict(params or {})
                    # Map synonyms
                    if 'date' in norm and 'start_date' not in norm and 'start' not in norm:
                        norm['start_date'] = norm['date']
                    if 'date' in norm and 'end_date' not in norm and 'end' not in norm:
                        norm['end_date'] = norm['date']
                    if 'start' in norm and 'start_date' not in norm:
                        norm['start_date'] = norm['start']
                    if 'end' in norm and 'end_date' not in norm:
                        norm['end_date'] = norm['end']

                    start_date = norm.get('start_date')
                    end_date   = norm.get('end_date')
                    duration   = norm.get('duration', 60)
                    attendees  = norm.get('attendees')

                    # Coerce ISO datetimes into date-only strings if needed
                    def _date_only(val):
                        if isinstance(val, str) and 'T' in val:
                            return val.split('T', 1)[0]
                        return val

                    start_date = _date_only(start_date)
                    end_date   = _date_only(end_date)

                    # If no explicit ISO date provided, infer from user's text like "Thursday" or "next Thursday"
                    if not start_date and not end_date:
                        inferred_date = extract_date_from_text(user_input)
                        if inferred_date:
                            # Normalize to YYYY-MM-DD
                            inferred_iso = inferred_date.isoformat()
                            start_date = inferred_iso
                            end_date = inferred_iso
                        else:
                            # Cannot proceed – ask for a date/range and exit this action
                            response_type = 'text'
                            agent_response_text = (
                                "Please share a date (e.g. 2025-10-23) or a start and end date so I can check availability."
                            )
                    if start_date or end_date:
                        # If only one provided, assume single-day window
                        if start_date and not end_date:
                            end_date = start_date
                        if end_date and not start_date:
                            start_date = end_date

                        try:
                            busy_ranges = gcal.find_free_slots(
                                start_date=start_date,
                                end_date=end_date,
                                duration=duration,
                                attendees=attendees,
                            )
                            # Simple human summary
                            summary = (
                                "Your calendars are completely free between those dates!"
                                if not busy_ranges else
                                f"I found {len(busy_ranges)} busy periods.\n" +
                                "\n".join(f"- {b['start']} – {b['end']}" for b in busy_ranges[:3])
                            )
                            response_type = 'text'
                            agent_response_text = summary
                            # Persist the agent text so it survives reloads
                            try:
                                Message.objects.create(
                                    conversation=convo,
                                    sender='agent',
                                    text=agent_response_text,
                                    message_type='text',
                                    content=None,
                                )
                            except Exception:
                                pass
                        except Exception as e:
                            response_type = 'text'
                            agent_response_text = f"Sorry, I couldn't check availability: {e}"
                            # Always persist error messages so they survive reloads
                            Message.objects.create(
                                conversation=convo,
                                sender='agent',
                                text=agent_response_text,
                                message_type='text',
                                content=None,
                            )

                elif action == 'create_event':
                    # Build a proper Google Calendar event body from AI params
                    norm = dict(params or {})
                    # Normalize common synonym keys from the AI output
                    if 'start_time' in norm and 'start' not in norm:
                        norm['start'] = norm['start_time']
                    if 'end_time' in norm and 'end' not in norm:
                        norm['end'] = norm['end_time']
                    tz_str = (client_tz_name or getattr(settings, 'TIME_ZONE', 'UTC') or 'UTC')

                    # Helper: parse common ISO-ish formats and simple natural language
                    try:
                        # Prefer the client timezone for localization if provided
                        from zoneinfo import ZoneInfo
                        client_tz = ZoneInfo(tz_str)
                        print(f"✅ Using client timezone: {tz_str}")
                    except Exception as e:
                        client_tz = None
                        print(f"⚠️ Failed to parse client timezone '{tz_str}', falling back to Django default: {e}")
                    import re

                    def parse_dt(val: str):
                        if not val:
                            return None
                        s = str(val).strip()
                        # Normalize space separator to 'T'
                        s = s.replace(' ', 'T')
                        # Support trailing 'Z'
                        if s.endswith('Z'):
                            try:
                                return datetime.fromisoformat(s.replace('Z', '+00:00'))
                            except Exception:
                                pass
                        # Add seconds if missing (e.g. 2025-10-23T09:00)
                        m = re.match(r'^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?:([+-]\d{2}:\d{2})?)$', s)
                        if m:
                            s2 = f"{m.group(1)}T{m.group(2)}:00{m.group(3) or ''}"
                            try:
                                return datetime.fromisoformat(s2)
                            except Exception:
                                pass
                        # Plain date (all-day)
                        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
                            try:
                                return datetime.fromisoformat(s + 'T00:00:00')
                            except Exception:
                                return None
                        try:
                            return datetime.fromisoformat(s)
                        except Exception:
                            return None

                    def parse_duration(val):
                        """Parse duration strings like '2 hours', '90 minutes', '1.5 hours', '3:00', or plain numbers. Returns minutes as int."""
                        if not val:
                            return None
                        s = str(val).strip().lower()
                        # Try plain number first
                        try:
                            return int(float(s))
                        except ValueError:
                            pass
                        # Handle "H:MM" format (e.g., "3:00" means 3 hours, "2:30" means 2.5 hours)
                        m = re.match(r"^(\d+):(\d{2})$", s)
                        if m:
                            hours = int(m.group(1))
                            minutes = int(m.group(2))
                            return hours * 60 + minutes
                        # Parse "X hours", "X minutes", "X mins", "X hr", "X h"
                        m = re.match(r"^(\d+(?:\.\d+)?)\s*(hour|hours|hr|h|minute|minutes|mins|min|m)s?$", s)
                        if m:
                            num = float(m.group(1))
                            unit = m.group(2)
                            if unit in ('hour', 'hours', 'hr', 'h'):
                                return int(num * 60)
                            else:  # minutes
                                return int(num)
                        return None

                    def parse_time_only(val: str):
                        """Parse simple time-of-day like '9am', '9:30 am', '12pm', 'noon', 'midnight'. Returns (hour, minute) or None."""
                        if not val:
                            return None
                        s = str(val).strip().lower()
                        if s in ("noon",):
                            return (12, 0)
                        if s in ("midnight",):
                            return (0, 0)
                        m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", s)
                        if not m:
                            return None
                        hour = int(m.group(1))
                        minute = int(m.group(2) or 0)
                        meridiem = m.group(3)
                        if meridiem:
                            if hour == 12:
                                hour = 0 if meridiem == 'am' else 12
                            elif meridiem == 'pm':
                                hour += 12
                        # 24-hour times like '14:00'
                        if not meridiem and hour > 23:
                            return None
                        return (hour, minute)

                    def resolve_date(val: str):
                        """Resolve a date string like '2025-10-23', 'today', 'tomorrow', 'thursday', 'next thursday' to a date object."""
                        if not val:
                            return None
                        s = str(val).strip().lower()
                        # ISO date
                        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                            try:
                                return datetime.fromisoformat(s + 'T00:00:00').date()
                            except Exception:
                                return None
                        # Use client timezone if available; otherwise Django's current timezone
                        tz = client_tz or get_current_timezone()
                        today = datetime.now(tz).date()
                        if s == 'today':
                            return today
                        if s == 'tomorrow':
                            return today + timedelta(days=1)
                        # Weekday names
                        weekdays = {
                            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
                            'friday': 4, 'saturday': 5, 'sunday': 6
                        }
                        prefix_next = False
                        parts = s.split()
                        if len(parts) == 2 and parts[0] == 'next' and parts[1] in weekdays:
                            prefix_next = True
                            target_idx = weekdays[parts[1]]
                        elif s in weekdays:
                            target_idx = weekdays[s]
                        else:
                            return None
                        delta = (target_idx - today.weekday()) % 7
                        if delta == 0 and prefix_next:
                            delta = 7
                        if delta < 0:
                            delta += 7
                        return today + timedelta(days=delta)

                    def extract_date_from_text(text: str):
                        """Pull a simple date reference from raw user text (today/tomorrow/weekday/next weekday)."""
                        if not text:
                            return None
                        s = str(text).lower()
                        # Prefer explicit tokens
                        for token in ["today", "tomorrow"]:
                            if token in s:
                                return resolve_date(token)
                        # next <weekday> or <weekday>
                        import re as _re
                        m = _re.search(r"\b(next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", s)
                        if m:
                            token = (m.group(0) or '').strip()
                            return resolve_date(token)
                        # Fallback: explicit ISO date
                        m = _re.search(r"\b\d{4}-\d{2}-\d{2}\b", s)
                        if m:
                            return resolve_date(m.group(0))
                        return None

                    # Determine start/end
                    date_str  = norm.get('date') or norm.get('start_date')
                    # Support phrasing like "by 8am" → treat as an end time
                    start_str = norm.get('start') or norm.get('start_time') or norm.get('date')
                    end_str   = norm.get('end')
                    duration  = norm.get('duration')
                    summary   = norm.get('summary') or 'Meeting'
                    attendees = norm.get('attendees') or []
                    
                    # Debug: Log what AI extracted
                    print(f"AI EXTRACTED: date='{date_str}', start='{start_str}', end='{end_str}', duration='{duration}', summary='{summary}'")

                    start_dt = parse_dt(start_str)
                    end_dt   = parse_dt(end_str) if end_str else None

                    # If the user says "by <time>" and no explicit end provided,
                    # infer end time and compute start from duration if available later.
                    text_lc = (user_input or '').lower()
                      # Pattern 1: "by X to Y" (common phrasing that means "from X to Y")
                    import re as _re
                    by_to_match = _re.search(
                        r"\bby\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+to\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", 
                        text_lc
                    )
                    if by_to_match:
                        start_str = by_to_match.group(1)
                        end_str = by_to_match.group(2)
                        start_dt = None
                        end_dt = None
                        print(f"🔍 PATTERN: 'by X to Y' → start={start_str}, end={end_str}")
                    
                    # Pattern 2: "from X to Y" (explicit range)
                    elif (' from ' in text_lc) and (' to ' in text_lc):
                        from_to = _re.search(
                            r"\bfrom\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+to\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", 
                            text_lc
                        )
                        if from_to:
                            start_str = from_to.group(1)
                            end_str = from_to.group(2)
                            start_dt = None
                            end_dt = None
                            print(f"🔍 PATTERN: 'from X to Y' → start={start_str}, end={end_str}")
                    
                    # Pattern 3: "X to Y" or "X-Y" (simple range)
                    elif not by_to_match:
                        simple_range = _re.search(
                            r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+(?:to|-)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", 
                            text_lc
                        )
                        if simple_range:
                            start_str = simple_range.group(1)
                            end_str = simple_range.group(2)
                            start_dt = None
                            end_dt = None
                            print(f"🔍 PATTERN: 'X to Y' → start={start_str}, end={end_str}")
                    
                    # Pattern 4: "by X" - treat as start time if duration is specified, otherwise as deadline
                    # This should only trigger if no range pattern was found
                    if not (by_to_match or (start_str and end_str)):
                        by_alone = _re.search(r"\bby\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", text_lc)
                        if by_alone:
                            # Check if "to" appears within 30 chars after "by" to avoid false positives
                            by_end = by_alone.end()
                            has_to_after = 'to' in text_lc[by_end:by_end+30]
                            if not has_to_after:
                                # If duration is explicitly mentioned, treat "by X" as start time
                                # Common phrases: "last for X", "for X hours", "X hour", etc.
                                has_duration = bool(duration) or any(word in text_lc for word in ['last for', 'lasting', 'duration'])
                                if has_duration:
                                    start_str = by_alone.group(1)
                                    start_dt = None
                                    print(f"🔍 PATTERN: 'by X' with duration → start={start_str}")
                                else:
                                    end_str = by_alone.group(1)
                                    end_dt = None
                                    print(f"🔍 PATTERN: 'by X' (deadline) → end={end_str}")
                    
                    # Pattern 5: "at X" for start time
                    if (' at ' in text_lc) and not start_str:
                        at_match = _re.search(r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", text_lc)
                        if at_match:
                            start_str = at_match.group(1)
                            start_dt = None
                            print(f"🔍 PATTERN: 'at X' → start={start_str}")


                    # If times are given without date, merge with the most reliable date.
                    # Prefer the user's natural-language date (e.g., "Friday") over any absolute
                    # date guessed by the AI to avoid stale/past years like 2023.
                    # Additionally, if AI provided full datetimes but the user mentioned an explicit
                    # date in natural language, snap those datetimes to that date to avoid past years.
                    date_from_text = extract_date_from_text(user_input)
                    ai_date_only   = resolve_date(date_str) if date_str else None
                    date_only      = date_from_text or ai_date_only
                    if date_only:
                        # Use the client's timezone when creating datetime objects
                        # so that "9am" means "9am in the user's local time", not UTC
                        user_tz = client_tz or get_current_timezone()
                        
                        if not start_dt and start_str:
                            hm = parse_time_only(start_str)
                            if hm:
                                # Create timezone-aware datetime in user's timezone
                                naive_dt = datetime.combine(date_only, datetime.min.time()).replace(hour=hm[0], minute=hm[1])
                                start_dt = make_aware(naive_dt, user_tz)
                        if end_str:
                            hm = parse_time_only(end_str)
                            if hm:
                                # Create timezone-aware datetime in user's timezone
                                naive_dt = datetime.combine(date_only, datetime.min.time()).replace(hour=hm[0], minute=hm[1])
                                end_dt = make_aware(naive_dt, user_tz)
                        # If AI provided full datetimes but with an incorrect/past date, snap to the requested date
                        if start_dt and (start_dt.date() != date_only):
                            # Preserve timezone when snapping to new date
                            original_tz = start_dt.tzinfo or user_tz
                            naive_dt = datetime.combine(date_only, start_dt.time())
                            start_dt = make_aware(naive_dt, original_tz)
                        if end_dt and (end_dt.date() != date_only):
                            # Preserve timezone when snapping to new date
                            original_tz = end_dt.tzinfo or user_tz
                            naive_dt = datetime.combine(date_only, end_dt.time())
                            end_dt = make_aware(naive_dt, original_tz)

                    # Compute end from duration when needed
                    if start_dt and not end_dt:
                        minutes = parse_duration(duration) if duration else 60
                        if minutes is None:
                            minutes = 60
                        end_dt = start_dt + timedelta(minutes=minutes)

                    # Compute start from end and duration (e.g., "by 9am")
                    if end_dt and not start_dt:
                        minutes = parse_duration(duration) if duration else 60
                        if minutes is None:
                            minutes = 60
                        start_dt = end_dt - timedelta(minutes=minutes)

                    # Final guard: ensure end is strictly after start
                    if start_dt and end_dt and end_dt <= start_dt:
                        minutes = parse_duration(duration) if duration else 60
                        if minutes is None:
                            minutes = 60
                        end_dt = start_dt + timedelta(minutes=minutes)

                    if not start_dt or not end_dt:
                        response_type = 'text'
                        has_date = bool(date_only)
                        agent_response_text = (
                            "I need a date plus a start time and either an end time or a duration."
                            if not has_date else
                            "I need a concrete start time and duration (or end time) to create the event. Please provide a start time and either an end time or a duration."
                        )
                        Message.objects.create(
                            conversation=convo,
                            sender='agent',
                            text=agent_response_text,
                            message_type='text',
                            content=None,
                        )
                    else:
                        # Normalize datetimes into the client's timezone
                        tz = client_tz or get_current_timezone()
                        print(tz)
                        if start_dt.tzinfo is None:
                            start_dt = make_aware(start_dt, tz)
                        else:
                            start_dt = start_dt.astimezone(tz)
                        if end_dt.tzinfo is None:
                            end_dt = make_aware(end_dt, tz)
                        else:
                            end_dt = end_dt.astimezone(tz)

                        # Log the resolved datetimes for diagnostics
                        try:
                            logger.info(
                                "Resolved event datetimes (local tz): start=%s, end=%s, title=%s, tz=%s",
                                start_dt.isoformat(), end_dt.isoformat(), summary, tz_str
                            )
                        except Exception:
                            pass

                        event_body = {
                            'summary': summary,
                            'start': {
                                'dateTime': start_dt.isoformat(),
                                'timeZone': tz_str,
                            },
                            'end': {
                                'dateTime': end_dt.isoformat(),
                                'timeZone': tz_str,
                            },
                        }
                        # Normalize attendees to list of {email}
                        if isinstance(attendees, (list, tuple)) and attendees:
                            event_body['attendees'] = [
                                {'email': a} for a in attendees if isinstance(a, str) and '@' in a
                            ]

                        # PROACTIVE CONFLICT DETECTION
                        # Calculate duration
                        duration_minutes = int((end_dt - start_dt).total_seconds() / 60)
                        
                        # Check for actual conflicts (not just busy ranges)
                        conflicts = check_conflicts_proactively(start_dt, end_dt, gcal)
                        has_conflict = len(conflicts) > 0
                        
                        # If conflict detected, find alternative times
                        alternatives = []
                        if has_conflict:
                            alternatives = find_alternative_times(start_dt, duration_minutes, gcal)
                        
                        # Generate AI message based on conflict status
                        if has_conflict and alternatives:
                            # Generate suggestion with alternative
                            conflict_names = ", ".join([c['summary'] for c in conflicts[:2]])
                            if len(conflicts) > 2:
                                conflict_names += f" and {len(conflicts)-2} more"
                            
                            # Format alt times for AI
                            alt_times_str = ", ".join([
                                datetime.fromisoformat(alt['start'].replace('Z', '+00:00')).strftime('%I:%M %p').lstrip('0')
                                for alt in alternatives[:2]
                            ])
                            
                            draft_prompt = (
                                f"User wants to schedule '{summary}' at {start_dt.strftime('%I:%M %p')}. "
                                f"However, they already have '{conflict_names}' at that time. "
                                f"Suggest they use {alt_times_str} instead (they're free then). "
                                f"Be friendly and concise (1-2 sentences)."
                            )
                        elif has_conflict:
                            # Conflict but no alternatives found
                            conflict_names = ", ".join([c['summary'] for c in conflicts[:2]])
                            draft_prompt = (
                                f"User wants to schedule '{summary}' but they already have '{conflict_names}' at that time. "
                                f"Let them know about the conflict and suggest trying a different time. "
                                f"Be friendly and concise (1 sentence)."
                            )
                        else:
                            # No conflict
                            draft_prompt = (
                                f"You drafted '{summary}' for the user to review. "
                                f"They are free at this time. "
                                f"Write a brief confirmation (1 sentence)."
                            )
                        
                        try:
                            agent_message = ai_agent._get_claude_chat_response(
                                [{"role": "user", "content": draft_prompt}],
                                system_prompt="You are a helpful calendar assistant. Be concise and friendly.",
                                temperature=0.7,
                                max_tokens=100
                            )
                        except Exception as e:
                            print(f"Failed to generate AI draft message: {e}")
                            conflict_msg = "You are free at this time." if not has_conflict else "⚠️ You have a conflict at this time."
                            agent_message = f"I've drafted this meeting. {conflict_msg}"
                        
                        response_type = 'event_confirmation_request'
                        response_content = {
                            'summary': summary,
                            'start': event_body['start'],
                            'end': event_body['end'],
                            'attendees': event_body.get('attendees', []),
                            'recurrence': norm.get('recurrence'),
                            'has_conflict': has_conflict,
                            'conflicts': conflicts if has_conflict else [],
                            'alternatives': alternatives if has_conflict else [],
                            'agent_message': agent_message
                        }
                        
                        agent_response_text = response_content['agent_message']
                        
                        # Persist the draft event preview as structured message
                        draft_msg = Message.objects.create(
                            conversation=convo,
                            sender='agent',
                            text=agent_response_text,
                            message_type='event_preview',
                            content=response_content,
                        )
                        
                        # Add the message ID to the response content so frontend can track it
                        response_content['message_id'] = draft_msg.id



                elif action == 'delete_event':
                    norm = dict(params or {})
                    summary_query = norm.get('summary')
                    date_str = norm.get('date') or norm.get('start_date')
                    time_str = norm.get('start') or norm.get('start_time')
                    
                    try:
                        from zoneinfo import ZoneInfo
                        tz = ZoneInfo(client_tz_name or getattr(settings, 'TIME_ZONE', 'UTC') or 'UTC')
                    except Exception:
                        tz = get_current_timezone()
                    
                    delete_all = norm.get('delete_all')
                    start_date_str = norm.get('start_date')
                    end_date_str = norm.get('end_date')
                    
                    # Determine time range
                    range_start = None
                    range_end = None
                    
                    if start_date_str and end_date_str:
                        s_date = _parse_simple_date(start_date_str)
                        e_date = _parse_simple_date(end_date_str)
                        if s_date and e_date:
                            range_start = datetime.combine(s_date, datetime.min.time()).isoformat() + 'Z'
                            range_end = datetime.combine(e_date, datetime.max.time()).isoformat() + 'Z'
                            target_date = f"{s_date} to {e_date}" # For display
                    
                    if not range_start:
                        # Fallback to single day logic
                        target_date_obj = None
                        if date_str:
                             target_date_obj = _parse_simple_date(date_str)
                        
                        if not target_date_obj:
                             target_date_obj = datetime.now().date() # Fallback to today
                        
                        target_date = target_date_obj # For display
                        range_start = datetime.combine(target_date_obj, datetime.min.time()).isoformat() + 'Z'
                        range_end = datetime.combine(target_date_obj, datetime.max.time()).isoformat() + 'Z'

                    events = gcal.list_events(time_min=range_start, time_max=range_end)
                    
                    # Filter by summary (fuzzy match) unless delete_all is True
                    matches = []
                    if delete_all:
                        matches = events
                    else:
                        for event in events:
                            event_summary = event.get('summary', '')
                            if summary_query and summary_query.lower() in event_summary.lower():
                                matches.append(event)
                            elif not summary_query:
                                pass
                    
                    # If delete_all is True and we have matches, we need to confirm deletion of MULTIPLE events
                    # The existing logic handles len(matches) > 1 by asking "Which one?", but for delete_all we want to confirm "Delete all X?"
                    if delete_all and matches:
                        # Special handling for bulk deletion confirmation
                        response_type = 'event_deletion_confirmation' # Re-using this type might need adjustment or a new type
                        # Actually, let's use a text confirmation for now or adapt the card
                        # If we want to use the card, we need to pass a single event ID. 
                        # But we have multiple.
                        # Let's create a special "bulk_delete" action or just iterate?
                        # Iterating is risky without confirmation.
                        
                        # Let's use a text response with a "confirm" button if possible, or just text.
                        # For now, let's treat it as a special case where we ask for confirmation in text.
                        # OR, we can pick the first event to show in the card and say "and X others"?
                        
                        # Let's try to support bulk delete in the confirmation card?
                        # The card expects 'event_id'.
                        
                        # Simplest approach: If delete_all, we just proceed to delete? NO, dangerous.
                        # We need confirmation.
                        
                        # Let's construct a summary message.
                        agent_message = f"I found {len(matches)} events on {target_date}. Are you sure you want to delete ALL of them?"
                        
                        # We can't use the standard 'event_deletion_confirmation' card easily because it's tied to a single event ID for the 'delete' button action.
                        # We might need to modify the frontend/backend to support a list of IDs or a 'bulk_delete' flag.
                        
                        # For this iteration, let's just list them and ask for confirmation via text?
                        # But the user wants to click "Delete".
                        
                        # Hack: Pass a special event_id or use a new message type?
                        # Let's use 'event_deletion_confirmation' but with a special ID 'BULK' and store the list in the session or params?
                        # Or pass all IDs in the 'event_id' field separated by commas?
                        
                        all_ids = ",".join([e['id'] for e in matches])
                        response_content = {
                            'event_id': all_ids, # Pass all IDs
                            'summary': f"{len(matches)} events",
                            'start': matches[0]['start'], # Just show first one's time or range
                            'end': matches[-1]['end'],
                            'action': 'delete_bulk' # New action type for frontend/backend to handle?
                        }
                        # Wait, the frontend just calls 'delete_event' with the ID.
                        # If we pass comma-separated IDs, we need to update the 'delete_event' handler to split them.
                        
                        draft_msg = Message.objects.create(
                            conversation=convo,
                            sender='agent',
                            text=agent_message,
                            message_type='event_deletion_confirmation',
                            content=response_content,
                        )
                        response_content['message_id'] = draft_msg.id
                        
                        # Update the message content to include message_id
                        draft_msg.content = response_content
                        draft_msg.save()
                        
                        agent_response_text = agent_message
                        
                        # We need to ensure the 'confirm_action' handles this.
                        # Let's return here to avoid falling into the other logic.
                        return JsonResponse({
                            'type': response_type,
                            'response': agent_message,
                            'content': response_content,
                            'message_id': draft_msg.id
                        })


                    # If not delete_all mode, continue with existing logic for single/ambiguous matches
                            
                    # If time is provided, filter by time as well
                    if time_str and matches:
                        try:
                            # Try to parse time_str (e.g. "10am", "14:00")
                            # We'll use a simple heuristic or dateutil if available, but for now let's try basic parsing
                            # or rely on the fact that 'start' param from AI might be normalized to HH:MM
                            
                            # Normalize time_str to HH:MM if possible
                            filter_hour = None
                            filter_minute = None
                            
                            # Simple 12h/24h parsing
                            ts = time_str.lower().replace(' ', '')
                            import re
                            # Match 10am, 10:30pm, 14:00, 14
                            time_match = re.match(r'(\d{1,2})(?::(\d{2}))?([ap]m)?', ts)
                            if time_match:
                                h = int(time_match.group(1))
                                m = int(time_match.group(2) or 0)
                                ampm = time_match.group(3)
                                
                                if ampm:
                                    if ampm == 'pm' and h < 12:
                                        h += 12
                                    elif ampm == 'am' and h == 12:
                                        h = 0
                                
                                filter_hour = h
                                filter_minute = m
                                
                                # Filter matches
                                time_filtered = []
                                for evt in matches:
                                    # event['start'] is a dict with 'dateTime' or 'date'
                                    start_dt_str = evt.get('start', {}).get('dateTime')
                                    if start_dt_str:
                                        evt_dt = datetime.fromisoformat(start_dt_str.replace('Z', '+00:00'))
                                        # Convert to local time if needed, but for now let's just compare hours/minutes
                                        # Ideally we should convert both to the same timezone.
                                        # Assuming event times are in user's local time or we can extract hour/minute from the string directly
                                        # if it has offset.
                                        
                                        # Let's use the simple approach: check if the event starts around the requested time
                                        # We need to handle timezone conversion properly if possible.
                                        # But often 'dateTime' includes offset.
                                        
                                        # Better: Convert event time to local user time (client_tz)
                                        evt_dt_local = evt_dt.astimezone(tz)
                                        
                                        # fuzzy match: within 15 mins?
                                        if evt_dt_local.hour == filter_hour and abs(evt_dt_local.minute - filter_minute) < 15:
                                            time_filtered.append(evt)
                                
                                if time_filtered:
                                    matches = time_filtered
                        except Exception as e:
                            print(f"Error filtering by time: {e}")
                            pass
                    match_index = norm.get('match_index')
                    
                    # Sort matches by start time to ensure consistent ordering for "first", "second", etc.
                    matches.sort(key=lambda x: x.get('start', {}).get('dateTime') or x.get('start', {}).get('date') or '')

                    if match_index and isinstance(match_index, int) and matches:
                        idx = match_index - 1 # 1-based index from AI
                        if 0 <= idx < len(matches):
                            matches = [matches[idx]]

                    if len(matches) == 1:
                        event = matches[0]
                        # Generate confirmation
                        response_type = 'event_deletion_confirmation'
                        response_content = {
                            'event_id': event['id'],
                            'summary': event['summary'],
                            'start': event['start'],
                            'end': event['end'],
                            'action': 'delete'
                        }
                        agent_message = f"Are you sure you want to delete '{event['summary']}'?"
                        
                        # Persist draft
                        draft_msg = Message.objects.create(
                            conversation=convo,
                            sender='agent',
                            text=agent_message,
                            message_type='event_deletion_confirmation',
                            content=response_content,
                        )
                        response_content['message_id'] = draft_msg.id
                        agent_response_text = agent_message

                    elif len(matches) > 1:
                        response_type = 'text'
                        agent_response_text = f"I found multiple events matching '{summary_query}'. Which one would you like to delete?"
                        # Persist error
                        Message.objects.create(conversation=convo, sender='agent', text=agent_response_text, message_type='text')
                        
                    else:
                        response_type = 'text'
                        agent_response_text = f"I couldn't find any event matching '{summary_query}' on {target_date}."
                        # Persist error
                        Message.objects.create(conversation=convo, sender='agent', text=agent_response_text, message_type='text')


                elif action == 'update_event':
                    norm = dict(params or {})
                    summary_query = norm.get('summary')
                    date_str = norm.get('date') or norm.get('start_date')
                    time_str = norm.get('start') or norm.get('start_time')
                    updates = norm.get('updates', {})
                    
                    if not updates:
                        response_type = 'text'
                        agent_response_text = "I couldn't determine what you'd like to update. Please specify what changes you want to make."
                        Message.objects.create(conversation=convo, sender='agent', text=agent_response_text, message_type='text')
                    else:
                        try:
                            from zoneinfo import ZoneInfo
                            tz = ZoneInfo(client_tz_name or getattr(settings, 'TIME_ZONE', 'UTC') or 'UTC')
                        except Exception:
                            tz = get_current_timezone()
                        
                        # Determine search time range
                        range_start = None
                        range_end = None
                        target_date = None
                        
                        if date_str:
                            target_date_obj = _parse_simple_date(date_str)
                            if target_date_obj:
                                target_date = target_date_obj
                                range_start = datetime.combine(target_date_obj, datetime.min.time()).isoformat() + 'Z'
                                range_end = datetime.combine(target_date_obj, datetime.max.time()).isoformat() + 'Z'
                        
                        if not range_start:
                            # Default to searching a wider range (today and future events)
                            today = datetime.now(tz).date()
                            target_date = today
                            range_start = datetime.combine(today, datetime.min.time()).isoformat() + 'Z'
                            # Search up to 30 days ahead
                            range_end = datetime.combine(today + timedelta(days=30), datetime.max.time()).isoformat() + 'Z'
                        
                        events = gcal.list_events(time_min=range_start, time_max=range_end)
                        
                        # Filter by summary (fuzzy match)
                        matches = []
                        if summary_query:
                            for event in events:
                                event_summary = event.get('summary', '')
                                if summary_query.lower() in event_summary.lower():
                                    matches.append(event)
                        
                        # If time is provided, filter by time as well
                        if time_str and matches:
                            try:
                                # Parse time_str (e.g. "10am", "14:00")
                                filter_hour = None
                                filter_minute = None
                                
                                ts = time_str.lower().replace(' ', '')
                                import re
                                time_match = re.match(r'(\d{1,2})(?::(\d{2}))?([ap]m)?', ts)
                                if time_match:
                                    h = int(time_match.group(1))
                                    m = int(time_match.group(2) or 0)
                                    ampm = time_match.group(3)
                                    
                                    if ampm:
                                        if ampm == 'pm' and h < 12:
                                            h += 12
                                        elif ampm == 'am' and h == 12:
                                            h = 0
                                    
                                    filter_hour = h
                                    filter_minute = m
                                    
                                    # Filter matches
                                    time_filtered = []
                                    for evt in matches:
                                        start_dt_str = evt.get('start', {}).get('dateTime')
                                        if start_dt_str:
                                            evt_dt = datetime.fromisoformat(start_dt_str.replace('Z', '+00:00'))
                                            evt_dt_local = evt_dt.astimezone(tz)
                                            
                                            # fuzzy match: within 15 mins
                                            if evt_dt_local.hour == filter_hour and abs(evt_dt_local.minute - filter_minute) < 15:
                                                time_filtered.append(evt)
                                    
                                    if time_filtered:
                                        matches = time_filtered
                            except Exception as e:
                                print(f"Error filtering by time: {e}")
                                pass
                        
                        match_index = norm.get('match_index')
                        
                        # Sort matches by start time
                        matches.sort(key=lambda x: x.get('start', {}).get('dateTime') or x.get('start', {}).get('date') or '')
                        
                        if match_index and isinstance(match_index, int) and matches:
                            idx = match_index - 1
                            if 0 <= idx < len(matches):
                                matches = [matches[idx]]
                        
                        if len(matches) == 1:
                            event = matches[0]
                            event_id = event['id']
                            
                            # Check for series update intent
                            update_series = norm.get('update_series', False)
                            
                            # If user wants to update series and it's a recurring instance
                            if update_series and 'recurringEventId' in event:
                                try:
                                    # Fetch master event
                                    master_event = gcal.get_event('primary', event['recurringEventId'])
                                    if master_event:
                                        event = master_event
                                        event_id = master_event['id']
                                except Exception as e:
                                    print(f"Error fetching master event: {e}")

                            
                            # Parse the updates and build the updated event preview
                            # We'll show a confirmation card with before/after details
                            
                            # Get current event details
                            current_start = event.get('start', {})
                            current_end = event.get('end', {})
                            current_summary = event.get('summary', '')
                            
                            # Parse updates
                            updated_start = current_start.copy()
                            updated_end = current_end.copy()
                            updated_summary = current_summary
                            
                            # Handle date updates
                            if 'date' in updates:
                                new_date_obj = _parse_simple_date(updates['date'])
                                if new_date_obj:
                                    new_date_str = new_date_obj.isoformat()
                                    
                                    # If current event has dateTime, preserve time but change date
                                    if current_start.get('dateTime'):
                                        current_dt = datetime.fromisoformat(current_start['dateTime'].replace('Z', '+00:00'))
                                        new_dt = datetime.combine(new_date_obj, current_dt.time()).replace(tzinfo=current_dt.tzinfo)
                                        updated_start = {'dateTime': new_dt.isoformat()}
                                        
                                        if current_end.get('dateTime'):
                                            current_end_dt = datetime.fromisoformat(current_end['dateTime'].replace('Z', '+00:00'))
                                            duration = current_end_dt - current_dt
                                            new_end_dt = new_dt + duration
                                            updated_end = {'dateTime': new_end_dt.isoformat()}
                                    else:
                                        # All-day event
                                        updated_start = {'date': new_date_str}
                                        updated_end = {'date': (new_date_obj + timedelta(days=1)).isoformat()}
                            
                            # Handle time updates (start/end)
                            if 'start' in updates:
                                new_time_str = updates['start']
                                # Parse time (e.g., "15:00", "3pm")
                                try:
                                    # Try HH:MM format first
                                    if ':' in new_time_str:
                                        time_parts = new_time_str.split(':')
                                        new_hour = int(time_parts[0])
                                        new_minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                                    else:
                                        # Try 12h format
                                        ts = new_time_str.lower().replace(' ', '')
                                        import re
                                        time_match = re.match(r'(\d{1,2})(?::(\d{2}))?([ap]m)?', ts)
                                        if time_match:
                                            new_hour = int(time_match.group(1))
                                            new_minute = int(time_match.group(2) or 0)
                                            ampm = time_match.group(3)
                                            
                                            if ampm:
                                                if ampm == 'pm' and new_hour < 12:
                                                    new_hour += 12
                                                elif ampm == 'am' and new_hour == 12:
                                                    new_hour = 0
                                        else:
                                            raise ValueError("Invalid time format")
                                    
                                    # Get the date from current event or updated date
                                    if updated_start.get('dateTime'):
                                        base_dt = datetime.fromisoformat(updated_start['dateTime'].replace('Z', '+00:00'))
                                        new_start_dt = base_dt.replace(hour=new_hour, minute=new_minute)
                                        updated_start = {'dateTime': new_start_dt.isoformat()}
                                        
                                        # Preserve duration if end exists
                                        if current_end.get('dateTime'):
                                            current_start_dt = datetime.fromisoformat(current_start['dateTime'].replace('Z', '+00:00'))
                                            current_end_dt = datetime.fromisoformat(current_end['dateTime'].replace('Z', '+00:00'))
                                            duration = current_end_dt - current_start_dt
                                            new_end_dt = new_start_dt + duration
                                            updated_end = {'dateTime': new_end_dt.isoformat()}
                                    else:
                                        # Convert all-day to timed event
                                        # Use current date or updated date
                                        if current_start.get('date'):
                                            event_date = datetime.fromisoformat(current_start['date']).date()
                                        else:
                                            event_date = datetime.now(tz).date()
                                        
                                        new_start_dt = datetime.combine(event_date, datetime.min.time()).replace(
                                            hour=new_hour, minute=new_minute, tzinfo=tz
                                        )
                                        updated_start = {'dateTime': new_start_dt.isoformat()}
                                        # Default 1 hour duration
                                        updated_end = {'dateTime': (new_start_dt + timedelta(hours=1)).isoformat()}
                                except Exception as e:
                                    print(f"Error parsing new start time: {e}")
                            
                            if 'end' in updates:
                                new_time_str = updates['end']
                                try:
                                    # Parse end time
                                    if ':' in new_time_str:
                                        time_parts = new_time_str.split(':')
                                        new_hour = int(time_parts[0])
                                        new_minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                                    else:
                                        ts = new_time_str.lower().replace(' ', '')
                                        import re
                                        time_match = re.match(r'(\d{1,2})(?::(\d{2}))?([ap]m)?', ts)
                                        if time_match:
                                            new_hour = int(time_match.group(1))
                                            new_minute = int(time_match.group(2) or 0)
                                            ampm = time_match.group(3)
                                            
                                            if ampm:
                                                if ampm == 'pm' and new_hour < 12:
                                                    new_hour += 12
                                                elif ampm == 'am' and new_hour == 12:
                                                    new_hour = 0
                                        else:
                                            raise ValueError("Invalid time format")
                                    
                                    if updated_start.get('dateTime'):
                                        base_dt = datetime.fromisoformat(updated_start['dateTime'].replace('Z', '+00:00'))
                                        new_end_dt = base_dt.replace(hour=new_hour, minute=new_minute)
                                        updated_end = {'dateTime': new_end_dt.isoformat()}
                                except Exception as e:
                                    print(f"Error parsing new end time: {e}")
                            
                            # Handle title/summary update
                            if 'summary' in updates:
                                updated_summary = updates['summary']
                            
                            # Check for conflicts with new time slot
                            has_conflict = False
                            conflicts = []
                            
                            if updated_start.get('dateTime'):
                                # Check if new time conflicts with other events
                                try:
                                    new_start_dt = datetime.fromisoformat(updated_start['dateTime'].replace('Z', '+00:00'))
                                    new_end_dt = datetime.fromisoformat(updated_end['dateTime'].replace('Z', '+00:00')) if updated_end.get('dateTime') else new_start_dt + timedelta(hours=1)
                                    
                                    # Search for events in the new time window
                                    conflict_search_start = new_start_dt.isoformat()
                                    conflict_search_end = new_end_dt.isoformat()
                                    
                                    all_events = gcal.list_events(
                                        time_min=conflict_search_start,
                                        time_max=conflict_search_end
                                    )
                                    
                                    for evt in all_events:
                                        # Skip the event being updated
                                        if evt['id'] == event_id:
                                            continue
                                        
                                        evt_start = evt.get('start', {}).get('dateTime')
                                        evt_end = evt.get('end', {}).get('dateTime')
                                        
                                        if evt_start and evt_end:
                                            evt_start_dt = datetime.fromisoformat(evt_start.replace('Z', '+00:00'))
                                            evt_end_dt = datetime.fromisoformat(evt_end.replace('Z', '+00:00'))
                                            
                                            # Check for overlap
                                            if (new_start_dt < evt_end_dt and new_end_dt > evt_start_dt):
                                                has_conflict = True
                                                conflicts.append({
                                                    'summary': evt.get('summary', 'Untitled'),
                                                    'start': evt['start'],
                                                    'end': evt['end']
                                                })
                                except Exception as e:
                                    print(f"Error checking conflicts: {e}")
                            
                            # Generate confirmation message
                            response_type = 'event_update_confirmation'
                            response_content = {
                                'event_id': event_id,
                                'original': {
                                    'summary': current_summary,
                                    'start': current_start,
                                    'end': current_end
                                },
                                'updated': {
                                    'summary': updated_summary,
                                    'start': updated_start,
                                    'end': updated_end
                                },
                                'has_conflict': has_conflict,
                                'conflicts': conflicts if has_conflict else [],
                                'action': 'update'
                            }
                            
                            # Generate AI message about the update
                            changes = []
                            if updated_summary != current_summary:
                                changes.append(f"title to '{updated_summary}'")
                            if updated_start != current_start:
                                # Format time nicely
                                try:
                                    if updated_start.get('dateTime'):
                                        new_dt = datetime.fromisoformat(updated_start['dateTime'].replace('Z', '+00:00')).astimezone(tz)
                                        time_str = new_dt.strftime('%I:%M %p').lstrip('0')
                                        date_str = new_dt.strftime('%A, %B %d').replace(' 0', ' ')
                                        changes.append(f"time to {time_str} on {date_str}")
                                    elif updated_start.get('date'):
                                        date_obj = datetime.fromisoformat(updated_start['date']).date()
                                        changes.append(f"date to {date_obj.strftime('%A, %B %d').replace(' 0', ' ')}")
                                except:
                                    changes.append("time")
                            
                            if changes:
                                change_desc = " and ".join(changes)
                                if has_conflict:
                                    agent_message = f"⚠️ I found '{current_summary}' and can update the {change_desc}, but you have a conflict at that time. Do you want to proceed?"
                                else:
                                    agent_message = f"I found '{current_summary}'. Update the {change_desc}?"
                            else:
                                agent_message = f"I found '{current_summary}', but I'm not sure what changes you'd like to make."
                            
                            # Persist draft
                            draft_msg = Message.objects.create(
                                conversation=convo,
                                sender='agent',
                                text=agent_message,
                                message_type='event_update_confirmation',
                                content=response_content,
                            )
                            response_content['message_id'] = draft_msg.id
                            agent_response_text = agent_message
                        
                        elif len(matches) > 1:
                            # Check if user wants to update series
                            update_series = norm.get('update_series', False)
                            
                            # If user wants to update series and the events are recurring instances
                            if update_series and matches[0].get('recurringEventId'):
                                # Use the first match to get the master event
                                try:
                                    master_event = gcal.get_event('primary', matches[0]['recurringEventId'])
                                    if master_event:
                                        # Treat as single match with the master event
                                        matches = [master_event]
                                        # Continue to the single match handling below
                                        # We'll jump back to re-process with len(matches) == 1
                                        event = matches[0]
                                        event_id = event['id']
                                        
                                        # Get current event details
                                        current_start = event.get('start', {})
                                        current_end = event.get('end', {})
                                        current_summary = event.get('summary', '')
                                        
                                        # Parse updates (reusing logic from single match case)
                                        updated_start = current_start.copy()
                                        updated_end = current_end.copy()
                                        updated_summary = current_summary
                                        
                                        # Handle date updates
                                        if 'date' in updates:
                                            new_date_obj = _parse_simple_date(updates['date'])
                                            if new_date_obj:
                                                new_date_str = new_date_obj.isoformat()
                                                
                                                if current_start.get('dateTime'):
                                                    current_dt = datetime.fromisoformat(current_start['dateTime'].replace('Z', '+00:00'))
                                                    new_dt = datetime.combine(new_date_obj, current_dt.time()).replace(tzinfo=current_dt.tzinfo)
                                                    updated_start = {'dateTime': new_dt.isoformat()}
                                                    
                                                    if current_end.get('dateTime'):
                                                        current_end_dt = datetime.fromisoformat(current_end['dateTime'].replace('Z', '+00:00'))
                                                        duration = current_end_dt - current_dt
                                                        new_end_dt = new_dt + duration
                                                        updated_end = {'dateTime': new_end_dt.isoformat()}
                                                else:
                                                    updated_start = {'date': new_date_str}
                                                    updated_end = {'date': (new_date_obj + timedelta(days=1)).isoformat()}
                                        
                                        # Handle time updates (start/end)
                                        if 'start' in updates:
                                            new_time_str = updates['start']
                                            try:
                                                if ':' in new_time_str:
                                                    time_parts = new_time_str.split(':')
                                                    new_hour = int(time_parts[0])
                                                    new_minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                                                else:
                                                    ts = new_time_str.lower().replace(' ', '')
                                                    import re
                                                    time_match = re.match(r'(\d{1,2})(?::(\d{2}))?([ap]m)?', ts)
                                                    if time_match:
                                                        new_hour = int(time_match.group(1))
                                                        new_minute = int(time_match.group(2) or 0)
                                                        ampm = time_match.group(3)
                                                        
                                                        if ampm:
                                                            if ampm == 'pm' and new_hour < 12:
                                                                new_hour += 12
                                                            elif ampm == 'am' and new_hour == 12:
                                                                new_hour = 0
                                                    else:
                                                        raise ValueError("Invalid time format")
                                                
                                                if updated_start.get('dateTime'):
                                                    base_dt = datetime.fromisoformat(updated_start['dateTime'].replace('Z', '+00:00'))
                                                    new_start_dt = base_dt.replace(hour=new_hour, minute=new_minute)
                                                    updated_start = {'dateTime': new_start_dt.isoformat()}
                                                    
                                                    if current_end.get('dateTime'):
                                                        current_start_dt = datetime.fromisoformat(current_start['dateTime'].replace('Z', '+00:00'))
                                                        current_end_dt = datetime.fromisoformat(current_end['dateTime'].replace('Z', '+00:00'))
                                                        duration = current_end_dt - current_start_dt
                                                        new_end_dt = new_start_dt + duration
                                                        updated_end = {'dateTime': new_end_dt.isoformat()}
                                                else:
                                                    if current_start.get('date'):
                                                        event_date = datetime.fromisoformat(current_start['date']).date()
                                                    else:
                                                        event_date = datetime.now(tz).date()
                                                    
                                                    new_start_dt = datetime.combine(event_date, datetime.min.time()).replace(
                                                        hour=new_hour, minute=new_minute, tzinfo=tz
                                                    )
                                                    updated_start = {'dateTime': new_start_dt.isoformat()}
                                                    updated_end = {'dateTime': (new_start_dt + timedelta(hours=1)).isoformat()}
                                            except Exception as e:
                                                print(f"Error parsing new start time: {e}")
                                        
                                        if 'end' in updates:
                                            new_time_str = updates['end']
                                            try:
                                                if ':' in new_time_str:
                                                    time_parts = new_time_str.split(':')
                                                    new_hour = int(time_parts[0])
                                                    new_minute = int(time_parts[1]) if len(time_parts) > 1 else 0
                                                else:
                                                    ts = new_time_str.lower().replace(' ', '')
                                                    import re
                                                    time_match = re.match(r'(\d{1,2})(?::(\d{2}))?([ap]m)?', ts)
                                                    if time_match:
                                                        new_hour = int(time_match.group(1))
                                                        new_minute = int(time_match.group(2) or 0)
                                                        ampm = time_match.group(3)
                                                        
                                                        if ampm:
                                                            if ampm == 'pm' and new_hour < 12:
                                                                new_hour += 12
                                                            elif ampm == 'am' and new_hour == 12:
                                                                new_hour = 0
                                                    else:
                                                        raise ValueError("Invalid time format")
                                                
                                                if updated_start.get('dateTime'):
                                                    base_dt = datetime.fromisoformat(updated_start['dateTime'].replace('Z', '+00:00'))
                                                    new_end_dt = base_dt.replace(hour=new_hour, minute=new_minute)
                                                    updated_end = {'dateTime': new_end_dt.isoformat()}
                                            except Exception as e:
                                                print(f"Error parsing new end time: {e}")
                                        
                                        if 'summary' in updates:
                                            updated_summary = updates['summary']
                                        
                                        # Generate confirmation
                                        response_type = 'event_update_confirmation'
                                        response_content = {
                                            'event_id': event_id,
                                            'original': {
                                                'summary': current_summary,
                                                'start': current_start,
                                                'end': current_end
                                            },
                                            'updated': {
                                                'summary': updated_summary,
                                                'start': updated_start,
                                                'end': updated_end
                                            },
                                            'has_conflict': False,
                                            'conflicts': [],
                                            'action': 'update',
                                            'is_series_update': True
                                        }
                                        
                                        changes = []
                                        if updated_summary != current_summary:
                                            changes.append(f"title to '{updated_summary}'")
                                        if updated_start != current_start:
                                            try:
                                                if updated_start.get('dateTime'):
                                                    new_dt = datetime.fromisoformat(updated_start['dateTime'].replace('Z', '+00:00')).astimezone(tz)
                                                    time_str = new_dt.strftime('%I:%M %p').lstrip('0')
                                                    date_str = new_dt.strftime('%A, %B %d').replace(' 0', ' ')
                                                    changes.append(f"time to {time_str} on {date_str}")
                                                elif updated_start.get('date'):
                                                    date_obj = datetime.fromisoformat(updated_start['date']).date()
                                                    changes.append(f"date to {date_obj.strftime('%A, %B %d').replace(' 0', ' ')}")
                                            except:
                                                changes.append("time")
                                        
                                        if changes:
                                            change_desc = " and ".join(changes)
                                            agent_message = f"I found the recurring '{current_summary}' series. Update the {change_desc} for ALL instances?"
                                        else:
                                            agent_message = f"I found the recurring '{current_summary}' series, but I'm not sure what changes you'd like to make."
                                        
                                        draft_msg = Message.objects.create(
                                            conversation=convo,
                                            sender='agent',
                                            text=agent_message,
                                            message_type='event_update_confirmation',
                                            content=response_content,
                                        )
                                        response_content['message_id'] = draft_msg.id
                                        agent_response_text = agent_message
                                    else:
                                        # Couldn't get master, fall back to asking which one
                                        response_type = 'text'
                                        event_list = []
                                        for i, evt in enumerate(matches[:5], 1):
                                            title = evt.get('summary', 'Untitled')
                                            start = evt.get('start', {})
                                            if start.get('dateTime'):
                                                dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00')).astimezone(tz)
                                                time_str = dt.strftime('%I:%M %p on %b %d').lstrip('0')
                                                event_list.append(f"{i}. {title} ({time_str})")
                                            else:
                                                event_list.append(f"{i}. {title}")
                                        
                                        agent_response_text = f"I found {len(matches)} events matching '{summary_query}'. Which one would you like to update?\\n\\n" + "\\n".join(event_list)
                                        Message.objects.create(conversation=convo, sender='agent', text=agent_response_text, message_type='text')
                                except Exception as e:
                                    print(f"Error fetching master event: {e}")
                                    # Fall back to asking which one
                                    response_type = 'text'
                                    event_list = []
                                    for i, evt in enumerate(matches[:5], 1):
                                        title = evt.get('summary', 'Untitled')
                                        start = evt.get('start', {})
                                        if start.get('dateTime'):
                                            dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00')).astimezone(tz)
                                            time_str = dt.strftime('%I:%M %p on %b %d').lstrip('0')
                                            event_list.append(f"{i}. {title} ({time_str})")
                                        else:
                                            event_list.append(f"{i}. {title}")
                                    
                                    agent_response_text = f"I found {len(matches)} events matching '{summary_query}'. Which one would you like to update?\\n\\n" + "\\n".join(event_list)
                                    Message.objects.create(conversation=convo, sender='agent', text=agent_response_text, message_type='text')
                            else:
                                # Normal case - ask which one
                                response_type = 'text'
                                event_list = []
                                for i, evt in enumerate(matches[:5], 1):
                                    title = evt.get('summary', 'Untitled')
                                    start = evt.get('start', {})
                                    if start.get('dateTime'):
                                        dt = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00')).astimezone(tz)
                                        time_str = dt.strftime('%I:%M %p on %b %d').lstrip('0')
                                        event_list.append(f"{i}. {title} ({time_str})")
                                    else:
                                        event_list.append(f"{i}. {title}")
                                
                                agent_response_text = f"I found {len(matches)} events matching '{summary_query}'. Which one would you like to update?\\n\\n" + "\\n".join(event_list)
                                Message.objects.create(conversation=convo, sender='agent', text=agent_response_text, message_type='text')
                        
                        else:
                            response_type = 'text'
                            agent_response_text = f"I couldn't find any event matching '{summary_query}'{f' on {target_date}' if target_date else ''}."
                            Message.objects.create(conversation=convo, sender='agent', text=agent_response_text, message_type='text')



                elif action == 'list_events':
                    # List events within a date range. Support simple synonyms and NL dates.
                    norm = dict(params or {})
                    # Map simple synonyms
                    if 'date' in norm and 'start_date' not in norm and 'end_date' not in norm:
                        norm['start_date'] = norm['date']
                        norm['end_date'] = norm['date']
                    if 'start' in norm and 'start_date' not in norm:
                        norm['start_date'] = norm['start']
                    if 'end' in norm and 'end_date' not in norm:
                        norm['end_date'] = norm['end']

                    raw_start_date = (norm.get('start_date') or '').strip() or None
                    raw_end_date   = (norm.get('end_date') or '').strip() or None

                    # Helpers for parsing simple natural language dates
                    
                    try:
                        from zoneinfo import ZoneInfo
                        tz = ZoneInfo(client_tz_name or getattr(settings, 'TIME_ZONE', 'UTC') or 'UTC')
                    except Exception:
                        tz = get_current_timezone()

                    # Initialize start_date and end_date BEFORE use
                    start_date = None
                    end_date = None

                    # Anchor to current/relative week if the user asked for it,
                    # even if the AI returned stale absolute dates.
                    text_lc = (user_input or '').lower()
                    def _override_week_range(shift_weeks: int = 0):
                        today_local = datetime.now(tz).date()
                        sow = today_local - timedelta(days=today_local.weekday()) + timedelta(days=7*shift_weeks)
                        eow = sow + timedelta(days=6)
                        return sow.isoformat(), eow.isoformat()

                    if 'week' in text_lc or 'schedule' in text_lc:
                        # Detect phrases "next week" or "last/previous week"
                        if 'next week' in text_lc:
                            start_date, end_date = _override_week_range(1)
                        elif 'last week' in text_lc or 'previous week' in text_lc:
                            start_date, end_date = _override_week_range(-1)
                        elif 'this week' in text_lc or 'week' in text_lc or 'schedule' in text_lc:
                            start_date, end_date = _override_week_range(0)

                    # If AI still provided dates wildly far from "now" (> 90 days), snap to this week
                    def _parse_iso_date(d: str):
                        try:
                            return datetime.fromisoformat(str(d).strip() + 'T00:00:00').date()
                        except Exception:
                            return None
                    if start_date and end_date:
                        sd = _parse_iso_date(start_date)
                        ed = _parse_iso_date(end_date)
                        today_local = datetime.now(tz).date()
                        if sd and abs((sd - today_local).days) > 90 and ('week' in text_lc or 'schedule' in text_lc):
                            start_date, end_date = _override_week_range(0)

                    # Parse provided start/end dates or infer from text
                    # Only override if not already set from week detection
                    
                    # First, try to parse the dates provided by AI
                    if raw_start_date:
                        sd = _parse_simple_date(raw_start_date)
                        start_date = sd.isoformat() if sd else None
                    if raw_end_date:
                        ed = _parse_simple_date(raw_end_date)
                        end_date = ed.isoformat() if ed else None
                        
                    # If no start_date, try to infer from other params or text
                    if not start_date:
                        sd = _parse_simple_date(norm.get('date')) or _parse_simple_date(norm.get('start'))
                        if not sd:
                            # Try scanning the raw user text for a simple date
                            import re as _re
                            m = _re.search(r"\b\d{4}-\d{2}-\d{2}\b", text_lc)
                            sd = _parse_simple_date(m.group(0)) if m else None
                        start_date = sd.isoformat() if sd else None
                        
                    # If no end_date but we have start_date, use same date
                    if not end_date and start_date:
                        end_date = start_date

                    query = norm.get('query')

                    if not start_date and not end_date:
                        # Default to current year as per user request
                        today_local = datetime.now(tz).date()
                        start_date = datetime(today_local.year, 1, 1).date().isoformat()
                        end_date = datetime(today_local.year, 12, 31).date().isoformat()

                    queries = norm.get('queries')

                    # Build RFC3339 boundaries in UTC 'Z'
                    # Here we keep it simple by assuming all-day window(s)
                    time_min = f"{start_date}T00:00:00Z"
                    time_max = f"{end_date}T23:59:59Z"
                    try:
                        items = gcal.list_events('primary', time_min=time_min, time_max=time_max, q=query, queries=queries)

                        def _fmt_when(ev):
                            start = (ev.get('start') or {})
                            end = (ev.get('end') or {})
                            s = start.get('dateTime') or start.get('date')
                            e = end.get('dateTime') or end.get('date')
                            def _parse_dt(v):
                                if not v:
                                    return None
                                if isinstance(v, str) and v.endswith('Z'):
                                    v = v.replace('Z', '+00:00')
                                try:
                                    return datetime.fromisoformat(v)
                                except Exception:
                                    return None
                            ds = _parse_dt(s)
                            de = _parse_dt(e)
                            try:
                                # Localize for display
                                if ds and ds.tzinfo:
                                    ds_local = ds.astimezone(tz)
                                elif ds:
                                    ds_local = ds.replace(tzinfo=None)
                                else:
                                    ds_local = None
                                if de and de.tzinfo:
                                    de_local = de.astimezone(tz)
                                elif de:
                                    de_local = de.replace(tzinfo=None)
                                else:
                                    de_local = None
                                if ds_local and de_local and ds_local.date() == de_local.date():
                                    return f"{ds_local.strftime('%b %d, %Y')} • {ds_local.strftime('%I:%M %p').lstrip('0')} – {de_local.strftime('%I:%M %p').lstrip('0')}"
                                if ds_local and de_local:
                                    return f"{ds_local.strftime('%b %d %I:%M %p').lstrip('0')} → {de_local.strftime('%b %d %I:%M %p').lstrip('0')}"
                                if ds_local:
                                    return ds_local.strftime('%b %d, %Y')
                                return ''
                            except Exception:
                                return ''

                        if not items:
                            when_text = start_date if start_date == end_date else f"{start_date} to {end_date}"
                            summary = f"You have no events on {when_text}."
                        else:
                            # Group events by day
                            from collections import defaultdict
                            events_by_day = defaultdict(list)
                            
                            def _parse_event_date(ev):
                                """Extract date from event for grouping"""
                                start = (ev.get('start') or {})
                                s = start.get('dateTime') or start.get('date')
                                if not s:
                                    return None
                                if isinstance(s, str) and s.endswith('Z'):
                                    s = s.replace('Z', '+00:00')
                                try:
                                    dt = datetime.fromisoformat(s)
                                    if dt.tzinfo:
                                        dt = dt.astimezone(tz)
                                    return dt.date()
                                except Exception:
                                    return None
                            
                            def _format_event_time(ev):
                                """Format event time range for display"""
                                start = (ev.get('start') or {})
                                end = (ev.get('end') or {})
                                s = start.get('dateTime') or start.get('date')
                                e = end.get('dateTime') or end.get('date')
                                
                                def _parse_dt(v):
                                    if not v:
                                        return None
                                    if isinstance(v, str) and v.endswith('Z'):
                                        v = v.replace('Z', '+00:00')
                                    try:
                                        return datetime.fromisoformat(v)
                                    except Exception:
                                        return None
                                
                                ds = _parse_dt(s)
                                de = _parse_dt(e)
                                
                                try:
                                    # Localize for display
                                    if ds and ds.tzinfo:
                                        ds_local = ds.astimezone(tz)
                                    elif ds:
                                        ds_local = ds
                                    else:
                                        ds_local = None
                                    if de and de.tzinfo:
                                        de_local = de.astimezone(tz)
                                    elif de:
                                        de_local = de
                                    else:
                                        de_local = None
                                    
                                    if ds_local and de_local:
                                        return f"{ds_local.strftime('%I:%M %p').lstrip('0')} - {de_local.strftime('%I:%M %p').lstrip('0')}"
                                    elif ds_local:
                                        return ds_local.strftime('%I:%M %p').lstrip('0')
                                    return ''
                                except Exception:
                                    return ''
                            
                            # Group events by day
                            for ev in items:
                                event_date = _parse_event_date(ev)
                                if event_date:
                                    events_by_day[event_date].append(ev)
                            
                            # Determine the time range type (day/week/month/year)
                            try:
                                start_dt = datetime.fromisoformat(start_date + 'T00:00:00').date()
                                end_dt = datetime.fromisoformat(end_date + 'T00:00:00').date()
                                day_span = (end_dt - start_dt).days + 1
                                
                                # Classify the range
                                if day_span == 1:
                                    range_type = 'day'
                                elif day_span <= 7:
                                    range_type = 'week'
                                elif day_span <= 31:
                                    range_type = 'month'
                                else:
                                    range_type = 'year'
                            except Exception:
                                range_type = 'week'  # Default fallback
                            
                            # Build formatted output
                            lines = []
                            
                            # Add header with AI-generated title
                            try:
                                start_dt = datetime.fromisoformat(start_date + 'T00:00:00').date()
                                end_dt = datetime.fromisoformat(end_date + 'T00:00:00').date()
                                
                                # Generate AI title based on context
                                title_generated = False
                                try:
                                    # Prepare context for AI
                                    search_context = ""
                                    if queries and isinstance(queries, list) and len(queries) > 0:
                                        if len(queries) == 1:
                                            search_context = f"searching for '{queries[0]}'"
                                        elif len(queries) == 2:
                                            search_context = f"searching for '{queries[0]}' and '{queries[1]}'"
                                        else:
                                            # Build quoted terms separately to avoid f-string backslash issue
                                            quoted_terms = ', '.join(f"'{q}'" for q in queries[:-1])
                                            search_context = f"searching for {quoted_terms}, and '{queries[-1]}'"
                                    elif query:
                                        search_context = f"searching for '{query}'"
                                    
                                    # Format date range
                                    if start_dt == end_dt:
                                        date_context = start_dt.strftime('%B %d, %Y')
                                    elif start_dt.year == end_dt.year:
                                        if start_dt.month == end_dt.month:
                                            date_context = f"{start_dt.strftime('%B %d')}-{end_dt.day}, {start_dt.year}"
                                        else:
                                            date_context = f"{start_dt.strftime('%B %d')} - {end_dt.strftime('%B %d, %Y')}"
                                    else:
                                        date_context = f"{start_dt.strftime('%B %Y')} - {end_dt.strftime('%B %Y')}"
                                    
                                    # Build AI prompt
                                    ai_prompt = f"""Generate a short, natural title (max 10 words) for a calendar event list.

                                        Context:
                                        - User's query: "{user_input}"
                                        - {search_context if search_context else "showing all events"}
                                        - Date range: {date_context}
                                        - Found {len(items)} event(s)

                                        Rules:
                                        - Start with the calendar emoji 📅
                                        - Be concise and natural
                                        - Include the search terms if present
                                        - Include the time period
                                        - Examples:
                                        * "📅 Bible study and Miracle hour - December 2025 to April 2026"
                                        * "📅 Bible study in 2025"
                                        * "📅 Your schedule for December 1-7, 2025"

                                        Generate only the title, nothing else:"""
                                    
                                    # Call AI to generate title
                                    ai_title = ai_agent._get_claude_chat_response(
                                        [{"role": "user", "content": ai_prompt}],
                                        system_prompt="You are a helpful assistant that generates concise, natural calendar titles.",
                                        temperature=0.7,
                                        max_tokens=50
                                    )
                                    
                                    if ai_title and ai_title.strip():
                                        # Clean up the title (remove quotes if present)
                                        ai_title = ai_title.strip().strip('"').strip("'")
                                        lines.append(f"{ai_title}\n")
                                        title_generated = True
                                except Exception as e:
                                    print(f"Error generating AI title: {e}")
                                    # Fall through to template-based fallback
                                
                                # Fallback to template-based title if AI generation failed
                                if not title_generated:
                                    title_prefix = "📅 "
                                    if queries and isinstance(queries, list) and len(queries) > 0:
                                        # User searched for specific events
                                        if len(queries) == 1:
                                            search_term = queries[0].capitalize()
                                        elif len(queries) == 2:
                                            search_term = f"{queries[0].capitalize()} and {queries[1]}"
                                        else:
                                            search_term = f"{', '.join(q.capitalize() for q in queries[:-1])}, and {queries[-1]}"
                                        
                                        # Add contextual date range
                                        if range_type == 'year':
                                            lines.append(f"{title_prefix}{search_term} in {start_dt.strftime('%Y')}\n")
                                        elif range_type == 'month':
                                            lines.append(f"{title_prefix}{search_term} in {start_dt.strftime('%B %Y')}\n")
                                        elif range_type == 'week':
                                            if start_dt.month == end_dt.month and start_dt.year == end_dt.year:
                                                date_range = f"{start_dt.strftime('%B')} {start_dt.day}-{end_dt.day}, {start_dt.year}"
                                            else:
                                                date_range = f"{start_dt.strftime('%B %d')} - {end_dt.strftime('%B %d, %Y')}"
                                            lines.append(f"{title_prefix}{search_term} - {date_range}\n")
                                        else:  # day
                                            today_date = datetime.now(tz).date()
                                            day_label = "today" if start_dt == today_date else f"on {start_dt.strftime('%A, %B %d, %Y')}"
                                            lines.append(f"{title_prefix}{search_term} {day_label}\n")
                                    elif query:
                                        # User searched with a single query string
                                        search_term = query.capitalize()
                                        if range_type == 'year':
                                            lines.append(f"{title_prefix}{search_term} in {start_dt.strftime('%Y')}\n")
                                        elif range_type == 'month':
                                            lines.append(f"{title_prefix}{search_term} in {start_dt.strftime('%B %Y')}\n")
                                        elif range_type == 'week':
                                            if start_dt.month == end_dt.month and start_dt.year == end_dt.year:
                                                date_range = f"{start_dt.strftime('%B')} {start_dt.day}-{end_dt.day}, {start_dt.year}"
                                            else:
                                                date_range = f"{start_dt.strftime('%B %d')} - {end_dt.strftime('%B %d, %Y')}"
                                            lines.append(f"{title_prefix}{search_term} - {date_range}\n")
                                        else:  # day
                                            today_date = datetime.now(tz).date()
                                            day_label = "today" if start_dt == today_date else f"on {start_dt.strftime('%A, %B %d, %Y')}"
                                            lines.append(f"{title_prefix}{search_term} {day_label}\n")
                                    else:
                                        # No search query - use generic title
                                        if range_type == 'day':
                                            today_date = datetime.now(tz).date()
                                            day_label = "Today's Schedule" if start_dt == today_date else f"Schedule for {start_dt.strftime('%A, %B %d, %Y')}"
                                            lines.append(f"{title_prefix}{day_label}\n")
                                        elif range_type == 'week':
                                            if start_dt.month == end_dt.month and start_dt.year == end_dt.year:
                                                date_range = f"{start_dt.strftime('%B')} {start_dt.day}-{end_dt.day}, {start_dt.year}"
                                            else:
                                                date_range = f"{start_dt.strftime('%B %d')} - {end_dt.strftime('%B %d, %Y')}"
                                            lines.append(f"{title_prefix}Your Weekly Schedule - {date_range}\n")
                                        elif range_type == 'month':
                                            lines.append(f"{title_prefix}Your Schedule for {start_dt.strftime('%B %Y')}\n")
                                        else:  # year
                                            lines.append(f"{title_prefix}Your Schedule for {start_dt.strftime('%Y')}\n")
                            except Exception:
                                lines.append("📅 Your Schedule\n")
                            
                            # Sort days chronologically
                            sorted_days = sorted(events_by_day.keys())
                            today_date = datetime.now(tz).date()
                            
                            for day in sorted_days:
                                day_events = events_by_day[day]
                                
                                # Format day header (remove leading zero from day)
                                day_name = day.strftime('%A, %B %d').replace(' 0', ' ')
                                
                                # Add (Today) indicator if applicable
                                if day == today_date:
                                    day_name += " (Today)"
                                
                                lines.append(f"**{day_name}**")
                                
                                # Add events for this day
                                for ev in day_events:
                                    title = ev.get('summary') or 'Untitled'
                                    time_str = _format_event_time(ev)
                                    lines.append(f"• {time_str}: {title}")
                                
                                lines.append("")  # Empty line between days
                            
                            # Add days with no events within the range (only for day and week views)
                            if range_type in ['day', 'week']:
                                try:
                                    start_dt = datetime.fromisoformat(start_date + 'T00:00:00').date()
                                    end_dt = datetime.fromisoformat(end_date + 'T00:00:00').date()
                                    current_date = start_dt
                                    
                                    while current_date <= end_dt:
                                        if current_date not in events_by_day:
                                            day_name = current_date.strftime('%A, %B %d').replace(' 0', ' ')
                                            if current_date == today_date:
                                                day_name += " (Today)"
                                            
                                            # Insert in chronological order
                                            inserted = False
                                            for i, line in enumerate(lines):
                                                if line.startswith('**'):
                                                    line_date_str = line.strip('*').split(' (')[0]
                                                    # Simple comparison - if this empty day should come before this line
                                                    if current_date < _parse_event_date(items[0]) if items else False:
                                                        lines.insert(i, f"**{day_name}**")
                                                        lines.insert(i+1, "*(No events scheduled)*")
                                                        lines.insert(i+2, "")
                                                        inserted = True
                                                        break
                                            
                                            if not inserted and current_date not in sorted_days:
                                                lines.append(f"**{day_name}**")
                                                lines.append("*(No events scheduled)*")
                                                lines.append("")
                                        
                                        current_date += timedelta(days=1)
                                except Exception:
                                    pass
                            
                            summary = "\n".join(lines).strip()
                            
                            # Use AI to generate a personalized closing message
                            try:
                                # Build a summary of the events for the AI
                                event_summary_parts = []
                                for day, day_events in sorted(events_by_day.items()):
                                    day_name = day.strftime('%A')
                                    event_count = len(day_events)
                                    event_titles = [ev.get('summary', 'Untitled') for ev in day_events[:3]]
                                    event_summary_parts.append(f"{day_name}: {event_count} event(s) - {', '.join(event_titles)}")
                                
                                event_summary = "; ".join(event_summary_parts[:7])  # Limit to prevent token overflow
                                
                                # Determine if events are in past, present, or future
                                today_date = datetime.now(tz).date()
                                try:
                                    start_dt = datetime.fromisoformat(start_date + 'T00:00:00').date()
                                    end_dt = datetime.fromisoformat(end_date + 'T00:00:00').date()
                                    
                                    if end_dt < today_date:
                                        time_context = "PAST events (already happened)"
                                    elif start_dt > today_date:
                                        time_context = "FUTURE events (upcoming)"
                                    elif start_dt == today_date and end_dt == today_date:
                                        time_context = "TODAY's events (current day)"
                                    else:
                                        time_context = "events spanning PAST, PRESENT, and/or FUTURE"
                                except:
                                    time_context = "events"
                                
                                ai_prompt = f"""The user just viewed their {range_type} schedule with {len(items)} total event(s). 
    
                                    CRITICAL: These are {time_context}. Your remark MUST reflect the correct time perspective.
    
                                    Events breakdown: {event_summary}
    
                                    Generate a friendly, personalized 1-2 sentence closing remark that:
                                    - Uses appropriate tense: past events = "you had/were busy", present = "you have", future = "you've got/ahead"
                                    - For PAST events, reflect on what they had scheduled (e.g., "Looks like you had a packed Monday")
                                    - For FUTURE events, look forward to what's coming (e.g., "You've got a busy day ahead")
                                    - For TODAY, use present tense (e.g., "You have a full schedule today")
                                    - Acknowledges their schedule (busy/light/balanced)
                                    - Mentions specific patterns if notable (e.g., "Friday was packed", "weekend is free")
                                    - Offers help with scheduling
                                    - Keep it warm and conversational
                                    - Add an emoji if appropriate
    
                                    Do not repeat the event list. Just provide the closing remark."""
    
                                closing_messages = [{"role": "user", "content": ai_prompt}]
                                closing_message = ai_agent._get_claude_chat_response(
                                    closing_messages,
                                    temperature=0.7,
                                    max_tokens=100
                                )
                                
                                if closing_message and closing_message.strip():
                                    summary = summary + "\n\n" + closing_message.strip()
                            except Exception as e:
                                print(f"Failed to generate AI closing message: {e}")
                                # Continue without closing message if AI fails
    
                        response_type = 'text'
                        agent_response_text = summary
                        try:
                            Message.objects.create(
                                conversation=convo,
                                sender='agent',
                                text=agent_response_text,
                                message_type='text',
                                content=None,
                            )
                        except Exception:
                            pass
                    except Exception as e:
                        response_type = 'text'
                        agent_response_text = f"Sorry, I couldn't list events: {e}"
                        # Always persist error messages so they survive reloads
                        Message.objects.create(
                            conversation=convo,
                            sender='agent',
                            text=agent_response_text,
                            message_type='text',
                            content=None,
                        )

                # etc. (list_events, delete_event …)

                # Reflect any modifications back to the payload sent to the front-end
                response_data.update({
                    'type': response_type,
                    'response': agent_response_text,
                    'content': response_content,
                })

            except Exception as e:
                print(f"Error processing calendar action: {e}")
                # traceback.print_exc()
                # User-facing error message (don't show raw exception in production)
                error_message = "Sorry, I encountered an error while processing your calendar request. The issue has been logged."
                # Persist error messages so they survive reloads
                Message.objects.create(
                    conversation=convo,
                    sender='agent',
                    text=error_message,
                    message_type='text',
                    content=None,
                )
                response_data.update({
                    'type': 'text',
                    'response': error_message,
                    'content': {},
                })
        elif response_type == 'calendar_action_request':
            # If a calendar action is requested but account is still not connected
            # return a needs_connection card to the frontend.
            response_data.update({
                'type': 'needs_connection',
                'response': None,
                'content': {
                    'message_for_user': 'Please connect your Google account to continue.',
                    'email': request.user.email,
                    'content_url': reverse('home_page:connect_google') + f"?next={reverse('home_page:assistant', args=[convo.id])}",
                    'needs_connection': True
                }
            })

        return JsonResponse(response_data)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON received'}, status=400)
    except Exception as e:
        logger.error(f"Error acting on calendar: {e}")
        import traceback
        traceback.print_exc()
        
        # Try to persist the error message if we have a conversation context
        error_message = f"Sorry, an internal server error occurred: {str(e)}"
        try:
            if 'convo' in locals() and convo:
                Message.objects.create(
                    conversation=convo,
                    sender='agent',
                    text=error_message,
                    message_type='text',
                    content=None,
                )
        except Exception:
            # If we can't persist, at least return the error to frontend
            pass
            
        return JsonResponse({'error': error_message}, status=500)


@login_required
@require_POST
def delete_conversation(request, convo_id:uuid.UUID):
    user = request.user
    conversation = get_object_or_404(Conversation, id=convo_id, user=user)

    try:
        conversation.delete()
        logger.info(f"User {user.username} deleted conversation {convo_id}")

        # Find the latest conversation after deletion
        remaining_conversations = Conversation.objects.filter(user=user).order_by('-created_at')

        if remaining_conversations.exists():
             # Redirect to the latest conversation
             latest_convo = remaining_conversations.first()
             print(f"Deleted convo {convo_id}. Redirecting to latest remaining convo {latest_convo.id}.")
             return JsonResponse({'success': True, 'redirect_url': reverse('home_page:assistant', args=[latest_convo.id])})
        else:
             # If no conversations remain, redirect to the initial empty state (no convo ID)
             print(f"Deleted convo {convo_id}. No convos remaining. Redirecting to initial empty state.")
             # Redirect to the base assistant URL
             return JsonResponse({'success': True, 'redirect_url': reverse('home_page:assistant')})


    except Exception as e:
        logger.error(f"Error deleting conversation {convo_id} for user {user.username}: {e}", exc_info=True) # log error
        return JsonResponse({'success': False, 'error': 'Error deleting conversation.'}, status=500)
    

def connect_google(request):
    initial_next = request.GET.get("next", "/agent/assistant/")

    parts      = urlparse(initial_next)
    query_dict = parse_qs(parts.query)
    query_dict["resume"] = ["true"]           # overwrite/add exactly once

    new_query = urlencode(query_dict, doseq=True)
    next_url  = urlunparse(parts._replace(query=new_query))
    extras = [ # scopes for gmail and calendar
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    params = [
        ("scope", " ".join(extras + ["profile", "email"])),
        ("process", "connect"), 
            ("prompt", "consent"), # to get new refresh tokens
            ("access_type", "offline"), # ensure refresh_token is issued
        ("next", next_url),
    ]
    qs = urllib.parse.urlencode(params)
    return redirect(f"/accounts/google/login/?{qs}")

@login_required
def settings_view(request):
    from .models import NotificationPreference
    
    # helper to ensure prefs exist
    prefs, created = NotificationPreference.objects.get_or_create(user=request.user)
    
    # Get return URL from POST or GET
    next_url = request.POST.get('next') or request.GET.get('next')

    if request.method == "POST":
        whatsapp_number = request.POST.get("whatsapp_number", "").strip()
        whatsapp_enabled = request.POST.get("whatsapp_enabled") == "on"
        email_enabled = request.POST.get("email_enabled") == "on"

        # Basic validation: if enabling WA, number should be present
        if whatsapp_enabled and not whatsapp_number:
            messages.error(request, "Please enter a valid WhatsApp number to enable WhatsApp reminders.")
        else:
            # If user provides a number but doesn't format it perfectly, we could try to clean it
            # For now, just save it.
            prefs.whatsapp_number = whatsapp_number
            prefs.whatsapp_enabled = whatsapp_enabled
            prefs.email_enabled = email_enabled
            
            # New fields
            try:
                lead_time = int(request.POST.get("reminder_lead_time", 30))
                prefs.reminder_lead_time = max(5, min(1440, lead_time))
            except (ValueError, TypeError):
                prefs.reminder_lead_time = 30
                
            prefs.morning_briefing_enabled = request.POST.get("morning_briefing_enabled") == "on"
            
            briefing_time_str = request.POST.get("morning_briefing_time")
            if briefing_time_str:
                try:
                    # Validate time format HH:MM
                    datetime.strptime(briefing_time_str, '%H:%M')
                    prefs.morning_briefing_time = briefing_time_str
                except ValueError:
                    pass # Keep existing or default
            
            prefs.save()
            messages.success(request, "Preferences saved successfully!")
            
            # Redirect to previous page if set, otherwise reload settings
            if next_url and next_url != request.path:
                return redirect(next_url)
        
        return redirect('home_page:settings')

    # Sidebar needs conversations
    conversations = Conversation.objects.filter(user=request.user).order_by('-created_at')

    context = {
        "preferences": prefs,
        "conversations": conversations, 
        "current_convo": None, # No chat selected
        "next_url": next_url,
    }
    return render(request, "home_page/settings.html", context)
