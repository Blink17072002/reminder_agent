from django.urls import path
from . import views

app_name = "home_page"

urlpatterns = [
    # AI assistant URLs 
    path("assistant/", views.assistant, name="assistant"), # Handles GET for initial load
    path("assistant/<uuid:convo_id>/", views.assistant, name="assistant"), # Handles GET for existing convos and POST for chat (handled by JS POSTing to chat_process)
    path("assistant/new-placeholder/", views.assistant, {'is_placeholder':True}, name="new_conversation_placeholder"),
    path("assistant/new/", views.new_conversation, name="new_conversation"), # Handles creating a new convo and redirecting
    path("chat/process/", views.chat_process, name="chat_process"), # for posting chat messages from the frontend
    path("assistant/delete_conversation/<uuid:convo_id>/", views.delete_conversation, name='delete_conversation'),
]