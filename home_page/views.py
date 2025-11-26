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
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
import logging
import urllib.parse
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
import traceback
from datetime import datetime, timedelta
from django.utils.timezone import get_current_timezone, make_aware
import re as _re
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

                # Fetch all messages for this conversation (including structured ones)
                messages_to_render = list(convo.messages.order_by('timestamp'))

                # if an existing conversation is loaded, it's not a new chat state for animation
                is_new_conversation_page = False

                # If a brand-new, message-less conversation was just opened,
                # flag it so the frontend can run the welcome-message animation.
                if len(messages_to_render) == 0:
                    is_new_conversation_page = True
                    welcome_message_for_frontend = welcome_message_text_content

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
                     print("No existing convos to redirect to. Redirecting to new conversation.")
        
        else:
            # handles the base /agent/assistant/ URL without an ID
            if conversations.exists():
                latest_convo = conversations.first()
                print("GET request to base URL with existing convos. Redirecting to latest.")
                return redirect('home_page:assistant', convo_id=latest_convo.id)
            else:
                print("GET request to base URL with no existing convos. Redirecting to new conversation.")
                return redirect('home_page:new_conversation')


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


    # Prepare messages for rendering - serialize JSON content for JavaScript
    messages_with_json = []
    for msg in messages_to_render:
        msg_dict = {
            'id': msg.id,
            'sender': msg.sender,
            'text': msg.text,
            'message_type': msg.message_type,
            'content': msg.content,
            'content_json': json.dumps(msg.content) if msg.content else None,
            'timestamp': msg.timestamp,
        }
        messages_with_json.append(msg_dict)

    # Prepare the context data to pass to the template
    context = {
        "conversations": conversations, # List of all recent conversations
        "current_convo": convo, # The currently selected conversation object (or None)
        "messages": messages_with_json, # Messages for the current_convo (or empty list)
        "is_new_conversation_page": is_new_conversation_page, # Flag for frontend animation
        # Pass welcome message text only when the flag is True
        "welcome_message_text": welcome_message_for_frontend if is_new_conversation_page else None,
        "active_convo_id": str(convo.id) if convo else None,
        "google_calendar_icon_url": os.path.join(settings.STATIC_URL, 'home_page/images/google_calendar_icon.svg') # Assuming this is needed
    }

    print(f"Rendering assistant.html with is_new_conversation_page={is_new_conversation_page}, {len(messages_with_json)} messages, current_convo={convo.id if convo else 'None'}.")
    return render(request, "home_page/assistant.html", context)





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
        # Client-reported IANA timezone (e.g., "Europe/London")
        client_tz_name = data.get("client_tz")
        confirmation_data = data.get("confirmation_data")

        if not user_input and not confirmation_data:
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
        
        if user_input:
            user_message = Message.objects.create(
                conversation=convo,
                sender='user',
                text=user_input,
                message_type='text',
                content=None,
            )

        # Get agent response using AIAgent
        # Pass the convo object to the AIAgent handle method
        ai_agent = AIAgent(user)

        # Check for confirmation_data to bypass AI and create event directly
        # confirmation_data is already extracted above
        if confirmation_data:
            print(f"Confirmation data received: {confirmation_data}")
            if not AIAgent(request.user).is_google_connected():
                 return JsonResponse({
                     'type': 'needs_connection',
                     'response': 'Please connect your Google account to confirm this event.',
                     'content': {
                         'content_url': reverse('home_page:connect_google') + f"?next={reverse('home_page:assistant', args=[convo.id])}",
                         'email': request.user.email
                     }
                 })
            
            try:
                gcal = GoogleCalendarService(request.user)
                # Sanitize the event body to remove extra fields like 'conflicts' or 'agent_message'
                event_body = {
                    'summary': confirmation_data.get('summary'),
                    'start': confirmation_data.get('start'),
                    'end': confirmation_data.get('end'),
                    'attendees': confirmation_data.get('attendees', []),
                }
                # Add description or location if they exist in confirmation_data
                if 'description' in confirmation_data:
                    event_body['description'] = confirmation_data['description']
                if 'location' in confirmation_data:
                    event_body['location'] = confirmation_data['location']

                ev = gcal.create_event('primary', event_body)
                
                # Parse start/end for the success message
                start_dt_iso = confirmation_data['start']['dateTime']
                end_dt_iso = confirmation_data['end']['dateTime']
                summary = confirmation_data.get('summary', 'Event')
                
                # Get user's timezone
                try:
                    from zoneinfo import ZoneInfo
                    user_tz = ZoneInfo(client_tz_name or getattr(settings, 'TIME_ZONE', 'UTC') or 'UTC')
                except Exception:
                    from django.utils.timezone import get_current_timezone
                    user_tz = get_current_timezone()
                
                # Helper to format for display (convert from UTC to user's timezone)
                def _fmt_time_iso(iso_str):
                    try:
                        # Parse as UTC datetime
                        dt_utc = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
                        # Convert to user's timezone
                        dt_local = dt_utc.astimezone(user_tz)
                        t = dt_local.strftime('%I:%M %p')
                        return t.lstrip('0').replace('AM', 'am').replace('PM', 'pm')
                    except: return iso_str
                
                def _fmt_date_iso(iso_str):
                    try:
                        # Parse as UTC datetime
                        dt_utc = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
                        # Convert to user's timezone
                        dt_local = dt_utc.astimezone(user_tz)
                        return dt_local.strftime('%A, %B %d')
                    except: return iso_str

                agent_response_text = (
                    f"I created '{summary}' on "
                    f"{_fmt_date_iso(start_dt_iso)} from {_fmt_time_iso(start_dt_iso)} to {_fmt_time_iso(end_dt_iso)}."
                )
                
                # Persist the success message as structured event card
                event_success_content = {
                    'event_title': summary,
                    'event_link': ev.get('htmlLink'),
                    'event_id': ev.get('id'),
                }
                
                Message.objects.create(
                    conversation=convo,
                    sender='agent',
                    text=agent_response_text,
                    message_type='event_success',
                    content=event_success_content,
                )

                return JsonResponse({
                    'type': 'event_success',
                    'response': agent_response_text,
                    'content': event_success_content,
                    'intent': 'calendar',
                    'convo_id': str(convo.id),
                    'convo_title': convo.title,
                    'user_message_text': user_input,
                    'is_first_actual_message': is_first_actual_message,
                })

            except Exception as e:
                print(f"Error creating confirmed event: {e}")
                error_msg = f"Sorry, I failed to create the event: {e}"
                Message.objects.create(
                    conversation=convo,
                    sender='agent',
                    text=error_msg,
                    message_type='text'
                )
                return JsonResponse({
                    'type': 'text',
                    'response': error_msg,
                    'content': {},
                    'intent': 'calendar',
                    'convo_id': str(convo.id)
                })

        result = ai_agent.handle(user_input, conversation=convo)

        agent_response_text = result.get("response") # Assuming 'response' key for text
        response_type = result.get("type", "text") # Get the type, default to text
        response_content = result.get("content", {}) # Get content for calendar actions
        intent = result.get("intent", "general") # Extract intent from result, default to general
        if response_type == 'needs_connection':
            next_url = reverse('home_page:assistant', args=[convo.id])
            connect_url = reverse('home_page:connect_google') + f'?next={next_url}'
            connect_url_to_add = connect_url
            email_to_add = request.user.email
        else:
            connect_url_to_add = None
            email_to_add = None

        if agent_response_text and str(agent_response_text).strip():
            # Persist plain text replies from the agent
            if response_type == 'text':
                Message.objects.create(
                    conversation=convo,
                    sender='agent',
                    text=agent_response_text,
                    message_type='text',
                    content=None,
                )

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
            'intent': intent,  # Add this
            'convo_id': str(convo.id),
            'convo_title': convo.title,
            'user_message_text': user_input,
            # Don't send the agent message text back here, the frontend JS
            # will display based on 'type' and 'response'/'content'
            # 'agent_message_text': agent_response_text, # Remove this
            'is_first_actual_message': is_first_actual_message,
        }

        if response_type == 'needs_connection' and connect_url_to_add:
            response_data['content']['content_url'] = connect_url_to_add
            response_data['content']['email'] = email_to_add

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
            # Tell frontend who to display in the button
             response_data['content']['email'] = request.user.email


        # After OAuth redirect with ?resume=true, do not short-circuit. Allow normal
        # handling below so that the prior user message is processed.

        if response_type == 'calendar_action_request' and AIAgent(request.user).is_google_connected():
            try:
                action  = response_content['action']
                params  = response_content['params']

                # Ensure a token row exists; if not, ask user to reconnect to issue tokens
                if not SocialToken.objects.filter(account__user=request.user, account__provider='google').exists():
                    response_data.update({
                        'type': 'needs_connection',
                        'response': None,
                        'content': {
                            'message_for_user': 'Please connect your Google account to continue.',
                            'email': request.user.email,
                            'content_url': reverse('home_page:connect_google') + f"?next={reverse('home_page:assistant', args=[convo.id])}",
                            'needs_connection': True
                        }
                    })
                    return JsonResponse(response_data)

                gcal    = GoogleCalendarService(request.user)

                if action == 'find_free_slots':
                    # Normalize AI params to expected API
                    norm = dict(params or {})
                    # Map synonyms
                    if 'date' in norm and 'start_date' not in norm and 'start' not in norm:
                        norm['start_date'] = norm['date']
                    if 'date' in norm and 'end_date' not in norm and 'end' not in norm:
                        norm['end_date'] = norm['date']
                    if 'start' in norm and 'start_date' not in norm:
                        norm['start_date'] = norm['start']
                    if 'end' in norm and 'end_date' not in norm:
                        norm['end_date'] = norm['end']

                    start_date = norm.get('start_date')
                    end_date   = norm.get('end_date')
                    duration   = norm.get('duration', 60)
                    attendees  = norm.get('attendees')

                    # Coerce ISO datetimes into date-only strings if needed
                    def _date_only(val):
                        if isinstance(val, str) and 'T' in val:
                            return val.split('T', 1)[0]
                        return val

                    start_date = _date_only(start_date)
                    end_date   = _date_only(end_date)

                    # If no explicit ISO date provided, infer from user's text like "Thursday" or "next Thursday"
                    if not start_date and not end_date:
                        inferred_date = extract_date_from_text(user_input)
                        if inferred_date:
                            # Normalize to YYYY-MM-DD
                            inferred_iso = inferred_date.isoformat()
                            start_date = inferred_iso
                            end_date = inferred_iso
                        else:
                            # Cannot proceed – ask for a date/range and exit this action
                            response_type = 'text'
                            agent_response_text = (
                                "Please share a date (e.g. 2025-10-23) or a start and end date so I can check availability."
                            )
                    if start_date or end_date:
                        # If only one provided, assume single-day window
                        if start_date and not end_date:
                            end_date = start_date
                        if end_date and not start_date:
                            start_date = end_date

                        try:
                            busy_ranges = gcal.find_free_slots(
                                start_date=start_date,
                                end_date=end_date,
                                duration=duration,
                                attendees=attendees,
                            )
                            # Simple human summary
                            summary = (
                                "Your calendars are completely free between those dates!"
                                if not busy_ranges else
                                f"I found {len(busy_ranges)} busy periods.\n" +
                                "\n".join(f"- {b['start']} – {b['end']}" for b in busy_ranges[:3])
                            )
                            response_type = 'text'
                            agent_response_text = summary
                            # Persist the agent text so it survives reloads
                            try:
                                Message.objects.create(
                                    conversation=convo,
                                    sender='agent',
                                    text=agent_response_text,
                                    message_type='text',
                                    content=None,
                                )
                            except Exception:
                                pass
                        except Exception as e:
                            response_type = 'text'
                            agent_response_text = f"Sorry, I couldn't check availability: {e}"
                            # Always persist error messages so they survive reloads
                            Message.objects.create(
                                conversation=convo,
                                sender='agent',
                                text=agent_response_text,
                                message_type='text',
                                content=None,
                            )

                elif action == 'create_event':
                    # Build a proper Google Calendar event body from AI params
                    norm = dict(params or {})
                    # Normalize common synonym keys from the AI output
                    if 'start_time' in norm and 'start' not in norm:
                        norm['start'] = norm['start_time']
                    if 'end_time' in norm and 'end' not in norm:
                        norm['end'] = norm['end_time']
                    tz_str = (client_tz_name or getattr(settings, 'TIME_ZONE', 'UTC') or 'UTC')

                    # Helper: parse common ISO-ish formats and simple natural language
                    try:
                        # Prefer the client timezone for localization if provided
                        from zoneinfo import ZoneInfo
                        client_tz = ZoneInfo(tz_str)
                        print(f"✅ Using client timezone: {tz_str}")
                    except Exception as e:
                        client_tz = None
                        print(f"⚠️ Failed to parse client timezone '{tz_str}', falling back to Django default: {e}")
                    import re

                    def parse_dt(val: str):
                        if not val:
                            return None
                        s = str(val).strip()
                        # Normalize space separator to 'T'
                        s = s.replace(' ', 'T')
                        # Support trailing 'Z'
                        if s.endswith('Z'):
                            try:
                                return datetime.fromisoformat(s.replace('Z', '+00:00'))
                            except Exception:
                                pass
                        # Add seconds if missing (e.g. 2025-10-23T09:00)
                        m = re.match(r'^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?:([+-]\d{2}:\d{2})?)$', s)
                        if m:
                            s2 = f"{m.group(1)}T{m.group(2)}:00{m.group(3) or ''}"
                            try:
                                return datetime.fromisoformat(s2)
                            except Exception:
                                pass
                        # Plain date (all-day)
                        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
                            try:
                                return datetime.fromisoformat(s + 'T00:00:00')
                            except Exception:
                                return None
                        try:
                            return datetime.fromisoformat(s)
                        except Exception:
                            return None

                    def parse_duration(val):
                        """Parse duration strings like '2 hours', '90 minutes', '1.5 hours', '3:00', or plain numbers. Returns minutes as int."""
                        if not val:
                            return None
                        s = str(val).strip().lower()
                        # Try plain number first
                        try:
                            return int(float(s))
                        except ValueError:
                            pass
                        # Handle "H:MM" format (e.g., "3:00" means 3 hours, "2:30" means 2.5 hours)
                        m = re.match(r"^(\d+):(\d{2})$", s)
                        if m:
                            hours = int(m.group(1))
                            minutes = int(m.group(2))
                            return hours * 60 + minutes
                        # Parse "X hours", "X minutes", "X mins", "X hr", "X h"
                        m = re.match(r"^(\d+(?:\.\d+)?)\s*(hour|hours|hr|h|minute|minutes|mins|min|m)s?$", s)
                        if m:
                            num = float(m.group(1))
                            unit = m.group(2)
                            if unit in ('hour', 'hours', 'hr', 'h'):
                                return int(num * 60)
                            else:  # minutes
                                return int(num)
                        return None

                    def parse_time_only(val: str):
                        """Parse simple time-of-day like '9am', '9:30 am', '12pm', 'noon', 'midnight'. Returns (hour, minute) or None."""
                        if not val:
                            return None
                        s = str(val).strip().lower()
                        if s in ("noon",):
                            return (12, 0)
                        if s in ("midnight",):
                            return (0, 0)
                        m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", s)
                        if not m:
                            return None
                        hour = int(m.group(1))
                        minute = int(m.group(2) or 0)
                        meridiem = m.group(3)
                        if meridiem:
                            if hour == 12:
                                hour = 0 if meridiem == 'am' else 12
                            elif meridiem == 'pm':
                                hour += 12
                        # 24-hour times like '14:00'
                        if not meridiem and hour > 23:
                            return None
                        return (hour, minute)

                    def resolve_date(val: str):
                        """Resolve a date string like '2025-10-23', 'today', 'tomorrow', 'thursday', 'next thursday' to a date object."""
                        if not val:
                            return None
                        s = str(val).strip().lower()
                        # ISO date
                        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                            try:
                                return datetime.fromisoformat(s + 'T00:00:00').date()
                            except Exception:
                                return None
                        # Use client timezone if available; otherwise Django's current timezone
                        tz = client_tz or get_current_timezone()
                        today = datetime.now(tz).date()
                        if s == 'today':
                            return today
                        if s == 'tomorrow':
                            return today + timedelta(days=1)
                        # Weekday names
                        weekdays = {
                            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
                            'friday': 4, 'saturday': 5, 'sunday': 6
                        }
                        prefix_next = False
                        parts = s.split()
                        if len(parts) == 2 and parts[0] == 'next' and parts[1] in weekdays:
                            prefix_next = True
                            target_idx = weekdays[parts[1]]
                        elif s in weekdays:
                            target_idx = weekdays[s]
                        else:
                            return None
                        delta = (target_idx - today.weekday()) % 7
                        if delta == 0 and prefix_next:
                            delta = 7
                        if delta < 0:
                            delta += 7
                        return today + timedelta(days=delta)

                    def extract_date_from_text(text: str):
                        """Pull a simple date reference from raw user text (today/tomorrow/weekday/next weekday)."""
                        if not text:
                            return None
                        s = str(text).lower()
                        # Prefer explicit tokens
                        for token in ["today", "tomorrow"]:
                            if token in s:
                                return resolve_date(token)
                        # next <weekday> or <weekday>
                        import re as _re
                        m = _re.search(r"\b(next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", s)
                        if m:
                            token = (m.group(0) or '').strip()
                            return resolve_date(token)
                        # Fallback: explicit ISO date
                        m = _re.search(r"\b\d{4}-\d{2}-\d{2}\b", s)
                        if m:
                            return resolve_date(m.group(0))
                        return None

                    # Determine start/end
                    date_str  = norm.get('date') or norm.get('start_date')
                    # Support phrasing like "by 8am" → treat as an end time
                    start_str = norm.get('start') or norm.get('start_time') or norm.get('date')
                    end_str   = norm.get('end')
                    duration  = norm.get('duration')
                    summary   = norm.get('summary') or 'Meeting'
                    attendees = norm.get('attendees') or []
                    
                    # Debug: Log what AI extracted
                    print(f"AI EXTRACTED: date='{date_str}', start='{start_str}', end='{end_str}', duration='{duration}', summary='{summary}'")

                    start_dt = parse_dt(start_str)
                    end_dt   = parse_dt(end_str) if end_str else None

                    # If the user says "by <time>" and no explicit end provided,
                    # infer end time and compute start from duration if available later.
                    text_lc = (user_input or '').lower()
                      # Pattern 1: "by X to Y" (common phrasing that means "from X to Y")
                    import re as _re
                    by_to_match = _re.search(
                        r"\bby\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+to\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", 
                        text_lc
                    )
                    if by_to_match:
                        start_str = by_to_match.group(1)
                        end_str = by_to_match.group(2)
                        start_dt = None
                        end_dt = None
                        print(f"🔍 PATTERN: 'by X to Y' → start={start_str}, end={end_str}")
                    
                    # Pattern 2: "from X to Y" (explicit range)
                    elif (' from ' in text_lc) and (' to ' in text_lc):
                        from_to = _re.search(
                            r"\bfrom\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+to\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", 
                            text_lc
                        )
                        if from_to:
                            start_str = from_to.group(1)
                            end_str = from_to.group(2)
                            start_dt = None
                            end_dt = None
                            print(f"🔍 PATTERN: 'from X to Y' → start={start_str}, end={end_str}")
                    
                    # Pattern 3: "X to Y" or "X-Y" (simple range)
                    elif not by_to_match:
                        simple_range = _re.search(
                            r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s+(?:to|-)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", 
                            text_lc
                        )
                        if simple_range:
                            start_str = simple_range.group(1)
                            end_str = simple_range.group(2)
                            start_dt = None
                            end_dt = None
                            print(f"🔍 PATTERN: 'X to Y' → start={start_str}, end={end_str}")
                    
                    # Pattern 4: "by X" - treat as start time if duration is specified, otherwise as deadline
                    # This should only trigger if no range pattern was found
                    if not (by_to_match or (start_str and end_str)):
                        by_alone = _re.search(r"\bby\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", text_lc)
                        if by_alone:
                            # Check if "to" appears within 30 chars after "by" to avoid false positives
                            by_end = by_alone.end()
                            has_to_after = 'to' in text_lc[by_end:by_end+30]
                            if not has_to_after:
                                # If duration is explicitly mentioned, treat "by X" as start time
                                # Common phrases: "last for X", "for X hours", "X hour", etc.
                                has_duration = bool(duration) or any(word in text_lc for word in ['last for', 'lasting', 'duration'])
                                if has_duration:
                                    start_str = by_alone.group(1)
                                    start_dt = None
                                    print(f"🔍 PATTERN: 'by X' with duration → start={start_str}")
                                else:
                                    end_str = by_alone.group(1)
                                    end_dt = None
                                    print(f"🔍 PATTERN: 'by X' (deadline) → end={end_str}")
                    
                    # Pattern 5: "at X" for start time
                    if (' at ' in text_lc) and not start_str:
                        at_match = _re.search(r"\bat\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", text_lc)
                        if at_match:
                            start_str = at_match.group(1)
                            start_dt = None
                            print(f"🔍 PATTERN: 'at X' → start={start_str}")


                    # If times are given without date, merge with the most reliable date.
                    # Prefer the user's natural-language date (e.g., "Friday") over any absolute
                    # date guessed by the AI to avoid stale/past years like 2023.
                    # Additionally, if AI provided full datetimes but the user mentioned an explicit
                    # date in natural language, snap those datetimes to that date to avoid past years.
                    date_from_text = extract_date_from_text(user_input)
                    ai_date_only   = resolve_date(date_str) if date_str else None
                    date_only      = date_from_text or ai_date_only
                    if date_only:
                        # Use the client's timezone when creating datetime objects
                        # so that "9am" means "9am in the user's local time", not UTC
                        user_tz = client_tz or get_current_timezone()
                        
                        if not start_dt and start_str:
                            hm = parse_time_only(start_str)
                            if hm:
                                # Create timezone-aware datetime in user's timezone
                                naive_dt = datetime.combine(date_only, datetime.min.time()).replace(hour=hm[0], minute=hm[1])
                                start_dt = make_aware(naive_dt, user_tz)
                        if end_str:
                            hm = parse_time_only(end_str)
                            if hm:
                                # Create timezone-aware datetime in user's timezone
                                naive_dt = datetime.combine(date_only, datetime.min.time()).replace(hour=hm[0], minute=hm[1])
                                end_dt = make_aware(naive_dt, user_tz)
                        # If AI provided full datetimes but with an incorrect/past date, snap to the requested date
                        if start_dt and (start_dt.date() != date_only):
                            # Preserve timezone when snapping to new date
                            original_tz = start_dt.tzinfo or user_tz
                            naive_dt = datetime.combine(date_only, start_dt.time())
                            start_dt = make_aware(naive_dt, original_tz)
                        if end_dt and (end_dt.date() != date_only):
                            # Preserve timezone when snapping to new date
                            original_tz = end_dt.tzinfo or user_tz
                            naive_dt = datetime.combine(date_only, end_dt.time())
                            end_dt = make_aware(naive_dt, original_tz)

                    # Compute end from duration when needed
                    if start_dt and not end_dt:
                        minutes = parse_duration(duration) if duration else 60
                        if minutes is None:
                            minutes = 60
                        end_dt = start_dt + timedelta(minutes=minutes)

                    # Compute start from end and duration (e.g., "by 9am")
                    if end_dt and not start_dt:
                        minutes = parse_duration(duration) if duration else 60
                        if minutes is None:
                            minutes = 60
                        start_dt = end_dt - timedelta(minutes=minutes)

                    # Final guard: ensure end is strictly after start
                    if start_dt and end_dt and end_dt <= start_dt:
                        minutes = parse_duration(duration) if duration else 60
                        if minutes is None:
                            minutes = 60
                        end_dt = start_dt + timedelta(minutes=minutes)

                    if not start_dt or not end_dt:
                        response_type = 'text'
                        has_date = bool(date_only)
                        agent_response_text = (
                            "I need a date plus a start time and either an end time or a duration."
                            if not has_date else
                            "I need a concrete start time and duration (or end time) to create the event. Please provide a start time and either an end time or a duration."
                        )
                        Message.objects.create(
                            conversation=convo,
                            sender='agent',
                            text=agent_response_text,
                            message_type='text',
                            content=None,
                        )
                    else:
                        # Normalize datetimes into the client's timezone
                        tz = client_tz or get_current_timezone()
                        print(tz)
                        if start_dt.tzinfo is None:
                            start_dt = make_aware(start_dt, tz)
                        else:
                            start_dt = start_dt.astimezone(tz)
                        if end_dt.tzinfo is None:
                            end_dt = make_aware(end_dt, tz)
                        else:
                            end_dt = end_dt.astimezone(tz)

                        # Log the resolved datetimes for diagnostics
                        try:
                            logger.info(
                                "Resolved event datetimes (local tz): start=%s, end=%s, title=%s, tz=%s",
                                start_dt.isoformat(), end_dt.isoformat(), summary, tz_str
                            )
                        except Exception:
                            pass

                        event_body = {
                            'summary': summary,
                            'start': {
                                'dateTime': start_dt.isoformat(),
                                'timeZone': tz_str,
                            },
                            'end': {
                                'dateTime': end_dt.isoformat(),
                                'timeZone': tz_str,
                            },
                        }
                        # Normalize attendees to list of {email}
                        if isinstance(attendees, (list, tuple)) and attendees:
                            event_body['attendees'] = [
                                {'email': a} for a in attendees if isinstance(a, str) and '@' in a
                            ]

                        # INTERCEPT: Do not create event yet. Check for conflicts and return confirmation request.
                        
                        # Check for conflicts
                        busy_ranges = []
                        try:
                            busy_ranges = gcal.find_free_slots(
                                start_date=start_dt.isoformat(),
                                end_date=end_dt.isoformat(),
                                attendees=attendees
                            )
                        except Exception as e:
                            print(f"Warning: Failed to check conflicts: {e}")
                        
                        has_conflict = bool(busy_ranges)
                        conflict_msg = "You are free at this time." if not has_conflict else "⚠️ You have a conflict at this time."
                        
                        response_type = 'event_confirmation_request'
                        response_content = {
                            'summary': summary,
                            'start': event_body['start'],
                            'end': event_body['end'],
                            'attendees': event_body.get('attendees', []),
                            'conflicts': has_conflict,
                            'agent_message': f"I've drafted this meeting. {conflict_msg}"
                        }
                        
                        agent_response_text = response_content['agent_message']
                        
                        # Persist the draft event preview as structured message
                        Message.objects.create(
                            conversation=convo,
                            sender='agent',
                            text=agent_response_text,
                            message_type='event_preview',
                            content=response_content,
                        )



                elif action == 'list_events':
                    # List events within a date range. Support simple synonyms and NL dates.
                    norm = dict(params or {})
                    # Map simple synonyms
                    if 'date' in norm and 'start_date' not in norm and 'end_date' not in norm:
                        norm['start_date'] = norm['date']
                        norm['end_date'] = norm['date']
                    if 'start' in norm and 'start_date' not in norm:
                        norm['start_date'] = norm['start']
                    if 'end' in norm and 'end_date' not in norm:
                        norm['end_date'] = norm['end']

                    raw_start_date = (norm.get('start_date') or '').strip() or None
                    raw_end_date   = (norm.get('end_date') or '').strip() or None

                    # Helpers for parsing simple natural language dates
                    
                    try:
                        from zoneinfo import ZoneInfo
                        tz = ZoneInfo(client_tz_name or getattr(settings, 'TIME_ZONE', 'UTC') or 'UTC')
                    except Exception:
                        tz = get_current_timezone()

                    def _parse_simple_date(val: str):
                        if not val:
                            return None
                        s = str(val).strip().lower()
                        # YYYY-MM-DD
                        import re as _re
                        if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                            try:
                                return datetime.fromisoformat(s + 'T00:00:00').date()
                            except Exception:
                                return None
                        today = datetime.now(tz).date()
                        if s == 'today':
                            return today
                        if s == 'tomorrow':
                            return today + timedelta(days=1)
                        weekdays = {
                            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
                            'friday': 4, 'saturday': 5, 'sunday': 6
                        }
                        parts = s.split()
                        prefix_next = (len(parts) == 2 and parts[0] == 'next' and parts[1] in weekdays)
                        if prefix_next or s in weekdays:
                            target_idx = weekdays[parts[1]] if prefix_next else weekdays[s]
                            delta = (target_idx - today.weekday()) % 7
                            if delta == 0 and prefix_next:
                                delta = 7
                            if delta < 0:
                                delta += 7
                            return today + timedelta(days=delta)
                        return None

                    # Anchor to current/relative week if the user asked for it,
                    # even if the AI returned stale absolute dates.
                    text_lc = (user_input or '').lower()
                    def _override_week_range(shift_weeks: int = 0):
                        today_local = datetime.now(tz).date()
                        sow = today_local - timedelta(days=today_local.weekday()) + timedelta(days=7*shift_weeks)
                        eow = sow + timedelta(days=6)
                        return sow.isoformat(), eow.isoformat()

                    if 'week' in text_lc or 'schedule' in text_lc:
                        # Detect phrases "next week" or "last/previous week"
                        if 'next week' in text_lc:
                            start_date, end_date = _override_week_range(1)
                        elif 'last week' in text_lc or 'previous week' in text_lc:
                            start_date, end_date = _override_week_range(-1)
                        elif 'this week' in text_lc or 'week' in text_lc or 'schedule' in text_lc:
                            start_date, end_date = _override_week_range(0)

                    # If AI still provided dates wildly far from "now" (> 90 days), snap to this week
                    def _parse_iso_date(d: str):
                        try:
                            return datetime.fromisoformat(str(d).strip() + 'T00:00:00').date()
                        except Exception:
                            return None
                    if start_date and end_date:
                        sd = _parse_iso_date(start_date)
                        ed = _parse_iso_date(end_date)
                        today_local = datetime.now(tz).date()
                        if sd and abs((sd - today_local).days) > 90 and ('week' in text_lc or 'schedule' in text_lc):
                            start_date, end_date = _override_week_range(0)

                    # Parse provided start/end dates or infer from text
                    start_date = None
                    end_date = None
                    
                    # First, try to parse the dates provided by AI
                    if raw_start_date:
                        sd = _parse_simple_date(raw_start_date)
                        start_date = sd.isoformat() if sd else None
                    if raw_end_date:
                        ed = _parse_simple_date(raw_end_date)
                        end_date = ed.isoformat() if ed else None
                        
                    # If no start_date, try to infer from other params or text
                    if not start_date:
                        sd = _parse_simple_date(norm.get('date')) or _parse_simple_date(norm.get('start'))
                        if not sd:
                            # Try scanning the raw user text for a simple date
                            import re as _re
                            m = _re.search(r"\b\d{4}-\d{2}-\d{2}\b", text_lc)
                            sd = _parse_simple_date(m.group(0)) if m else None
                        start_date = sd.isoformat() if sd else None
                        
                    # If no end_date but we have start_date, use same date
                    if not end_date and start_date:
                        end_date = start_date

                    if not start_date and not end_date:
                        response_type = 'text'
                        agent_response_text = (
                            "Please share a start date and end date, or say 'this week', so I can list your events."
                        )
                        try:
                            Message.objects.create(
                                conversation=convo,
                                sender='agent',
                                text=agent_response_text,
                                message_type='text',
                                content=None,
                            )
                        except Exception:
                            pass
                    else:
                        # Build RFC3339 boundaries in UTC 'Z'
                        # Here we keep it simple by assuming all-day window(s)
                        time_min = f"{start_date}T00:00:00Z"
                        time_max = f"{end_date}T23:59:59Z"
                        try:
                            items = gcal.list_events('primary', time_min=time_min, time_max=time_max)

                            def _fmt_when(ev):
                                start = (ev.get('start') or {})
                                end = (ev.get('end') or {})
                                s = start.get('dateTime') or start.get('date')
                                e = end.get('dateTime') or end.get('date')
                                def _parse_dt(v):
                                    if not v:
                                        return None
                                    if isinstance(v, str) and v.endswith('Z'):
                                        v = v.replace('Z', '+00:00')
                                    try:
                                        return datetime.fromisoformat(v)
                                    except Exception:
                                        return None
                                ds = _parse_dt(s)
                                de = _parse_dt(e)
                                try:
                                    # Localize for display
                                    if ds and ds.tzinfo:
                                        ds_local = ds.astimezone(tz)
                                    elif ds:
                                        ds_local = ds.replace(tzinfo=None)
                                    else:
                                        ds_local = None
                                    if de and de.tzinfo:
                                        de_local = de.astimezone(tz)
                                    elif de:
                                        de_local = de.replace(tzinfo=None)
                                    else:
                                        de_local = None
                                    if ds_local and de_local and ds_local.date() == de_local.date():
                                        return f"{ds_local.strftime('%b %d, %Y')} • {ds_local.strftime('%I:%M %p').lstrip('0')} – {de_local.strftime('%I:%M %p').lstrip('0')}"
                                    if ds_local and de_local:
                                        return f"{ds_local.strftime('%b %d %I:%M %p').lstrip('0')} → {de_local.strftime('%b %d %I:%M %p').lstrip('0')}"
                                    if ds_local:
                                        return ds_local.strftime('%b %d, %Y')
                                    return ''
                                except Exception:
                                    return ''

                            if not items:
                                when_text = start_date if start_date == end_date else f"{start_date} to {end_date}"
                                summary = f"You have no events on {when_text}."
                            else:
                                # Group events by day
                                from collections import defaultdict
                                events_by_day = defaultdict(list)
                                
                                def _parse_event_date(ev):
                                    """Extract date from event for grouping"""
                                    start = (ev.get('start') or {})
                                    s = start.get('dateTime') or start.get('date')
                                    if not s:
                                        return None
                                    if isinstance(s, str) and s.endswith('Z'):
                                        s = s.replace('Z', '+00:00')
                                    try:
                                        dt = datetime.fromisoformat(s)
                                        if dt.tzinfo:
                                            dt = dt.astimezone(tz)
                                        return dt.date()
                                    except Exception:
                                        return None
                                
                                def _format_event_time(ev):
                                    """Format event time range for display"""
                                    start = (ev.get('start') or {})
                                    end = (ev.get('end') or {})
                                    s = start.get('dateTime') or start.get('date')
                                    e = end.get('dateTime') or end.get('date')
                                    
                                    def _parse_dt(v):
                                        if not v:
                                            return None
                                        if isinstance(v, str) and v.endswith('Z'):
                                            v = v.replace('Z', '+00:00')
                                        try:
                                            return datetime.fromisoformat(v)
                                        except Exception:
                                            return None
                                    
                                    ds = _parse_dt(s)
                                    de = _parse_dt(e)
                                    
                                    try:
                                        # Localize for display
                                        if ds and ds.tzinfo:
                                            ds_local = ds.astimezone(tz)
                                        elif ds:
                                            ds_local = ds
                                        else:
                                            ds_local = None
                                        if de and de.tzinfo:
                                            de_local = de.astimezone(tz)
                                        elif de:
                                            de_local = de
                                        else:
                                            de_local = None
                                        
                                        if ds_local and de_local:
                                            return f"{ds_local.strftime('%I:%M %p').lstrip('0')} - {de_local.strftime('%I:%M %p').lstrip('0')}"
                                        elif ds_local:
                                            return ds_local.strftime('%I:%M %p').lstrip('0')
                                        return ''
                                    except Exception:
                                        return ''
                                
                                # Group events by day
                                for ev in items:
                                    event_date = _parse_event_date(ev)
                                    if event_date:
                                        events_by_day[event_date].append(ev)
                                
                                # Determine the time range type (day/week/month/year)
                                try:
                                    start_dt = datetime.fromisoformat(start_date + 'T00:00:00').date()
                                    end_dt = datetime.fromisoformat(end_date + 'T00:00:00').date()
                                    day_span = (end_dt - start_dt).days + 1
                                    
                                    # Classify the range
                                    if day_span == 1:
                                        range_type = 'day'
                                    elif day_span <= 7:
                                        range_type = 'week'
                                    elif day_span <= 31:
                                        range_type = 'month'
                                    else:
                                        range_type = 'year'
                                except Exception:
                                    range_type = 'week'  # Default fallback
                                
                                # Build formatted output
                                lines = []
                                
                                # Add header with date range
                                try:
                                    start_dt = datetime.fromisoformat(start_date + 'T00:00:00').date()
                                    end_dt = datetime.fromisoformat(end_date + 'T00:00:00').date()
                                    
                                    # Format header based on range type
                                    if range_type == 'day':
                                        today_date = datetime.now(tz).date()
                                        day_label = "Today's Schedule" if start_dt == today_date else f"Schedule for {start_dt.strftime('%A, %B %d, %Y')}"
                                        lines.append(f"📅 {day_label}\n")
                                    elif range_type == 'week':
                                        if start_dt.month == end_dt.month and start_dt.year == end_dt.year:
                                            date_range = f"{start_dt.strftime('%B')} {start_dt.day}-{end_dt.day}, {start_dt.year}"
                                        else:
                                            date_range = f"{start_dt.strftime('%B %d')} - {end_dt.strftime('%B %d, %Y')}"
                                        lines.append(f"📅 Your Weekly Schedule - {date_range}\n")
                                    elif range_type == 'month':
                                        lines.append(f"📅 Your Schedule for {start_dt.strftime('%B %Y')}\n")
                                    else:  # year
                                        lines.append(f"📅 Your Schedule for {start_dt.strftime('%Y')}\n")
                                except Exception:
                                    lines.append("📅 Your Schedule\n")
                                
                                # Sort days chronologically
                                sorted_days = sorted(events_by_day.keys())
                                today_date = datetime.now(tz).date()
                                
                                for day in sorted_days:
                                    day_events = events_by_day[day]
                                    
                                    # Format day header (remove leading zero from day)
                                    day_name = day.strftime('%A, %B %d').replace(' 0', ' ')
                                    
                                    # Add (Today) indicator if applicable
                                    if day == today_date:
                                        day_name += " (Today)"
                                    
                                    lines.append(f"**{day_name}**")
                                    
                                    # Add events for this day
                                    for ev in day_events:
                                        title = ev.get('summary') or 'Untitled'
                                        time_str = _format_event_time(ev)
                                        lines.append(f"• {time_str}: {title}")
                                    
                                    lines.append("")  # Empty line between days
                                
                                # Add days with no events within the range (only for day and week views)
                                if range_type in ['day', 'week']:
                                    try:
                                        start_dt = datetime.fromisoformat(start_date + 'T00:00:00').date()
                                        end_dt = datetime.fromisoformat(end_date + 'T00:00:00').date()
                                        current_date = start_dt
                                        
                                        while current_date <= end_dt:
                                            if current_date not in events_by_day:
                                                day_name = current_date.strftime('%A, %B %d').replace(' 0', ' ')
                                                if current_date == today_date:
                                                    day_name += " (Today)"
                                                
                                                # Insert in chronological order
                                                inserted = False
                                                for i, line in enumerate(lines):
                                                    if line.startswith('**'):
                                                        line_date_str = line.strip('*').split(' (')[0]
                                                        # Simple comparison - if this empty day should come before this line
                                                        if current_date < _parse_event_date(items[0]) if items else False:
                                                            lines.insert(i, f"**{day_name}**")
                                                            lines.insert(i+1, "*(No events scheduled)*")
                                                            lines.insert(i+2, "")
                                                            inserted = True
                                                            break
                                                
                                                if not inserted and current_date not in sorted_days:
                                                    lines.append(f"**{day_name}**")
                                                    lines.append("*(No events scheduled)*")
                                                    lines.append("")
                                            
                                            current_date += timedelta(days=1)
                                    except Exception:
                                        pass
                                
                                summary = "\n".join(lines).strip()
                                
                                # Use AI to generate a personalized closing message
                                try:
                                    # Build a summary of the events for the AI
                                    event_summary_parts = []
                                    for day, day_events in sorted(events_by_day.items()):
                                        day_name = day.strftime('%A')
                                        event_count = len(day_events)
                                        event_titles = [ev.get('summary', 'Untitled') for ev in day_events[:3]]
                                        event_summary_parts.append(f"{day_name}: {event_count} event(s) - {', '.join(event_titles)}")
                                    
                                    event_summary = "; ".join(event_summary_parts[:7])  # Limit to prevent token overflow
                                    
                                    ai_prompt = f"""The user just viewed their {range_type} schedule with {len(items)} total event(s). 

                                        Events breakdown: {event_summary}

                                        Generate a friendly, personalized 1-2 sentence closing remark that:
                                        - Acknowledges their schedule (busy/light/balanced)
                                        - Mentions specific patterns if notable (e.g., "Friday is packed", "weekend is free")
                                        - Offers help with scheduling
                                        - Keep it warm and conversational
                                        - Add an emoji if appropriate

                                        Do not repeat the event list. Just provide the closing remark."""

                                    closing_messages = [{"role": "user", "content": ai_prompt}]
                                    closing_message = ai_agent._get_claude_chat_response(
                                        closing_messages,
                                        temperature=0.7,
                                        max_tokens=100
                                    )
                                    
                                    if closing_message and closing_message.strip():
                                        summary = summary + "\n\n" + closing_message.strip()
                                except Exception as e:
                                    print(f"Failed to generate AI closing message: {e}")
                                    # Continue without closing message if AI fails

                            response_type = 'text'
                            agent_response_text = summary
                            try:
                                Message.objects.create(
                                    conversation=convo,
                                    sender='agent',
                                    text=agent_response_text,
                                    message_type='text',
                                    content=None,
                                )
                            except Exception:
                                pass
                        except Exception as e:
                            response_type = 'text'
                            agent_response_text = f"Sorry, I couldn't list events: {e}"
                            # Always persist error messages so they survive reloads
                            Message.objects.create(
                                conversation=convo,
                                sender='agent',
                                text=agent_response_text,
                                message_type='text',
                                content=None,
                            )

                # etc. (list_events, delete_event …)

                # Reflect any modifications back to the payload sent to the front-end
                response_data.update({
                    'type': response_type,
                    'response': agent_response_text,
                    'content': response_content,
                })

            except Exception as e:
                print(f"Error processing calendar action: {e}")
                # traceback.print_exc()
                error_message = f"Sorry, I encountered an error while processing your calendar request: {str(e)}"
                # Persist error messages so they survive reloads
                Message.objects.create(
                    conversation=convo,
                    sender='agent',
                    text=error_message,
                    message_type='text',
                    content=None,
                )
                response_data.update({
                    'type': 'text',
                    'response': error_message,
                    'content': {},
                })
        elif response_type == 'calendar_action_request':
            # If a calendar action is requested but account is still not connected
            # return a needs_connection card to the frontend.
            response_data.update({
                'type': 'needs_connection',
                'response': None,
                'content': {
                    'message_for_user': 'Please connect your Google account to continue.',
                    'email': request.user.email,
                    'content_url': reverse('home_page:connect_google') + f"?next={reverse('home_page:assistant', args=[convo.id])}",
                    'needs_connection': True
                }
            })

        return JsonResponse(response_data)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON received'}, status=400)
    except Exception as e:
        print(f"Error in chat_process: {e}")
        import traceback
        traceback.print_exc()
        
        # Try to persist the error message if we have a conversation context
        error_message = f"Sorry, an internal server error occurred: {str(e)}"
        try:
            if 'convo' in locals() and convo:
                Message.objects.create(
                    conversation=convo,
                    sender='agent',
                    text=error_message,
                    message_type='text',
                    content=None,
                )
        except Exception:
            # If we can't persist, at least return the error to frontend
            pass
            
        return JsonResponse({'error': error_message}, status=500)


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
    

def connect_google(request):
    initial_next = request.GET.get("next", "/agent/assistant/")

    parts      = urlparse(initial_next)
    query_dict = parse_qs(parts.query)
    query_dict["resume"] = ["true"]           # overwrite/add exactly once

    new_query = urlencode(query_dict, doseq=True)
    next_url  = urlunparse(parts._replace(query=new_query))
    extras = [ # scopes for gmail and calendar
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    params = [
        ("scope", " ".join(extras + ["profile", "email"])),
        ("process", "connect"), 
            ("prompt", "consent"), # to get new refresh tokens
            ("access_type", "offline"), # ensure refresh_token is issued
        ("next", next_url),
    ]
    qs = urllib.parse.urlencode(params)
    return redirect(f"/accounts/google/login/?{qs}")