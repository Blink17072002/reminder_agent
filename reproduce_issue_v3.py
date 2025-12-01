
import os
import sys
import django
from unittest.mock import MagicMock

# Setup Django environment
sys.path.append('c:\\Users\\blink\\OneDrive\\Documents\\reminder_agent')
sys.modules['dotenv'] = MagicMock()
sys.modules['allauth'] = MagicMock()
sys.modules['allauth.socialaccount'] = MagicMock()
sys.modules['allauth.socialaccount.models'] = MagicMock()
sys.modules['googleapiclient'] = MagicMock()
sys.modules['googleapiclient.discovery'] = MagicMock()
sys.modules['google.oauth2'] = MagicMock()
sys.modules['google.oauth2.credentials'] = MagicMock()
sys.modules['google.auth'] = MagicMock()
sys.modules['google.auth.transport'] = MagicMock()
sys.modules['google.auth.transport.requests'] = MagicMock()

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')
django.setup()

from home_page.services.ai_agent import AIAgent

# Mock conversation and messages
class MockMessage:
    def __init__(self, sender, text, timestamp):
        self.sender = sender
        self.text = text
        self.timestamp = timestamp

class MockConversation:
    def __init__(self, messages):
        self.messages = MagicMock()
        self.messages.filter.return_value.order_by.return_value = messages

def test_extraction():
    agent = AIAgent(user=MagicMock())
    
    # Simulate history: User asked for standup meetings, AI listed them.
    history = [
        MockMessage('user', "Find my standup meetings throughout the year 2025", 1),
        MockMessage('agent', "Here are your standup meetings for 2025...", 2)
    ]
    
    conversation = MockConversation(history)
    
    # Current request
    current_text = "Can you show me the days I have Bible study and miracle hour"
    
    print(f"Testing extraction with history...")
    print(f"History: {[m.text for m in history]}")
    print(f"Current: {current_text}")
    
    # We need to mock _get_claude_chat_response to actually call the API or simulate the failure.
    # Since I cannot call the real API easily without credentials/environment, 
    # I will rely on the fact that I can modify the prompt and see if the logic holds.
    # BUT, to actually reproduce it, I would need the real LLM.
    # Since I can't do that, I will assume the hypothesis is correct based on logs and proceed to fix it.
    # However, I can use this script to verify that my prompt CHANGES are syntactically correct 
    # and that the history is indeed passed as I expect.
    
    # Actually, I can't run the real LLM here. 
    # So I will skip the actual execution of this script and move to fixing the prompt directly.
    # The logs are conclusive enough: The AI received the history and the new message, 
    # and outputted the OLD query. This is a classic "stuck in context" issue.
    
    pass

if __name__ == "__main__":
    test_extraction()
