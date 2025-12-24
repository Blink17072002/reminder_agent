import os
import django
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')
django.setup()

from django.contrib.auth import get_user_model
from home_page.models import NotificationPreference, SentNotification
from home_page.services.notification_service import check_and_send_reminders

User = get_user_model()

def run_test():
    print("Setting up test...")
    
    # 1. Create a test user
    username = f"testuser_{int(datetime.now().timestamp())}"
    user = User.objects.create_user(username=username, email=f"{username}@example.com", password="password")
    
    # 2. Create notification preference
    NotificationPreference.objects.create(
        user=user,
        whatsapp_number="whatsapp:+1234567890",
        whatsapp_enabled=True,
        email_enabled=True
    )
    
    print(f"Created user {username} with prefs.")

    # 3. Mock GoogleCalendarService to return an upcoming event
    with patch('home_page.services.notification_service.GoogleCalendarService') as MockServiceClass:
        mock_service = MockServiceClass.return_value
        
        # Define a mock event starting in 10 minutes
        start_time = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        mock_event = {
            'id': 'evt_123',
            'summary': 'Test Meeting',
            'start': {'dateTime': start_time}
        }
        mock_service.list_events.return_value = [mock_event]
        
        # Mock send_email
        mock_service.send_email.return_value = True

        # Mock settings
        with patch('home_page.services.notification_service.settings') as mock_settings:
            mock_settings.TWILIO_ACCOUNT_SID = 'AC_testing'
            mock_settings.TWILIO_AUTH_TOKEN = 'token_testing'
            mock_settings.TWILIO_WHATSAPP_NUMBER = 'whatsapp:+199999999'

            # 4. Mock Twilio Client
            with patch('home_page.services.notification_service.Client') as MockTwilioClient:

                mock_twilio_instance = MockTwilioClient.return_value
                mock_message = MagicMock()
                mock_message.sid = "SM12345"
                mock_twilio_instance.messages.create.return_value = mock_message

                print("Running check_and_send_reminders()...")
                check_and_send_reminders()
                
                # 5. Assertions
                print("Verifying email sent...")
                mock_service.send_email.assert_called_once()
                args = mock_service.send_email.call_args
                print(f"Email called with: {args}")
                
                print("Verifying WhatsApp sent...")
                mock_twilio_instance.messages.create.assert_called_once()
                wargs = mock_twilio_instance.messages.create.call_args
                print(f"WhatsApp called with: {repr(wargs)}")
                
                # Check DB side effects
                print("Verifying SentNotification records...")
                sent_count = SentNotification.objects.filter(user=user, event_id='evt_123').count()
                print(f"Found {sent_count} SentNotification records.")
                if sent_count == 2:
                    print("SUCCESS: Both notifications recorded.")
                else:
                    print(f"FAILURE: Expected 2 notifications, found {sent_count}.")
                    
                # Cleanup
                user.delete()

if __name__ == "__main__":
    run_test()
