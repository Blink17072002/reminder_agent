import os
import django
import logging
import sys

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')
django.setup()

# Configure logging to stdout
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

from home_page.services.notification_service import check_and_send_reminders
from home_page.models import NotificationPreference
from django.contrib.auth import get_user_model

def run_debug():
    print("--- Debugging Reminder Service ---")
    User = get_user_model()
    
    # 1. Check users
    print(f"Total users: {User.objects.count()}")
    
    # 2. Check preferences
    prefs = NotificationPreference.objects.all()
    print(f"Total notification preferences found: {prefs.count()}")
    for p in prefs:
        print(f"  - User: {p.user.username}, WA: {p.whatsapp_enabled} ({p.whatsapp_number}), Email: {p.email_enabled}")

    # 3. Run check
    print("\nRunning check_and_send_reminders()...")
    check_and_send_reminders()
    print("--- Done ---")

if __name__ == "__main__":
    run_debug()
