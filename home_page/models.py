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

class NotificationPreference(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="notification_preference")
    whatsapp_number = models.CharField(max_length=20, blank=True, null=True, help_text="e.g. +14155238886")
    whatsapp_enabled = models.BooleanField(default=False)
    email_enabled = models.BooleanField(default=True)
    
    # New Fields for Reminder Configuration
    reminder_lead_time = models.IntegerField(default=30, help_text="Minutes before event to remind")
    morning_briefing_enabled = models.BooleanField(default=True)
    morning_briefing_time = models.TimeField(default="08:00")
    
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Prefs for {self.user.username}"

class SentNotification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_notifications")
    event_id = models.CharField(max_length=255)
    notification_type = models.CharField(max_length=10, choices=[('email', 'Email'), ('whatsapp', 'WhatsApp')])
    # New Fields for Retry Logic
    failure_count = models.IntegerField(default=0)
    status = models.CharField(max_length=10, choices=[('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed')], default='sent')
    
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['event_id', 'notification_type', 'user']),
        ]

    def __str__(self):
        return f"{self.notification_type} for event {self.event_id} to {self.user.username}"