from django.db import models
from django.contrib.auth import get_user_model
import uuid

User = get_user_model()

# Create your models here.
class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="conversations")
    title = models.CharField(max_length=120, default="New Chat")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title
    
class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.CharField(max_length=10, choices=[('user', 'User'), ('agent', 'Agent')])
    # Allow empty text so we can store structured messages (e.g., event cards)
    text = models.TextField(blank=True, default='')
    # Persist the semantic type of the message for proper rehydration on reload
    message_type = models.CharField(max_length=40, default='text')
    # Optional JSON payload for structured content
    content = models.JSONField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        kind = getattr(self, 'message_type', 'text') or 'text'
        preview = (self.text or '').strip()[:30]
        return f"{kind} from {self.sender} at {self.timestamp}: {preview}"