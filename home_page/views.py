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
# Create your views here.

@login_required
def calendars(request):
    try:
        svc = GoogleCalendarService(request.user)
    except SocialToken.DoesNotExist:
        messages.info(request, "Please connect your Google account to view calendars.")
        return redirect('home_page:assistant')
    items = svc.list_calendars()
    context = {"calendars": items}

    return render(request, "home_page/calendars.html", context)

@login_required
def events(request, cal_id="primary"):
    items = GoogleCalendarService(request.user).list_events(calendar_id=cal_id)
    return render(request, "home_page/events.html")

@login_required
def create_event(request, cal_id="primary"):
    svc = GoogleCalendarService(request.user)
    if request.method == "POST":
        event = {  # builds an `event` dict from POSTed form fields
            "summary":   request.POST["summary"],
            "start":     {"dateTime": request.POST["start"]},
            "end":       {"dateTime": request.POST["end"]},
            "attendees": [{"email": e.strip()} for e in request.POST.get("attendees","").split(",") if e],
        }
        svc.create_event(cal_id, event)  # calls the service to create it on Google Calendar
        return redirect("home_page:events", cal_id=cal_id)   # redirects back to the events list for this calendar
    context = {"calendar_id": cal_id}

    return render(request, "home_page/create_event.html", context)

