import os
import django
import json
from datetime import datetime, timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')
django.setup()

from home_page.models import Conversation, Message
from home_page.services.calendar_service import GoogleCalendarService
from django.contrib.auth.models import User
from django.test import RequestFactory
from home_page.views import chat_process

def verify_deletion_flow():
    # 1. Setup User and Conversation
    user = User.objects.first()
    if not user:
        print("No user found. Please create a superuser or run migrations.")
        return

    convo = Conversation.objects.create(user=user)
    print(f"Created test conversation: {convo.id}")

    # 2. Create a Test Event directly via Service
    service = GoogleCalendarService(user)
    start_time = (datetime.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    end_time = start_time + timedelta(hours=1)
    
    event = {
        'summary': 'Verification Test Event',
        'start': {'dateTime': start_time.isoformat()},
        'end': {'dateTime': end_time.isoformat()},
    }
    
    created_event = service.create_event('primary', event)
    print(f"Created test event: {created_event.get('id')}")

    # 3. Simulate User Requesting Deletion
    factory = RequestFactory()
    
    # Request deletion
    data = {
        'message': 'Delete Verification Test Event',
        'convo_id': str(convo.id),
        'client_tz': 'UTC'
    }
    request = factory.post('/agent/chat/process/', data=json.dumps(data), content_type='application/json')
    request.user = user
    
    print("\nSending deletion request...")
    response = chat_process(request)
    response_data = json.loads(response.content)
    
    print(f"Response Type: {response_data.get('type')}")
    
    if response_data.get('type') == 'event_deletion_confirmation':
        print("SUCCESS: Received deletion confirmation request.")
        event_id_to_delete = response_data['content']['event_id']
        
        # 4. Simulate User Saying "Yes"
        data_confirm = {
            'message': 'Yes',
            'convo_id': str(convo.id),
            'client_tz': 'UTC'
        }
        request_confirm = factory.post('/agent/chat/process/', data=json.dumps(data_confirm), content_type='application/json')
        request_confirm.user = user
        
        print("\nSending 'Yes' confirmation...")
        response_confirm = chat_process(request_confirm)
        response_confirm_data = json.loads(response_confirm.content)
        
        print(f"Confirmation Response Type: {response_confirm_data.get('type')}")
        
        if response_confirm_data.get('type') == 'event_success': # Or whatever success type is returned
             print("SUCCESS: Deletion confirmed via text.")
             
             # Verify event is actually gone
             try:
                 service.service.events().get(calendarId='primary', eventId=event_id_to_delete).execute()
                 print("FAILURE: Event still exists in calendar.")
             except Exception as e:
                 if '404' in str(e) or 'deleted' in str(e).lower():
                     print("SUCCESS: Event verified as deleted from calendar.")
                 else:
                     print(f"WARNING: Error checking event status: {e}")

        elif response_confirm_data.get('type') == 'event_deleted':
             print("SUCCESS: Deletion confirmed via text (event_deleted type).")
        else:
             print(f"FAILURE: Expected success/deleted response, got {response_confirm_data.get('type')}")
             print(response_confirm_data)

    else:
        print("FAILURE: Did not receive deletion confirmation.")
        print(response_data)

if __name__ == "__main__":
    verify_deletion_flow()
