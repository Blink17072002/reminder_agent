import time
from django.core.management.base import BaseCommand
from home_page.services.notification_service import check_and_send_reminders, check_and_send_morning_briefings

class Command(BaseCommand):
    help = 'Runs the reminder checking loop'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting reminder agent service...'))
        
        while True:
            try:
                check_and_send_reminders()
                check_and_send_morning_briefings()
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error in reminder loop: {e}"))
            
            # Sleep for 10 seconds before next check
            # self.stdout.write("Sleeping for 10 seconds...")
            time.sleep(10)
