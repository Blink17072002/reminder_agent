import os
import django
from django.conf import settings
from unittest.mock import MagicMock, patch
from datetime import datetime

# Configure Django settings manually
if not settings.configured:
    settings.configure(
        INSTALLED_APPS=[
            'django.contrib.auth',
            'django.contrib.contenttypes',
            'home_page',
        ],
        SECRET_KEY='secret',
        TIME_ZONE='UTC',
    )
    django.setup()

import sys
from unittest.mock import MagicMock

# Mock allauth modules
sys.modules['allauth'] = MagicMock()
sys.modules['allauth.socialaccount'] = MagicMock()
sys.modules['allauth.socialaccount.models'] = MagicMock()

# Mock google modules
sys.modules['google'] = MagicMock()
sys.modules['google.oauth2'] = MagicMock()
sys.modules['google.oauth2.credentials'] = MagicMock()
sys.modules['googleapiclient'] = MagicMock()
sys.modules['googleapiclient.discovery'] = MagicMock()
sys.modules['google.auth'] = MagicMock()
sys.modules['google.auth.transport'] = MagicMock()
sys.modules['google.auth.transport.requests'] = MagicMock()

from home_page.services.calendar_service import GoogleCalendarService

def reproduce_recurring_update():
    # Mock user
    user = MagicMock()
    
    # Mock GoogleCalendarService
    with patch('home_page.services.calendar_service.GoogleCalendarService') as MockService:
        service_instance = MockService.return_value
        
        # Simulate list_events returning a recurring event instance
        # Recurring event instances usually have an ID different from the master, and contain 'recurringEventId'
        instance_id = "event_123_20231129T100000Z"
        master_id = "event_123"
        
        recurring_instance = {
            "id": instance_id,
            "summary": "Weekly Meeting",
            "start": {"dateTime": "2023-11-29T10:00:00Z"},
            "end": {"dateTime": "2023-11-29T11:00:00Z"},
            "recurringEventId": master_id
        }
        
        service_instance.list_events.return_value = [recurring_instance]
        
        # Simulate the logic in views.py (simplified)
        # 1. Search for event
        events = service_instance.list_events(time_min="...", time_max="...")
        
        # 2. Filter (assume we found it)
        event_to_update = events[0]
        
        # 3. Prepare update (e.g. change title)
        event_id = event_to_update['id']
        calendar_id = 'primary'
        event_body = {
            'summary': 'Weekly Meeting Updated',
            'start': event_to_update['start'],
            'end': event_to_update['end']
        }
        
        # 4. Call update_event
        service_instance.update_event(calendar_id, event_id, event_body)
        
        # Verify what was called
        print(f"Event ID used for update: {event_id}")
        print(f"Is this the master ID? {event_id == master_id}")
        
        if event_id != master_id:
            print("❌ Current behavior: Updates the single instance, NOT the series.")
        else:
            print("✅ Current behavior: Updates the series.")

if __name__ == "__main__":
    reproduce_recurring_update()
