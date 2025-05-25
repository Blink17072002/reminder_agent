from django.contrib import admin
from .models import Conversation, Message
# Register your models here.

class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ('sender', 'text', 'timestamp')

@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'title', 'created_at')
    inlines = [MessageInline]
    search_fields =('title', 'user__username')

@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'conversation', 'sender', 'timestamp')
    search_fields = ('text',)
    list_filter = ('sender',)