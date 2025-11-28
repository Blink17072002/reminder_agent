import os
import django
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')
django.setup()

from home_page.services.ai_agent import AIAgent
from django.contrib.auth import get_user_model

User = get_user_model()
# Assuming a user exists, or create one
user = User.objects.first()
if not user:
    print("No user found, creating one.")
    user = User.objects.create(username='testuser', email='test@example.com')

agent = AIAgent(user)

# Test case
user_input = "Delete the test meeting."
print(f"Testing input: '{user_input}'")

# 1. Check Intent
intent = agent.determine_intent(user_input)
print(f"Determined Intent: {intent}")

# 2. Check Handle Result
# Mocking conversation as None for simplicity
result = agent.handle(user_input, conversation=None)
print(f"Handle Result Type: {result.get('type')}")
print(f"Handle Result Content: {result.get('content')}")
print(f"Handle Result Response: {result.get('response')}")
