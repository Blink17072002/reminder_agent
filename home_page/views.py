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


    # Prepare the context data to pass to the template
    context = {
        "conversations": conversations, # List of all recent conversations
        "current_convo": convo, # The currently selected conversation object (or None)
        "messages": messages_to_render, # Messages for the current_convo (or empty list)
        "is_new_conversation_page": is_new_conversation_page, # Flag for frontend animation
        # Pass welcome message text only when the flag is True
        "welcome_message_text": welcome_message_for_frontend if is_new_conversation_page else None,
        "active_convo_id": str(convo.id) if convo else None,
        "google_calendar_icon_url": os.path.join(settings.STATIC_URL, 'home_page/images/google_calendar_icon.svg') # Assuming this is needed
    }

    print(f"Rendering assistant.html with is_new_conversation_page={is_new_conversation_page}, {len(messages_to_render)} messages, current_convo={convo.id if convo else 'None'}.")
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
        result = ai_agent.handle(user_input, conversation=convo) # <--- Pass convo object

        agent_response_text = result.get("response") # Assuming 'response' key for text
        response_type = result.get("type", "text") # Get the type, default to text
        response_content = result.get("content", {}) # Get content for calendar actions
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
                    from datetime import datetime, timedelta
                    from django.utils.timezone import make_aware, get_current_timezone
                    try:
                        # Prefer the client timezone for localization if provided
                        from zoneinfo import ZoneInfo
                        client_tz = ZoneInfo(tz_str)
                    except Exception:
                        client_tz = None
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
                    start_str = norm.get('start') or norm.get('start_time') or norm.get('date')
                    end_str   = norm.get('end')
                    duration  = norm.get('duration')
                    summary   = norm.get('summary') or 'Meeting'
                    attendees = norm.get('attendees') or []

                    start_dt = parse_dt(start_str)
                    end_dt   = parse_dt(end_str) if end_str else None

                    # If times are given without date, merge with the most reliable date.
                    # Prefer the user's natural-language date (e.g., "Friday") over any absolute
                    # date guessed by the AI to avoid stale/past years like 2023.
                    date_from_text = extract_date_from_text(user_input)
                    ai_date_only   = resolve_date(date_str) if date_str else None
                    date_only      = date_from_text or ai_date_only
                    if date_only:
                        if not start_dt and start_str:
                            hm = parse_time_only(start_str)
                            if hm:
                                start_dt = datetime.combine(date_only, datetime.min.time()).replace(hour=hm[0], minute=hm[1])
                        if not end_dt and end_str:
                            hm = parse_time_only(end_str)
                            if hm:
                                end_dt = datetime.combine(date_only, datetime.min.time()).replace(hour=hm[0], minute=hm[1])
                        # If AI provided full datetimes but with an incorrect/past date, snap to the requested date
                        if start_dt and (start_dt.date() != date_only):
                            start_dt = datetime.combine(date_only, start_dt.time())
                        if end_dt and (end_dt.date() != date_only):
                            end_dt = datetime.combine(date_only, end_dt.time())

                    # Compute end from duration when needed
                    if start_dt and not end_dt:
                        try:
                            minutes = int(duration) if duration is not None else 60
                        except Exception:
                            minutes = 60
                        end_dt = start_dt + timedelta(minutes=minutes)

                    # Compute start from end and duration (e.g., "by 9am")
                    if end_dt and not start_dt:
                        try:
                            minutes = int(duration) if duration is not None else 60
                        except Exception:
                            minutes = 60
                        start_dt = end_dt - timedelta(minutes=minutes)

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
                        if start_dt.tzinfo is None:
                            start_dt = make_aware(start_dt, tz)
                        else:
                            start_dt = start_dt.astimezone(tz)
                        if end_dt.tzinfo is None:
                            end_dt = make_aware(end_dt, tz)
                        else:
                            end_dt = end_dt.astimezone(tz)

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

                        ev = gcal.create_event('primary', event_body)
                        response_type         = 'event_success'

                        def safe_get_email(gcal, fallback):
                            try:
                                return gcal.service.http.credentials.id_token.get('email')
                            except (AttributeError, KeyError):
                                return fallback
                        response_content      = {
                            'event_title':   summary,
                            'connected_email': safe_get_email(gcal, request.user.email),
                            'event_link':    ev.get('htmlLink'),
                            'event_id':      ev.get('id'),
                            'created_start': (ev.get('start') or {}).get('dateTime') or (ev.get('start') or {}).get('date'),
                            'created_end':   (ev.get('end')   or {}).get('dateTime') or (ev.get('end')   or {}).get('date'),
                        }
                        # Persist the structured success card so it survives page reloads
                        try:
                            Message.objects.create(
                                conversation=convo,
                                sender='agent',
                                text='',
                                message_type='event_success',
                                content=response_content,
                            )
                        except Exception:
                            pass

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
                response_data.update({
                    'type': 'text',
                    'response': f"Sorry, I encountered an error while processing your calendar request: {str(e)}",
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