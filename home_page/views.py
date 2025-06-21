from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse, Http404
from .services.calendar_service import GoogleCalendarService
from .services.ai_agent import AIAgent
from allauth.socialaccount.models import SocialToken
from django.contrib import messages
from .models import Conversation, Message
import json
import uuid
import os
from django.conf import settings
import traceback
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
import logging
# Create your views here.


logger = logging.getLogger(__name__)


@login_required
def assistant(request, convo_id=None, is_placeholder=False):
    user = request.user
    # Fetch conversations ordered by creation date, newest first
    conversations = Conversation.objects.filter(user=user).order_by('-created_at')

    # is_new_conversation_page determines if the frontend should animate the welcome message
    is_new_conversation_page = False
    convo = None # Initialize current conversation object
    messages_to_render = [] # Initialize messages list to pass to template
    welcome_message_for_frontend = None # Initialize welcome message text for frontend

    # Define the welcome message text (can be defined once outside the view function if preferred)
    welcome_message_text_content = "Hi! I'm your professional calendar assistant. I can help you manage your schedule, create and update events, find optimal meeting times, and provide scheduling suggestions. What would you like me to help you with today?"


    # Handle GET requests (loading the page)
    if request.method == "GET":
        if is_placeholder:
            # Logic for the new chat placeholder state
            print("GET request for new chat placeholder. Showing initial empty state.")
            is_new_conversation_page = True # set flag for frontend animation
            convo = None
            messages_to_render = []
            welcome_message_for_frontend = welcome_message_text_content # pass welcome message text
            
            # Add a placeholder to the conversations list for frontend rendering
            class PlaceholderConvo:
                id = "placeholder"
                title = "New Chat"
            conversations = list(conversations)
            if not any(getattr(c, 'id', None) == "placeholder" for c in conversations):
                conversations.insert(0, PlaceholderConvo())
            
        elif convo_id:
            # --- Logic for loading an existing conversation (by ID in URL) ---
            try:
                # Get the conversation object, including the user for the ownership check
                convo = get_object_or_404(Conversation.objects.select_related('user'), id=convo_id, user=user)
                print(f"GET request for conversation ID: {convo.id}. Loading existing conversation.")

                # Fetch messages for this conversation
                messages_to_render = list(convo.messages.filter(text__isnull=False, text__gt='').order_by('timestamp'))

                # if an existing conversation is loaded, it's not a new chat state for animation
                is_new_conversation_page = False

                # If the fetched conversation has no messages, this is a newly created empty chat.
                # Flag it as a new conversation for frontend welcome message animation.
                if len(messages_to_render) == 0:
                    # is_new_conversation_page = True
                    print(f"Conversation {convo.id} has no messages. Setting is_new_conversation_page = True for frontend animation.")
                    # Pass the welcome message text to the frontend context for animation
                    # welcome_message_for_frontend = welcome_message_text_content
                    # The frontend JS will look for is_new_conversation_page=True and handle the welcome message display and animation.
                    # We do NOT add a backend welcome message here anymore.

                else:
                    print(f"GET request for existing conversation with {len(messages_to_render)} messages.")


            except (ValueError, uuid.UUID, Http404): # Catch errors for invalid UUID format or non-existent UUID
                print(f"GET request with invalid or non-existent convo ID: {convo_id}. Redirecting to latest or initial state.")
                messages.error(request, "Invalid or non-existent conversation ID.")
                # Attempt to redirect to the latest conversation if one exists
                latest_convo = conversations.first()
                if latest_convo:
                     # Redirect to the assistant view with the latest conversation's ID
                     return redirect('home_page:assistant', convo_id=latest_convo.id)
                else:
                     # If no latest convo, fall through to render the initial empty state
                     #convo_id = None # Set convo_id to None to trigger the next block
                     print("No existing convos to redirect to. Redirecting to new conversation placeholder")
        
        else:
            # handles the base /agent/assistant/ URL without an ID
            if conversations.exists():
                latest_convo = conversations.first()
                print("GET request to base URL with existing convos. Redirecting to latest.")
                return redirect('home_page:assistant', convo_id=latest_convo.id)
            else:
                print("GET request to base URL with no existing convos. Redirecting to new placeholder.")
                return redirect('home_page:new_conversation_placeholder')


        # --- Logic for the true initial empty state (no convo_id in URL AND no existing conversations) ---
        # This block handles the very first time a user visits the page and has no conversations at all.
        # In this case, there is no convo_id in the URL.
        # if not convo_id and not conversations.exists():
        #     print("GET request, no ID in URL, no existing conversations. Showing initial empty state.")
        #     is_new_conversation_page = True # Set this to True for the very first visit
        #     convo = None # Ensure current convo is None
        #     messages_to_render = [] # Ensure no messages are rendered initially
        #     # Pass the welcome message text to the frontend context for animation
        #     welcome_message_for_frontend = welcome_message_text_content

        # Note: If there are existing conversations but no convo_id in the URL (e.g., user manually goes to /agent/assistant/),
        # the page will render with the recents list but no selected conversation in the chat panel.
        # is_new_conversation_page will be False in this scenario.
        # The frontend might need adjustments to handle this state gracefully (e.g., display a message "Select a conversation or start a new one").


    # If it's a POST request to this view, it's an error as POSTs should go to chat_process
    if request.method == "POST":
        raise Http404("POST requests to /agent/assistant/ are not allowed. Use /agent/chat/process/.")


    # Prepare the context data to pass to the template
    context = {
        "conversations": conversations, # List of all recent conversations
        "current_convo": convo, # The currently selected conversation object (or None)
        "messages": messages_to_render, # Messages for the current_convo (or empty list)
        "is_new_conversation_page": is_new_conversation_page, # Flag for frontend animation
        # Pass welcome message text only when the flag is True
        "welcome_message_text": welcome_message_for_frontend if is_new_conversation_page else None,
        "google_calendar_icon_url": os.path.join(settings.STATIC_URL, 'home_page/images/google_calendar_icon.svg') # Assuming this is needed
    }

    print(f"Rendering assistant.html with is_new_conversation_page={is_new_conversation_page}, {len(messages_to_render)} messages, current_convo={convo.id if convo else 'None'}.")
    return render(request, "home_page/assistant.html", context)


