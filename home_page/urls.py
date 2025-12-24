from django.urls import path
from . import views

app_name = "home_page"

urlpatterns = [
    # AI assistant URLs 
    path("assistant/", views.assistant, name="assistant"), # Handles GET for initial load
    path("assistant/<uuid:convo_id>/", views.assistant, name="assistant"), # Handles GET for existing convos and POST for chat (handled by JS POSTing to chat_process)
    path("assistant/new/", views.assistant, {'is_placeholder': True}, name="new_conversation"), # Shows placeholder state without creating conversation
    path("chat/process/", views.chat_process, name="chat_process"), # for posting chat messages from the frontend
    path("assistant/delete_conversation/<uuid:convo_id>/", views.delete_conversation, name='delete_conversation'),
    path("connect/google/", views.connect_google, name="connect_google"),
    path("settings/", views.settings_view, name="settings"),
    path("whatsapp/reply/", views.whatsapp_reply, name="whatsapp_reply")
]