@login_required
def assistant(request, convo_id=None):
    user = request.user
    conversations = Conversation.objects.filter(user=user).order_by('-created_at')

    # Determine if this is a GET request for a *new* chat session (no convos or explicit new convo)
    is_new_conversation_page = False
    convo = None # Initialize convo as None for clarity in GET logic below
    messages_to_render = [] # Initialize messages list to pass to template

    if request.method == "POST":
        user_input = request.POST.get("message", "")
        print(f"POST request received. convo_id: {convo_id}, user_input: '{user_input[:50]}...'") # Debug POST start

        # Handle empty message submission on POST (this is primarily for POSTs with convo_id)
        if not user_input.strip():
            print(f"POST received with empty message. convo_id: {convo_id}")
            if convo_id:
                convo = get_object_or_404(Conversation, id=convo_id, user=user)
                messages_to_render = list(convo.messages.all()) # Fetch messages for display
            else: # Should ideally not be hit with current JS flow for empty messages
                 print("Warning: Empty POST received without convo_id. Falling back to latest convo or empty.")
                 convo = conversations.first() # Try to find the latest
                 messages_to_render = list(convo.messages.all()) if convo else []

            # Determine if this empty POST is effectively on a new page (no actual messages yet)
            if convo and convo.messages.count() == 0: # Check actual saved messages
                 is_new_conversation_page = True

            context = {"conversations": conversations, "current_convo": convo, "messages": messages_to_render, "is_new_conversation_page": is_new_conversation_page}
            print(f"Rendering assistant.html after empty POST. is_new_conversation_page: {is_new_conversation_page}")
            return render(request, "home_page/assistant.html", context)

        # --- START: Handle non-empty POST ---
        print(f"Non-empty POST received. convo_id: {convo_id}")
        # Get the current conversation for a non-empty POST
        if convo_id:
            convo = get_object_or_404(Conversation, id=convo_id, user=user)
            print(f"Using existing conversation with ID: {convo.id} for POST.")
        else:
             # This is the crucial case: First message from /agent/assistant/
             print("POST received without convo_id. This is the first message. Creating a new conversation.")
             convo = Conversation.objects.create(user=user, title="New Chat") # Temporary title
             print(f"Created new conversation with ID: {convo.id} for this first POST.")

        # Check if this is the first *actual* user message in this conversation
        # (excluding the potential temporary welcome message rendered by the template)
        # We check database messages count *before* adding the new user message
        is_first_actual_message = convo.messages.count() == 0
        if is_first_actual_message:
             print("This POST contains the first actual user message for this conversation.")

        # save user message
        Message.objects.create(conversation=convo, sender='user', text=user_input)
        print(f"Saved user message to conversation {convo.id}.")

        # get agent response
        ai_agent = AIAgent(user)
        result = ai_agent.handle(user_input)
        agent_text = result.get("response") or result.get("error") or ""

        # Only save agent message if it's not empty after AI processing
        if agent_text and agent_text.strip(): # Check for None/empty string
            Message.objects.create(conversation=convo, sender='agent', text=agent_text)
            print(f"Saved agent message to conversation {convo.id}.")
        else:
             print(f"Agent response for convo {convo.id} was empty after AI processing, not saving.") # Debug print if agent replies empty
             agent_text = None # Ensure agent_text is None if empty/whitespace

        print(f"Agent response result: {json.dumps(result, indent=2)}")

        # --- START: AI Title Generation ---
        # Generate title only if it's the very first actual user message AND agent responded
        if is_first_actual_message and agent_text: # Check agent_text is not None/empty
            print("Attempting AI title generation (first actual message, agent responded)...")
            title_prompt = f"Based on this first message exchange, generate a very short and concise title (max 5 words) for the conversation. Only provide the title text.\nUser: {user_input}\nAgent: {agent_text}"
            print(f"Title prompt: {title_prompt}")
            # IMPORTANT: Call AI agent with a separate instance or ensure state is clean
            # (AIAgent is stateless based on __init__, so this should be fine)
            title_agent = AIAgent(user)
            title_result = title_agent.handle(title_prompt, is_title_generation=True) # Use separate instance or call handle again
            print(f"AI title agent result: {json.dumps(title_result, indent=2)}")
            new_title = title_result.get("response")

            if new_title:
                new_title = new_title.strip().strip('"')
                print(f"AI generated title (stripped): \"{new_title}\"")
                if new_title: # Ensure title is not empty after stripping
                    convo.title = new_title[:120] # Cap at model field max_length
                    convo.save()
                    print(f"Conversation {convo.id} title updated and saved to: \"{convo.title}\".")
                else:
                    print("AI generated title was empty after stripping (first message). Using fallback.")
                    # Fallback to user input start if AI provided empty/just quotes
                    convo.title = user_input[:40] or "New Chat"
                    convo.save()
                    print(f"Conversation {convo.id} title updated (fallback on empty AI title) and saved to: \"{convo.title}\".")
            else:
                print("AI did not return a title in 'summary' for the first message. Using fallback.")
                # Fallback to user input start if AI fails
                convo.title = user_input[:40] or "New Chat"
                convo.save()
                print(f"Conversation {convo.id} title updated (fallback on AI failure) and saved to: {convo.title}.")

        elif is_first_actual_message: # If it's the first message but agent failed (or no agent_text)
             print("AI agent failed or returned no text for initial response on first message. Using fallback title.")
             convo.title = user_input[:40] or "New Chat"
             convo.save()
             print(f"Conversation {convo.id} title updated (fallback on initial agent failure/no text) and saved to: {convo.title}.")
        # --- END: AI Title Generation ---

        # Prepare response data - always send the latest state of the conversation
        response_data = {
            'summary': result.get("summary"), # Pass agent summary if available
            'result': result.get("result"),   # Pass agent result if available
            'error': result.get("error"),     # Pass agent error if available
            'convo_id': str(convo.id),        # Always send the UUID of the conversation
            'convo_title': convo.title,       # Always send the current title
            # We might need to send the new message data back too for JS to append
            'user_message_text': user_input,                       # Send user message text back
            'agent_message_text': agent_text,                       # Send agent response text back (can be None)
            'is_first_actual_message': is_first_actual_message, # Tell JS if this was the first message
        }

        print(f"Sending JSON response for convo {convo.id} with title: \"{convo.title}\", agent_text_present: {bool(agent_text)}, is_first_actual_message: {is_first_actual_message}.")
        return JsonResponse(response_data)
    # --- END: Handle non-empty POST ---

    # --- START: GET request logic (initial page load or clicking recent convo) ---
    # If a convo_id is provided in the URL
    if convo_id:
        try:
            convo = get_object_or_404(Conversation, id=convo_id, user=user)
            print(f"GET request for conversation ID: {convo.id}.")
            messages_to_render = list(convo.messages.all().order_by('timestamp')) # Convert queryset to list and order

            # Check if this is an explicit new conversation page load via /assistant/new/ redirect
            # and it has no messages yet.
            if len(messages_to_render) == 0:
                is_new_conversation_page = True
                print("GET request for empty conversation (likely from /new/). Adding temporary welcome message.")
                # Create and add temporary welcome message
                welcome_message_text = "Hi! I'm your professional calendar assistant. I can help you manage your schedule, create and update events, find optimal meeting times, and provide scheduling suggestions. What would you like me to help you with today?"
                # Temporary message object. No need for a conversation link for this temporary one.
                temp_welcome_message = Message(sender='agent', text=welcome_message_text, conversation=None) # convo=None is okay for temp
                messages_to_render = [temp_welcome_message] # Pass only the welcome message to the template
                print("Added temporary welcome message for empty conversation.")

            else:
                 print(f"GET request for existing conversation with {len(messages_to_render)} messages.")


        except (ValueError, uuid.UUID): # Catch error if convo_id is not a valid UUID format
            print(f"GET request with invalid convo_id format: {convo_id}. Redirecting to latest or new.")
            messages.error(request, "Invalid conversation ID.")
            convo = conversations.first() # Try to get the latest valid conversation
            if convo:
                 return redirect('home_page:assistant', convo_id=convo.id)
            else:
                 # If no valid convos, fall through to the 'else' block below to handle empty state
                 convo_id = None # Clear convo_id to trigger the empty state logic
                 print("Invalid convo_id and no existing convos. Falling through to empty state logic.")
        except Http404: # Also catch Http404 if a valid UUID doesn't match a conversation
             print(f"GET request for non-existent convo ID: {convo_id}. Redirecting to latest or new.")
             messages.error(request, "Conversation not found.")
             convo = conversations.first()
             if convo:
                  return redirect('home_page:assistant', convo_id=convo.id)
             else:
                  convo_id = None # Fall through
                  print("Non-existent convo ID and no existing convos. Falling through to empty state logic.")


    # If no convo_id in URL (either initially, or after redirect from invalid ID)
    # and no existing conversations found for the user
    if not convo_id and not convo: # Ensure convo is also None here
        print("GET request, no ID in URL, no conversations found. Showing empty state with welcome message.")
        is_new_conversation_page = True
        convo = None # Still no current convo object yet for template context

        # --- START: Welcome Message Logic for Initial Empty State ---
        welcome_message_text = "Hi! I'm your professional calendar assistant. I can help you manage your schedule, create and update events, find optimal meeting times, and provide scheduling suggestions. What would you like me to help you with today?"
        # Create a *temporary* message object. No convo yet to associate, which is fine for rendering.
        temp_welcome_message = Message(sender='agent', text=welcome_message_text, conversation=None) # Associate temporarily, convo=None is okay for temp
        messages_to_render = [temp_welcome_message] # Pass the welcome message to the template
        print("Added temporary welcome message for initial empty state.")
        # --- END: Welcome Message Logic for Initial Empty State ---

    # Add is_new_conversation_page flag and messages_to_render to context
    # Ensure current_convo is in context, even if None for the true empty state
    context = {
        "conversations": conversations,
        "current_convo": convo, # This will be a Conversation object or None
        "messages": messages_to_render, # This will contain Message objects (real or temp)
        "is_new_conversation_page": is_new_conversation_page
    }

    print(f"Rendering assistant.html with is_new_conversation_page={is_new_conversation_page}, {len(messages_to_render)} messages.")
    return render(request, "home_page/assistant.html", context)