@login_required
def new_conversation(request):
    # This view is called when the user clicks "+ New Task".
    # It creates a new blank conversation immediately and redirects to the assistant view with its ID.
    # This makes the "New Chat" appear in the recents list right away.
    # The assistant view will fetch this empty convo and trigger frontend animation.
    # convo = Conversation.objects.create(user=request.user, title="New Chat") # Create the new conversation immediately
    print(f"New conversation view called. Redirecting to new placeholder state.")
    # Redirect to the assistant view with the new convo ID.
    return redirect("home_page:new_conversation_placeholder")


@csrf_exempt # <--- Add this decorator temporarily for testing JSON post (remove in production and handle CSRF properly)
# Or better, handle CSRF token check manually if not using CsrfViewMiddleware globally
# Or ensure CsrfViewMiddleware is active and JS sends the token in header (as done above)
def chat_process(request):
    # Ensure it's a POST request
    if request.method != "POST":
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    try:
        # Load JSON data from the request body
        # Ensure CsrfViewMiddleware is active OR add @csrf_exempt for testing
        data = json.loads(request.body)
        user_input = data.get("message", "").strip()
        convo_id = data.get("convo_id") # Get convo_id from JSON data

        if not user_input:
             # Handle empty message appropriately, maybe return existing messages or an error
             return JsonResponse({'error': 'Empty message received'}, status=400)

        user = request.user
        convo = None

        # ... (rest of your chat_process logic for handling convo_id,
        # fetching/creating conversation, creating user message) ...

        if convo_id:
            try:
                convo = get_object_or_404(Conversation, id=convo_id, user=user)
            except (ValueError, uuid.UUID):
                 return JsonResponse({'error': 'Invalid conversation ID'}, status=400)
            except Http404:
                 return JsonResponse({'error': 'Conversation not found'}, status=404)
        else:
             # Create a new conversation if no convo_id is provided
            convo = Conversation.objects.create(user=user, title="New Chat")
            # Mark as first message if it's the first in this newly created convo
            # is_first_actual_message = True # This logic needs adjustment if convo is new here

        # Now, message history logic needs the actual convo object
        is_first_actual_message = convo.messages.count() == 0 if convo else True # Check count if convo exists
        user_message = Message.objects.create(conversation=convo, sender='user', text=user_input)

        # Get agent response using AIAgent
        # Pass the convo object to the AIAgent handle method
        ai_agent = AIAgent(user)
        result = ai_agent.handle(user_input, conversation=convo) # <--- Pass convo object

        agent_response_text = result.get("response") # Assuming 'response' key for text
        response_type = result.get("type", "text") # Get the type, default to text
        response_content = result.get("content", {}) # Get content for calendar actions

        if agent_response_text and agent_response_text.strip():
            # Create agent message in DB only for 'text' type responses or
            # the message_for_user part of calendar actions
            if response_type == 'text' or (response_type == 'calendar_action_request' and response_content.get('message_for_user')):
                 Message.objects.create(conversation=convo, sender='agent', text=agent_response_text)

        # Generate title for first message
        if is_first_actual_message and convo.title == "New Chat": # Check if title is default "New Chat"
             # Use the AIAgent to generate the title
             title_result = ai_agent.handle(f"Generate a very short and concise title (max 5 words) for a chat based on the user message: '{user_input}'. Only provide the title text.", is_title_generation=True) # <--- Use the agent for title generation
             new_title = title_result.get("response") # Assuming title generation returns type 'text' and key 'response'

             if new_title:
                 new_title = new_title.strip().strip('"').strip("'")
                 if new_title:
                     convo.title = new_title[:120]
                     convo.save()
             # If title generation fails or is empty, keep the default or use user input snippet
             if convo.title == "New Chat":
                 convo.title = user_input[:40] + "..." if len(user_input) > 40 else user_input or "New Chat"
                 convo.save()


        # Prepare the JSON response for the frontend
        response_data = {
            'type': response_type, # Include the response type
            'response': agent_response_text, # Main text response
            'content': response_content, # Additional content for calendar actions etc.
            'convo_id': str(convo.id),
            'convo_title': convo.title,
            'user_message_text': user_input,
            # Don't send the agent message text back here, the frontend JS
            # will display based on 'type' and 'response'/'content'
            # 'agent_message_text': agent_response_text, # Remove this
            'is_first_actual_message': is_first_actual_message,
        }

        # If it's a calendar action request needing connection, add the connect URL
        if response_type == 'calendar_action_request' and response_content.get('needs_connection'):
             # You need a way to get the Google auth connect URL here
             # This might involve django-allauth's flow or a custom URL
             # Example (conceptual):
             # from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
             # from allauth.socialaccount.providers.oauth2.views import OAuth2LoginView
             # connect_url = reverse('google_connect') # Assuming you have a URL named 'google_connect' for the connect flow
             # response_data['content']['connect_url'] = connect_url

             # A simpler way for now is to hardcode or generate the connect URL here if possible
             # Based on logs, it looks like the connect URL pattern is /accounts/google/login/?process=connect&next=/agent/assistant/convo_id/
             # Need the current conversation ID in the 'next' parameter
             next_url = reverse('home_page:assistant', args=[convo.id]) # URL after successful connection
             connect_url = reverse('google_login') + f'?process=connect&next={next_url}' # Use reverse for the base login URL
             response_data['content']['connect_url'] = connect_url


        return JsonResponse(response_data)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON received'}, status=400)
    except Exception as e:
        print(f"Error in chat_process: {e}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': f'An internal server error occurred: {e}'}, status=500)


@login_required
@require_POST
def delete_conversation(request, convo_id:uuid.UUID):
    user = request.user
    conversation = get_object_or_404(Conversation, id=convo_id, user=user)

    try:
        conversation.delete()
        logger.info(f"User {user.username} deleted conversation {convo_id}")

        # Find the latest conversation after deletion
        remaining_conversations = Conversation.objects.filter(user=user).order_by('-created_at')

        if remaining_conversations.exists():
             # Redirect to the latest conversation
             latest_convo = remaining_conversations.first()
             print(f"Deleted convo {convo_id}. Redirecting to latest remaining convo {latest_convo.id}.")
             return JsonResponse({'success': True, 'redirect_url': reverse('home_page:assistant', args=[latest_convo.id])})
        else:
             # If no conversations remain, redirect to the initial empty state (no convo ID)
             print(f"Deleted convo {convo_id}. No convos remaining. Redirecting to initial empty state.")
             # Redirect to the base assistant URL
             return JsonResponse({'success': True, 'redirect_url': reverse('home_page:assistant')})


    except Exception as e:
        logger.error(f"Error deleting conversation {convo_id} for user {user.username}: {e}", exc_info=True) # log error
        return JsonResponse({'success': False, 'error': 'Error deleting conversation.'}, status=500)