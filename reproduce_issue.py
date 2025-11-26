import os
import django
from django.conf import settings

# Configure Django settings manually since we are running a standalone script
if not settings.configured:
    settings.configure(
        INSTALLED_APPS=[
            'django.contrib.auth',
            'django.contrib.contenttypes',
            'django.contrib.sessions',
            'django.contrib.sites',
            'allauth',
            'allauth.account',
            'allauth.socialaccount',
            'home_page',
        ],
        SITE_ID=1,
        SECRET_KEY='secret',
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        TIME_ZONE='UTC',
    )
    django.setup()

from unittest.mock import MagicMock, patch
from home_page.services.ai_agent import AIAgent

def reproduce():
    # Mock user
    user = MagicMock()
    user.email = "test@example.com"
    
    # Initialize agent
    agent = AIAgent(user)
    
    # Mock dependencies
    agent.claude_client = MagicMock()
    agent.is_google_connected = MagicMock(return_value=True)
    agent.determine_intent = MagicMock(return_value='calendar')
    
    # Mock the raw response from Claude to be the refusal text we saw in logs
    refusal_text = "Unfortunately I do not have access to your personal calendar or schedule. As a calendar assistant, I can only process actions and requests related to the current date."
    
    # We need to mock _get_claude_chat_response to return this text
    # The handle method calls _get_claude_chat_response
    agent._get_claude_chat_response = MagicMock(return_value=refusal_text)
    
    # Also mock summarize_user_fields to behave as it currently does (returning missing fields)
    # This confirms the fallback path is taken
    agent.summarize_user_fields = MagicMock(return_value={
        "present": {}, 
        "missing": ["date", "time", "summary"]
    })
    
    print("--- Running Reproduction ---")
    print(f"Mocked AI Response: {refusal_text}")
    
    # Run handle
    result = agent.handle("Check my schedule for last week")
    
    print("\n--- Result ---")
    print(f"Type: {result.get('type')}")
    print(f"Response: {result.get('response')}")
    
    # Check if the result matches the generic fallback message
    if "I can schedule that" in result.get('response', ''):
        print("\n✅ Issue Reproduced: Agent returned generic scheduling prompt instead of the refusal.")
    elif result.get('response') == refusal_text:
        print("\n❌ Issue Not Reproduced: Agent returned the refusal text (already fixed?).")
    else:
        print(f"\n❓ Unexpected Result: {result}")

if __name__ == "__main__":
    reproduce()