# --- END: GET request logic ---


@login_required
@require_POST # Ensure this view only accepts POST requests
def chat_process(request): # Handles POST requests only from JS
    # This view handles POST requests for sending messages
    try:
        # Get JSON data from the request body
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)


        message_text = data.get('message')
        conversation_id_str = data.get('conversation_id') # Get UUID from POST data body

        if not message_text or not message_text.strip():
            print("POST received with empty message.")
            # Returning a success True but no messages tells JS to just clear input.
            return JsonResponse({'success': True, 'message_sent': False})


        conversation = None
        is_first_actual_message = False

        # --- START: Find or Create Conversation ---
        if conversation_id_str:
            try:
                # Get the existing conversation using the UUID from the body
                convo = get_object_or_404(Conversation, id=conversation_id_str, user=request.user)
                print(f"Using existing conversation with ID: {convo.id} for POST.")
                # Check if this is the first *actual* message in this conversation (before adding the new user message)
                if convo.messages.count() == 0:
                     is_first_actual_message = True
                     print("This POST is the first actual message for existing conversation (it was empty).")

            except (ValueError, Http404):
                 # If UUID is invalid format or not found for this user, treat as needing a new conversation
                 print(f"POST received with invalid or non-existent convo ID '{conversation_id_str}'. Creating a new conversation.")
                 convo = Conversation.objects.create(user=request.user, title="New Chat") # Create new convo with temp title
                 is_first_actual_message = True # It's the first message of this *new* conversation
                 conversation_id_str = str(convo.id) # Get the new UUID string

        # If no conversation_id was provided in the POST body, create a new one
        if not conversation:
            print("POST received without convo_id in body. Creating a new conversation.")
            convo = Conversation.objects.create(user=request.user, title="New Chat") # Create new convo with temp title
            is_first_actual_message = True # It's the first message of this *new* conversation
            conversation_id_str = str(convo.id) # Get the new UUID string
        # --- END: Find or Create Conversation ---


        # Create the user message and save it
        Message.objects.create(
            conversation=convo,
            sender='user',
            text=message_text.strip() # Save stripped text
        )
        print(f"Saved user message to conversation {convo.id}.")

        # Initialize AI Agent for the current user
        agent = AIAgent(request.user)

        # Process the message through the AI Agent
        # The agent's response is now a structured dictionary
        agent_response_data = agent.handle(message_text.strip(), conversation=convo) # Pass conversation for history

        # --- START: Save Agent's Response (Simplified Log for Structured Types) ---
        # Save the agent's response to the database.
        # For 'text' responses, save the text content.
        # For structured types, save a simplified log/placeholder for history.
        saved_agent_message_content = None # What gets saved in the DB
        response_type = agent_response_data.get('type', 'text') # Default to text if type is missing

        if response_type == 'text':
            saved_agent_message_content = agent_response_data.get('response')
            if saved_agent_message_content and saved_agent_message_content.strip():
                Message.objects.create(
                    conversation=convo,
                    sender='agent',
                    text=saved_agent_message_content.strip() # Save stripped text
                )
                print(f"Saved agent text response to conversation {conversation.id}.")
            else:
                 print(f"Agent returned empty text response for convo {conversation.id}. Not saving as message.")
                 agent_response_data['response'] = "" # Ensure frontend gets empty string if None/whitespace

        elif response_type == 'needs_connection':
             # Save a log entry indicating connection was needed
             saved_agent_message_content = "Agent action: Requested Google account connection."
             # Optionally save details if needed: agent_response_data.get('content', {})
             Message.objects.create(
                conversation=conversation,
                sender='agent',
                text=saved_agent_message_content # Save the log entry
             )
             print(f"Saved log entry for 'needs_connection' for convo {conversation.id}.")

        elif response_type == 'event_success':
             # Save a log entry indicating event creation success
             event_title = agent_response_data.get('content', {}).get('event_title', 'An event')
             saved_agent_message_content = f"Agent action: Created event - {event_title}."
              # Optionally save details if needed: agent_response_data.get('content', {})
             Message.objects.create(
                conversation=conversation,
                sender='agent',
                text=saved_agent_message_content # Save the log entry
             )
             print(f"Saved log entry for 'event_success' for convo {conversation.id}.")

        # Add elif for other structured types (event_failed, thinking, etc.)
        # Decoupling DB message content from frontend rendering allows flexibility.
        # The frontend uses agent_response_data['type'] and ['content'] for rendering.
        # --- END: Save Agent's Response ---


        # Update conversation timestamp (done automatically if auto_now=True)
        # conversation.save() # This line might not be needed if auto_now=True on updated_at


        # --- START: AI Title Generation (only for the very first actual message exchange) ---
        # Generate title only if it's the very first actual user message
        # AND the agent provided *any* kind of meaningful response (text or structured action)
        # Check if agent_response_data indicates a non-empty or actionable response
        has_meaningful_agent_response = (
            (response_type == 'text' and agent_response_data.get('response') and agent_response_data.get('response').strip()) or
            (response_type in ['needs_connection', 'event_success', 'event_failed']) # Add other action types
        )


        if is_first_actual_message and has_meaningful_agent_response:
            print("Attempting AI title generation (first actual message, meaningful agent response)...")
            # Use the AI agent to generate a title based on the first exchange
            # Provide both user message and the agent's text response *if* it was a text response.
            # If agent response was structured, describe the action taken for context.
            agent_context_for_title = ""
            if response_type == 'text':
                 agent_context_for_title = agent_response_data.get('response', '').strip()[:100] # Limit agent text context
            elif response_type == 'needs_connection':
                 agent_context_for_title = "Agent asked user to connect Google Account."
            elif response_type == 'event_success':
                 event_title = agent_response_data.get('content', {}).get('event_title', 'an event')
                 agent_context_for_title = f"Agent confirmed creating event: {event_title}."
            # Add context for other action types...

            title_prompt_text = f"User: {message_text.strip()}"
            if agent_context_for_title:
                 title_prompt_text += f"\nAgent: {agent_context_for_title}"

            title_prompt = f"Based on the following conversation start, generate a very short and concise title (max 7 words) for the conversation. Only provide the title text.\n{title_prompt_text}"

            print(f"Title prompt: {title_prompt}")
            # Call AI agent for title generation
            try:
                # Use the AI agent instance, call handle with the title prompt
                 title_result = agent.handle(title_prompt, conversation=None, is_title_generation=True) # Pass a flag or context
                 # Assuming handle returns {'type': 'text', 'response': 'Generated Title'} for this prompt
                 ai_generated_title = title_result.get('response')

                 if ai_generated_title:
                    # Clean up potential leading/trailing quotes or markdown from AI
                    new_title = ai_generated_title.strip().strip('"').strip("'")
                    print(f"AI generated title (cleaned): \"{new_title}\"")

                    if new_title: # Ensure title is not empty after stripping
                        convo.title = new_title[:120] # Cap at model field max_length
                        convo.save()
                        print(f"Conversation {convo.id} title updated and saved to: \"{convo.title}\".")
                    else:
                        print("AI generated title was empty after stripping (first message). Using fallback.")
                        # Fallback to user input start if AI provided empty/just quotes
                        convo.title = message_text.strip()[:40] or "New Chat"
                        convo.save()
                        print(f"Conversation {convo.id} title updated (fallback on empty AI title) and saved to: \"{convo.title}\".")
                 else:
                    print("AI did not return a title in 'response' for the first message. Using fallback.")
                    # Fallback to user input start if AI fails to give any response text
                    convo.title = message_text.strip()[:40] or "New Chat"
                    convo.save()
                    print(f"Conversation {convo.id} title updated (fallback on AI failure) and saved to: {convo.title}.")

            except Exception as e:
                 print(f"Error during AI title generation: {e}")
                 # Fallback title on error
                 convo.title = message_text.strip()[:40] or "New Chat"
                 convo.save()
                 print(f"Conversation {convo.id} title updated (fallback on error) and saved to: {convo.title}.")

             # Include the generated/fallback title in the JSON response
            agent_response_data['title'] = convo.title

        elif convo.title: # For existing convos, ensure current title is sent
             agent_response_data['title'] = convo.title
        else:
              agent_response_data['title'] = "New Chat"


        # Prepare the JSON response
        response_data = {
            'conversation_id': str(convo.id),        # Always send the UUID string
            'is_first_actual_message': is_first_actual_message, # Tell JS if this was the first message
            'agent_response_data': agent_response_data, # Include the agent's full structured response
        }

        # Include the generated/updated title if it exists in agent_response_data (added in title logic)
        if 'title' in agent_response_data:
             response_data['title'] = agent_response_data['title']
             print(f"Including updated title in JSON: \"{response_data['title']}\"")
        elif convo.title: # Fallback to current conversation title if not explicitly set in agent_response_data
             response_data['title'] = convo.title
             print(f"Including current conversation title in JSON: \"{response_data['title']}\"")
        else:
             response_data['title'] = "New Chat" # Final fallback title for JSON


        print(f"Sending JSON response for convo {convo.id}. Response data structure: {json.dumps(response_data, indent=2)}")
        return JsonResponse(response_data)

    except Exception as e:
        print(f"Error in chat_process view: {e}")
        import traceback
        traceback.print_exc() # Print full traceback for debugging
        # Return a structured error response
        return JsonResponse({
            'conversation_id': conversation_id_str or None, # Send back the ID if we had one
            'error': 'An internal server error occurred.',
            'agent_response_data': { # Send a simple text error response structure
                 'type': 'text',
                 'response': 'Sorry, something went wrong on the server. Please try again.'
            }
        }, status=500)

@login_required
def new_conversation(request):
    # This view is simple - just create a new blank convo and redirect
    # The assistant view will handle displaying the welcome message because messages.count() will be 0
    convo = Conversation.objects.create(user=request.user, title="New Chat") # Temporary title
    print(f"New conversation view called, created convo ID: {convo.id}.")
    # Redirect to the assistant view with the new convo ID.
    # The GET logic of assistant will handle displaying the welcome message.
    return redirect("home_page:assistant", convo_id=convo.id)