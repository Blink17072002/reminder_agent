import os
import django
from django.conf import settings
from django.test import RequestFactory
from unittest.mock import MagicMock, patch
import json

# Configure Django settings
if not settings.configured:
    settings.configure(
        INSTALLED_APPS=[
            'django.contrib.auth',
            'django.contrib.contenttypes',
            'django.contrib.sessions',
            'django.contrib.messages',
            'home_page',
        ],
        SECRET_KEY='secret',
        TIME_ZONE='UTC',
        MIDDLEWARE=[
            'django.contrib.sessions.middleware.SessionMiddleware',
            'django.contrib.messages.middleware.MessageMiddleware',
        ],
        ROOT_URLCONF='home_page.urls',
    )
    django.setup()

# Mock allauth and google modules BEFORE importing views
import sys
sys.modules['allauth'] = MagicMock()
sys.modules['allauth.socialaccount'] = MagicMock()
sys.modules['allauth.socialaccount.models'] = MagicMock()
sys.modules['google'] = MagicMock()
sys.modules['google.oauth2'] = MagicMock()
sys.modules['google.oauth2.credentials'] = MagicMock()
sys.modules['googleapiclient'] = MagicMock()
sys.modules['googleapiclient.discovery'] = MagicMock()
sys.modules['google.auth'] = MagicMock()
sys.modules['google.auth.transport'] = MagicMock()
sys.modules['google.auth.transport.requests'] = MagicMock()
sys.modules['anthropic'] = MagicMock()

from home_page.views import chat_process
from home_page.models import Conversation, Message

@patch('home_page.views.GoogleCalendarService')
@patch('home_page.views.AIAgent')
def test_recurring_update(MockAIAgent, MockService):
    # Setup mocks
    service_instance = MockService.return_value
    agent_instance = MockAIAgent.return_value
    
    # Mock list_events to return a recurring instance
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
    
    # Mock get_event to return the master event
    master_event = {
        "id": master_id,
        "summary": "Weekly Meeting",
        "start": {"dateTime": "2023-11-29T10:00:00Z"},
        "end": {"dateTime": "2023-11-29T11:00:00Z"},
        "recurrence": ["RRULE:FREQ=WEEKLY"]
    }
    service_instance.get_event.return_value = master_event
    
    # Mock AI response to simulate "update series" intent
    # The view calls agent.handle()
    agent_instance.handle.return_value = {
        "action": "update_event",
        "params": {
            "summary": "Weekly Meeting",
            "update_series": True,
            "updates": {"summary": "Weekly Meeting Updated"}
        },
        "message_for_user": "Updating series..."
    }
    
    # Create a request
    factory = RequestFactory()
    data = {'user_input': 'Update all instances of Weekly Meeting'}
    request = factory.post('/chat_process', data=data, content_type='application/json')
    
    # Mock user and session
    request.user = MagicMock()
    request.user.is_authenticated = True
    request.session = {}
    
    # Mock Conversation.objects.get (or create)
    # Since chat_process uses get_object_or_404 or similar, we might need to mock models
    # But chat_process creates conversation if not exists?
    # Let's look at chat_process signature/logic.
    # It takes conversation_id from body.
    
    data['conversation_id'] = 'new'
    request._body = json.dumps(data).encode('utf-8')
    
    # We need to mock Message.objects.create to avoid DB access
    with patch('home_page.views.Message.objects.create') as mock_msg_create, \
         patch('home_page.views.Conversation.objects.create') as mock_conv_create, \
         patch('home_page.views.Conversation.objects.get') as mock_conv_get:
             
        mock_conv = MagicMock()
        mock_conv_create.return_value = mock_conv
        mock_conv_get.return_value = mock_conv
        
        # Run the view
        response = chat_process(request)
        
        # Verify get_event was called with master_id
        service_instance.get_event.assert_called_with('primary', master_id)
        
        # Verify update_event was called with master_id
        # Note: chat_process usually returns a confirmation card first, 
        # and the ACTUAL update happens when user confirms?
        # Let's check views.py again.
        
        # In views.py, 'update_event' action from AI leads to:
        # 1. Search/Filter events
        # 2. Generate 'event_update_confirmation' message (draft)
        # 3. Return JSON to frontend
        
        # It does NOT call update_event immediately. It waits for user confirmation.
        # But the confirmation message content should contain the event_id to be updated.
        
        # Check the arguments passed to Message.objects.create for the confirmation message
        # The content should have 'event_id': master_id
        
        found_confirmation = False
        for call in mock_msg_create.call_args_list:
            args, kwargs = call
            if kwargs.get('message_type') == 'event_update_confirmation':
                content = kwargs.get('content')
                print(f"Confirmation content: {content}")
                if content.get('event_id') == master_id:
                    print("✅ Verified: Confirmation contains Master Event ID.")
                    found_confirmation = True
                else:
                    print(f"❌ Failed: Confirmation contains {content.get('event_id')}, expected {master_id}")
        
        if not found_confirmation:
            print("❌ Failed: No event_update_confirmation message created.")

if __name__ == "__main__":
    test_recurring_update()
