
import os
import django
import sys
import logging

# Setup Django environment
sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')
django.setup()

from django.conf import settings
from django.core.mail import send_mail
from home_page.services.calendar_service import GoogleCalendarService
from django.contrib.auth.models import User

def verify_config():
    print("--- 1. Checking Email Settings ---")
    print(f"EMAIL_HOST: {settings.EMAIL_HOST}")
    print(f"EMAIL_PORT: {settings.EMAIL_PORT}")
    print(f"EMAIL_HOST_USER: {settings.EMAIL_HOST_USER}")
    print(f"EMAIL_USE_TLS: {settings.EMAIL_USE_TLS}")
    
    # Test Email
    try:
        print("\nAttempting to send test email...")
        send_mail(
            subject='Test Email from Reminder Agent',
            message='This is a test email to verify SMTP settings.',
            from_email=f"Test Agent <{settings.EMAIL_HOST_USER}>",
            recipient_list=[settings.EMAIL_HOST_USER], # Send to self
            fail_silently=False,
        )
        print("SUCCESS: Test email sent!")
    except Exception as e:
        print(f"FAILURE: Could not send email. Error: {e}")

    print("\n--- 2. Checking Google Calendar Auth ---")
    # user = User.objects.first() # specific to user 'joshua' usually
    # if not user:
    #     print("No user found in DB.")
    #     return

    # print(f"Testing for user: {user.username}")
    # try:
    #     service = GoogleCalendarService(user)
    #     print("GoogleCalendarService initialized.")
    #     events = service.list_events(time_min=None, q=None) # just list some events
    #     print(f"SUCCESS: Retrieved {len(events)} events from Calendar.")
    # except Exception as e:
    #     print(f"FAILURE: Google API Error: {e}")

if __name__ == "__main__":
    verify_config()
