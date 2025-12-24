import json
from anthropic import Anthropic
from django.conf import settings
from .calendar_service import GoogleCalendarService
from allauth.socialaccount.models import SocialToken, SocialAccount
from django.contrib.auth import get_user_model
from django.urls import reverse
from typing import TYPE_CHECKING, Any
import os
import traceback

if TYPE_CHECKING:
    User = get_user_model()

# Get a logger instance
import logging
logger = logging.getLogger(__name__)

class AIAgent:
    def __init__(self, user: Any):
        self.user = user
        
        # ---- Initialise Anthropic (Claude) only ----
        anthropic_key = (
            os.getenv('CLAUDE_API_KEY') or os.getenv('ANTHROPIC_API_KEY')
        )
        self.claude_client = (
            Anthropic(api_key=anthropic_key) if anthropic_key else None
        )
        # Placeholder so any legacy call to self.openai_client is harmless.
        self.openai_client = None

        # ---- Claude model names (pick ones you can access) ----
        self.general_chat_model   = "claude-3-haiku-20240307"
        self.calendar_intent_model = self.general_chat_model
        self.calendar_param_model  = self.general_chat_model
        self.title_generation_model = self.general_chat_model

    def _get_openai_response(self, messages, json_mode: bool = False, temperature: float = 0.7, max_tokens: int   = 500):
        """Helper to call OpenAI API."""
        if not self.openai_client:
            logger.warning("OpenAI client not initialized. Cannot get response.")
            return None # Return None or raise error as appropriate

        try:
            response_format = {"type": "json_object"} if json_mode else {"type": "text"}
            # Use instance model or passed model
            model_to_use = self.general_chat_model
            temp_to_use = temperature
            tokens_to_use = max_tokens

            # Special case for title generation which might use different params
            if messages and messages[0].get('content', '').startswith("Based on this first message exchange"):
                 model_to_use = self.title_generation_model
                 temp_to_use = 0.1 # Lower temperature for more deterministic title
                 tokens_to_use = 20 # Max tokens for title

            resp = self.openai_client.chat.completions.create(
                model=model_to_use,
                messages=messages,
                temperature=temp_to_use,
                max_tokens=tokens_to_use,
                response_format=response_format,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"Error calling OpenAI API: {e}")
            logger.error(traceback.format_exc())
            # Depending on criticality, re-raise or return None
            raise e # Re-raise to be caught by calling handle method

    def _get_claude_response(self, messages):
        """Helper to call Claude API."""
        if not self.claude_client:
            logger.warning("Claude client not initialized. Cannot get response.")
            return None # Return None or raise error as appropriate
        try:
            # Use instance model or passed model
            model_to_use = self.calendar_intent_model # Default model
            temp_to_use = 0.7 # Default temperature
            tokens_to_use = 150 # Default max_tokens

            # Adjust params based on prompt (simple heuristic)
            prompt_content = messages[-1].get('content', '') if messages else ''
            if "Classify the user's intent" in prompt_content:
                 model_to_use = self.calendar_intent_model
                 temp_to_use = 0 # Deterministic intent
                 tokens_to_use = 20 # Short response expected
            elif "Extract the calendar action and parameters" in prompt_content:
                 model_to_use = self.calendar_param_model
                 temp_to_use = 0.3 # A bit more flexible than intent, but still structured
                 tokens_to_use = 300 # More tokens for JSON output


            resp = self.claude_client.messages.create(
                model       = self.general_chat_model,
                messages    = messages,
                temperature = temp_to_use,
                max_tokens  = min(tokens_to_use, 250),   # hard cap ≈ 1-2 short paragraphs
            )
            return resp.content[0].text.strip()
        except Exception as e:
            logger.error(f"Error calling Claude API: {e}")
            logger.error(traceback.format_exc())
            # Depending on criticality, re-raise or return None
            raise e # Re-raise to be caught by calling handle method

    def get_google_account_email(self):
        """Retrieves the connected Google account email."""
        try:
            social_account = SocialToken.objects.select_related('account').get(
                account__user=self.user,
                account__provider='google'
            ).account
            return social_account.extra_data.get('email')
        except (SocialToken.DoesNotExist, SocialAccount.DoesNotExist, AttributeError):
            return None

    def is_google_connected(self) -> bool:
        """Return True if the user has a linked Google account.

        Rely primarily on the presence of a SocialAccount row. Token
        presence varies across providers/flows, and we'll let the
        calendar service attempt a refresh when needed. This avoids
        getting stuck in a reconnect loop when the account is already
        linked but tokens are about to be refreshed.
        """
        try:
            return SocialAccount.objects.filter(
                user=self.user, provider="google"
            ).exists()
        except Exception:
            return False
        
    def determine_intent(self, text: str, conversation=None) -> str:
        """Uses Claude to classify the user's intent (calendar vs general_chat)."""
        if not self.claude_client:
            logger.warning("Claude client not initialized, defaulting intent to general_chat.")
            return 'general_chat'

        intent_prompt = (
            """You are a calendar assistant's intent classifier.

            Analyze the user's message and classify it as EITHER:
            - "calendar" - if the user wants to create, view, edit, delete, or manage calendar events/schedules
            - "general_chat" - for greetings, questions about capabilities, off-topic conversation, or unclear requests

            Calendar intent examples:
            - "Schedule a meeting tomorrow at 2pm"
            - "What's on my calendar next week?"
            - "Cancel my 3pm appointment"
            - "Delete the test meeting"
            - "Remove my dentist appointment"
            - "Find free time on Thursday"
            - "Add lunch with Sarah to my calendar"
            - "The one at 10am" (Context: answering "Which event?")
            - "Yes, delete it" (Context: confirming deletion)

            General chat examples:
            - "Hello!" / "Hi there"
            - "What can you do?"
            - "How's the weather?"
            - "Thanks!" / "That's helpful"

            Reply with ONLY the single word: "calendar" or "general_chat"

            User message: {user_message}"""
        )
        
        messages = []
        if conversation:
            # Include recent conversation history for context
            history_messages = conversation.messages.filter(text__isnull=False, text__gt='').order_by('-timestamp')[:4]
            history_messages = list(history_messages)[::-1]
            messages = [
                {"role": ("user" if m.sender == "user" else "assistant"), "content": m.text}
                for m in history_messages
            ]
            
        messages.append({"role": "user", "content": f"{intent_prompt}\n\nUser message: {text}"})
        
        try:
            # Use the specific model and low temperature for intent
            intent = self._get_claude_response(messages) # Model/temp handled in helper based on prompt
            intent = intent.strip().lower()
            if intent in ['calendar', 'general_chat']:
                logger.info(f"Intent detected: {intent}")
                return intent
            else:
                logger.warning(f"AI returned unknown intent '{intent}', defaulting to general_chat.")
                return 'general_chat'
        except Exception as e:
            logger.error(f"Error determining intent: {e}, defaulting to general_chat.")
            # Log the full traceback if needed, but return a default to keep the app running
            # traceback.print_exc()
            return 'general_chat'

    def extract_calendar_parameters(self, text: str) -> dict:
        """Uses Claude to extract parameters for calendar actions."""
        if not self.claude_client:
            logger.warning("Claude client not initialized. Cannot extract calendar parameters.")
            return {"action": "unknown", "params": {}, "details": "AI client not initialized."}

        parameter_prompt = (
            """You are a calendar parameter extractor. Extract structured data from the user's calendar request.

            ACTIONS: create_event, list_events, delete_event, update_event, find_free_slots, list_calendars

            REQUIRED FIELDS for create_event:
            - summary/title (what the event is about)
            - date (when it happens)
            - time information: EITHER (start + end) OR duration

            REQUIRED FIELDS for delete_event:
            - summary/title (to identify the event)
            - date (optional, to narrow down search)
            - start (optional, to disambiguate events)
            - end (optional, to disambiguate events)

            REQUIRED FIELDS for update_event:
            - summary/title (to identify the event to update)
            - updates object (containing fields to modify)
            - date (optional, to narrow down search)
            - start (optional, to disambiguate events if multiple matches)

            OPTIONAL FIELDS:
            - attendees (list of email addresses)

            RESPONSE FORMAT - Return ONLY valid JSON with double quotes, no markdown:
            {
            "action": "action_name",
            "params": {
                "summary": "event title",
                "date": "relative or absolute date",
                "start": "time or datetime",
                "end": "time or datetime",
                "duration": "minutes or 'X hours'",
                "attendees": ["email@example.com"],
                "updates": {
                    "summary": "new title",
                    "start": "new start time",
                    "end": "new end time",
                    "date": "new date"
                },
                "present": {},
                "missing": []
            },
            "details": "brief human summary of what was understood"
            }

            CRITICAL: Analyze what information IS present and what's MISSING:
            - "present" object: Include ANY fields you detected (summary, date, start, end, duration, attendees)
            - "missing" array: List required fields that are MISSING or UNCLEAR

            Examples:

            Input: "Schedule team meeting tomorrow 2-3pm"
            Output: {{"action": "create_event", "params": {{"summary": "team meeting", "date": "tomorrow", "start": "14:00", "end": "15:00", "present": {{"summary": "team meeting", "date": "tomorrow", "start": "14:00", "end": "15:00"}}, "missing": []}}, "details": "team meeting tomorrow 2-3pm"}}

            Input: "Book a dentist appointment"
            Output: {{"action": "create_event", "params": {{"summary": "dentist appointment", "present": {{"summary": "dentist appointment"}}, "missing": ["date", "time"]}}, "details": "dentist appointment"}}

            Input: "Meeting with John at 2pm"
            Output: {{"action": "create_event", "params": {{"summary": "meeting with John", "start": "14:00", "present": {{"summary": "meeting with John", "start": "14:00"}}, "missing": ["date", "end"]}}, "details": "meeting with John at 2pm"}}

            Input: "What's on my calendar tomorrow?"
            Output: {{"action": "list_events", "params": {{"date": "tomorrow"}}, "details": "list events tomorrow"}}

            Input: "Change my dentist appointment to 3pm"
            Output: {{"action": "update_event", "params": {{"summary": "dentist appointment", "updates": {{"start": "15:00"}}, "present": {{"summary": "dentist appointment", "updates": {{"start": "15:00"}}}}, "missing": []}}, "details": "update dentist appointment to 3pm"}}

            Input: "Move tomorrow's meeting to Friday"
            Output: {{"action": "update_event", "params": {{"summary": "meeting", "date": "tomorrow", "updates": {{"date": "Friday"}}, "present": {{"summary": "meeting", "date": "tomorrow", "updates": {{"date": "Friday"}}}}, "missing": []}}, "details": "move tomorrow's meeting to Friday"}}

            Input: "Reschedule the team sync to 4pm and rename it to standup"
            Output: {{"action": "update_event", "params": {{"summary": "team sync", "updates": {{"start": "16:00", "summary": "standup"}}, "present": {{"summary": "team sync", "updates": {{"start": "16:00", "summary": "standup"}}}}, "missing": []}}, "details": "update team sync to 4pm and rename to standup"}}

            Input: "Update the meeting at 10am to 2pm"
            Output: {{"action": "update_event", "params": {{"summary": "meeting", "start": "10:00", "updates": {{"start": "14:00"}}, "present": {{"summary": "meeting", "start": "10:00", "updates": {{"start": "14:00"}}}}, "missing": []}}, "details": "update meeting at 10am to 2pm"}}

            If unclear or not calendar-related: {{"action": "unknown", "params": {{}}, "details": "request unclear"}}

            User message: {user_message}"""
        )
        
        messages = [
            {"role": "user", "content": f"{parameter_prompt}\n\nUser message: {text}"}
        ]

        try:
            # Use the specific model for parameter extraction
            json_str = self._get_claude_response(messages) # Model/temp handled in helper based on prompt
            logger.debug(f"Claude parameter extraction raw response: {json_str}")
            # Attempt to parse the JSON string
            try:
                 extracted_data = json.loads(json_str)
                 # Basic validation of the JSON structure
                 if not isinstance(extracted_data, dict) or 'action' not in extracted_data or 'params' not in extracted_data or 'details' not in extracted_data:
                      logger.warning(f"AI returned invalid JSON structure: {extracted_data}")
                      return {"action": "unknown", "params": {}, "details": "Failed to extract details."} # Default to unknown if format is wrong
                 return extracted_data
            except json.JSONDecodeError:
                 logger.warning(f"AI returned non-JSON response for parameter extraction: {json_str}")
                 # If AI doesn't return valid JSON, treat as unknown intent
                 return {"action": "unknown", "params": {}, "details": "Failed to extract details."}

        except Exception as e:
            logger.error(f"Error extracting calendar parameters: {e}")
            logger.error(traceback.format_exc())
            # If API call fails, treat as unknown intent
            return {"action": "unknown", "params": {}, "details": f"Failed to extract details: {e}"}

    def handle(self, text: str, conversation=None, is_title_generation=False) -> dict:
        """
        Processes the user's message, determines intent, and returns a structured response
        indicating the next step (general chat, needs connection, or calendar action data).
        Does NOT perform calendar actions directly.
        """
        # Handle title generation separately if the flag is set
        if is_title_generation:
            if not self.claude_client:
                return {'type': 'text', 'response': "AI client not initialized for title generation."}
            try:
                messages = [{"role": "user", "content": text}]
                title = self._get_claude_chat_response(
                    messages,
                    temperature=0.1,
                    max_tokens=20,
                )
                return {'type': 'text', 'response': title}
            except Exception as e:
                logger.error(f"Error generating title: {e}")
                # Return a fallback or error message for title generation
                return {'type': 'text', 'response': "Error generating title."}

        # --- Main message handling logic ---
        if not self.claude_client:
            logger.warning("Claude client is not initialized.")
            return {
                'type': 'text',
                'response': "AI services are not configured. Please check the server settings."
            }

        # 1. Determine Intent (Calendar or General Chat)
        intent = self.determine_intent(text, conversation)
        logger.info(f"Message intent: {intent}")

        # 2. Handle based on Intent
        if intent == 'calendar':
            # Check Google Connection Status FIRST for calendar intents
            if not self.is_google_connected():
                logger.info("Calendar intent detected, but Google not connected. Requesting connection.")
                return {
                    'type': 'needs_connection',
                    'content': {
                        'email': self.get_google_account_email() or self.user.email, # Pass email if available
                        'message_for_user':(
                            "Sure – I can do that once you connect your Google account."
                        ),
                        'needs_connection': True
                    }
                }
            else:
                # If connected, proceed to extract calendar parameters
                logger.info("Google connected. Extracting calendar parameters with context...")
                # Get current date for AI context
                from datetime import datetime
                current_date = datetime.now().strftime("%Y-%m-%d")
                current_day = datetime.now().strftime("%A")
                
                system = (
                    f"""You are a calendar assistant. Today is {current_day}, {current_date}. Extract calendar actions from the user's CURRENT request only.

                    ⚠️ ULTRA-CRITICAL JSON-ONLY RULE ⚠️
                    YOU ARE A PARSER, NOT AN ASSISTANT. YOU DO NOT HAVE ACCESS TO THE CALENDAR.
                    YOU MUST RETURN VALID JSON ONLY. NO EXPLANATIONS. NO TEXT RESPONSES.
                    
                    ❌ FORBIDDEN - DO NOT DO THIS:
                    "I apologize, but I do not see..."
                    "Okay, got it. Here is the updated schedule..."
                    "The events I see are..."
                    "I cannot find any information about..."
                    
                    ✅ REQUIRED - ALWAYS DO THIS:
                    {{"action": "delete_event", "params": {{"summary": "event name"}}, "message_for_user": "Searching..."}}
                    
                    IF YOU RETURN ANYTHING OTHER THAN JSON, YOU HAVE FAILED.
                    DO NOT CHECK IF EVENTS EXIST. DO NOT LIST EVENTS. JUST EXTRACT PARAMETERS AS JSON.
                    EVEN IF YOU THINK THE EVENT DOES NOT EXIST, YOU MUST RETURN THE SEARCH QUERY SO THE SYSTEM CAN CHECK.

                    CRITICAL RULES:
                    1. Return EXACTLY ONE JSON object - never return multiple JSON objects
                    2. Process only the SINGLE action the user is requesting right now
                    3. If user mentions multiple time slots, create ONE event with the primary/main time they want
                    4. DO NOT create multiple events from a single request
                    5. DO NOT repeat previous actions from conversation history
                    6. Use dialogue history ONLY to resolve contextual references (like "that day", "same time")
                    7. ALWAYS use the current date {current_date} as reference for date calculations
                    8. NEVER use dates from past years - all dates should be relative to {current_date}
                    
                    SINGLE JSON RESPONSE FORMAT:
                    Return ONLY one JSON object, nothing else before or after it.
                    
                    ACTIONS: create_event, list_events, delete_event, find_free_slots, list_calendars
                    
                    For list_events:
                    - Extract time range from user's request ("this week", "tomorrow", "next Monday", "this month", "this year", "month")
                    - Extract search terms/keywords ONLY from the CURRENT user message (not from conversation history)
                    - CRITICAL: If the user asks for different events than before (e.g., previously "standup", now "Bible study"), extract the NEW search terms
                    - Examples of search terms: "standup", "meeting with John", "Bible study", "miracle hour", "dentist", etc.
                    - ALWAYS calculate dates relative to TODAY ({current_date})
                    - Return: {{"action": "list_events", "params": {{"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", "queries": ["term1", "term2"]}}, "message_for_user": "..."}}
                    
                    SEARCH TERM EXTRACTION EXAMPLES:
                    Current: "Find my standup meetings" → queries: ["standup"]
                    Current: "Show me Bible study and miracle hour" → queries: ["Bible study", "miracle hour"]
                    Current: "When do I have dentist appointments?" → queries: ["dentist"]
                    
                    CONTEXT RESOLUTION (use history to understand references):
                    - "that day" / "the same day" / "same day" → the MOST RECENT date mentioned in conversation
                    - "same time" / "at the same time" → the time from the last event created
                    - "with them too" → attendees mentioned before
                    - "for the same duration" → duration from previous context
                    
                    EXAMPLES:
                    Previous: "Create meeting on Thursday at 2pm"
                    Current: "Schedule another at 4pm on the same day"
                    → Extract ONLY: {{"action": "create_event", "params": {{"summary": "another", "date": "Thursday", "start": "16:00"}}, ...}}
                    
                    Previous: "Book dentist Tuesday 9am to 10am"
                    Current: "Add lunch same day at noon"
                    → Extract ONLY: {{"action": "create_event", "params": {{"summary": "lunch", "date": "Tuesday", "start": "12:00"}}, ...}}

                    ACTIONS: create_event, list_events, delete_event, update_event, find_free_slots, list_calendars

                    For create_event, REQUIRED fields:
                    - Event title/summary
                    - Date (explicit or relative)
                    - Time: EITHER (start + end times) OR duration
                    
                    For delete_event, REQUIRED fields:
                    - summary (event title to identify and delete)
                    - date (optional, defaults to today if not specified)
                    
                    For update_event, REQUIRED fields:
                    - summary (event title to identify the event)
                    - updates (object containing fields to modify: start, end, date, summary, etc.)
                    - date (optional, to narrow down search for the event to update)
                    - start (optional, to disambiguate if multiple events match)
                    - update_series (boolean, MUST be true if user wants to update ALL instances/the entire series/recurring event)
                    
                    DETECTING SERIES UPDATES - Set update_series to TRUE if the user says:
                    - "all instances"
                    - "all of them"
                    - "every instance"
                    - "the whole series"
                    - "the recurring event"
                    - "every occurrence"
                    - "all future instances"
                    - Or asks to update a recurring event by name without specifying a single instance
                    
                    CRITICAL FOR UPDATE_EVENT:
                    - ALWAYS return the update_event action as JSON, NEVER respond with explanatory text
                    - DO NOT check if the event exists - just extract the parameters
                    - The backend will handle searching for and verifying the event
                    - The "updates" object should contain ONLY the fields the user wants to change
                    - Return format: {{"action": "update_event", "params": {{"summary": "event name", "updates": {{"start": "15:00"}}}}, "message_for_user": "Looking for event to update..."}}
                    
                    UPDATE EXAMPLES:
                    User: "Change my dentist appointment to 3pm"
                    Response: {{"action": "update_event", "params": {{"summary": "dentist appointment", "updates": {{"start": "15:00"}}}}, "message_for_user": "Looking for dentist appointment to update..."}}
                    
                    User: "Move tomorrow's meeting to Friday"
                    Response: {{"action": "update_event", "params": {{"summary": "meeting", "date": "tomorrow", "updates": {{"date": "Friday"}}}}, "message_for_user": "Looking for tomorrow's meeting to reschedule..."}}
                    
                    User: "Reschedule the team sync to 4pm and rename it to standup"
                    Response: {{"action": "update_event", "params": {{"summary": "team sync", "updates": {{"start": "16:00", "summary": "standup"}}}}, "message_for_user": "Looking for team sync to update..."}}
                    
                    User: "Update the meeting at 10am to 2pm"
                    Response: {{"action": "update_event", "params": {{"summary": "meeting", "start": "10:00", "updates": {{"start": "14:00"}}}}, "message_for_user": "Looking for the meeting at 10am to update..."}}
                    
                    User: "Change the lunch meeting to 1 hour earlier"
                    Response: {{"action": "update_event", "params": {{"summary": "lunch meeting", "updates": {{"time_shift": "-1 hour"}}}}, "message_for_user": "Looking for lunch meeting to reschedule..."}}

                    User: "Update all instances of the weekly meeting to 3pm"
                    Response: {{"action": "update_event", "params": {{"summary": "weekly meeting", "update_series": true, "updates": {{"start": "15:00"}}}}, "message_for_user": "Looking for weekly meeting series to update..."}}
                    
                    User: "Update all instances of prayer meeting to 10pm to 11pm"
                    Response: {{"action": "update_event", "params": {{"summary": "prayer meeting", "update_series": true, "updates": {{"start": "22:00", "end": "23:00"}}}}, "message_for_user": "Looking for prayer meeting series to update..."}}
                    
                    User: "All of them" (in context of updating a recurring event)
                    Response: {{"action": "update_event", "params": {{"summary": "event name from context", "update_series": true, "updates": {{from context}}}}, "message_for_user": "Updating all instances..."}}
                    
                    CRITICAL FOR DELETE_EVENT:
                    - ALWAYS return the delete_event action as JSON, NEVER respond with explanatory text
                    - DO NOT check if the event exists - just extract the parameters
                    - The backend will handle searching for and verifying the event
                    - Return format: {{"action": "delete_event", "params": {{"summary": "event name", "date": "YYYY-MM-DD"}}, "message_for_user": "Searching for event to delete..."}}
                    
                    DELETE EXAMPLES:
                    User: "Delete the test meeting"
                    Response: {{"action": "delete_event", "params": {{"summary": "test meeting"}}, "message_for_user": "Looking for the test meeting to delete..."}}
                    
                    User: "Remove my dentist appointment tomorrow"  
                    Response: {{"action": "delete_event", "params": {{"summary": "dentist appointment", "date": "YYYY-MM-DD"}}, "message_for_user": "Searching for dentist appointment..."}}
                    
                    User: "Cancel the team sync on Friday"
                    Response: {{"action": "delete_event", "params": {{"summary": "team sync", "date": "YYYY-MM-DD"}}, "message_for_user": "Looking for team sync to cancel..."}}

                    User: "The one at 10am" (Context: clarifying which event to delete)
                    Response: {{"action": "delete_event", "params": {{"summary": "event name from context", "date": "YYYY-MM-DD", "start": "10:00"}}, "message_for_user": "Looking for the event at 10am to delete..."}}

                    User: "The first one" (Context: clarifying which event to delete)
                    Response: {{"action": "delete_event", "params": {{"summary": "event name from context", "date": "YYYY-MM-DD", "match_index": 1}}, "message_for_user": "Deleting the first event..."}}
                    
                    User: "Delete the second meeting"
                    Response: {{"action": "delete_event", "params": {{"summary": "meeting", "date": "YYYY-MM-DD", "match_index": 2}}, "message_for_user": "Deleting the second meeting..."}}

                    User: "Delete all events tomorrow"
                    Response: {{"action": "delete_event", "params": {{"delete_all": true, "date": "tomorrow"}}, "message_for_user": "Deleting all events for tomorrow..."}}

                    User: "Clear my calendar for next week"
                    Response: {{"action": "delete_event", "params": {{"delete_all": true, "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}}, "message_for_user": "Clearing calendar for next week..."}}

                    User: "Delete everything on my calendar"
                    Response: {{"action": "delete_event", "params": {{"delete_all": true, "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}}, "message_for_user": "Clearing entire calendar..."}} (Set range to cover reasonable future, e.g. 1-2 years)
                    
                    OPTIONAL fields:
                    - Recurrence: If user mentions repetition (e.g. "every Monday", "daily", "weekly"), extract as RRULE string (RFC 5545).
                      Examples:
                      - "every Monday" -> "RRULE:FREQ=WEEKLY;BYDAY=MO"
                      - "daily" -> "RRULE:FREQ=DAILY"
                      - "every month on the 1st" -> "RRULE:FREQ=MONTHLY;BYMONTHDAY=1"
                      - "every weekday" -> "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
                      - "until Dec 31, 2025" -> "RRULE:FREQ=DAILY;UNTIL=20251231T235959Z" (IMPORTANT: UNTIL must be UTC YYYYMMDDTHHMMSSZ, no hyphens)

                    RESPONSE FORMAT - Single JSON object only, no markdown, double quotes:
                    {{
                    "action": "create_event",
                    "params": {{
                        "summary": "event title",
                        "date": "date reference",
                        "start": "start time",
                        "end": "end time",
                        "duration": "duration if provided instead of end",
                        "recurrence": "RRULE string (optional)",
                        "attendees": ["emails"],
                        "present": {{}},
                        "missing": []
                    }},
                    "message_for_user": "brief confirmation",
                    "agent_explanation": "Complete explanation of task, missing info, and assumptions in natural language"
                    }}

                    FIELD DETECTION:
                    - "present": Include ALL detected fields as an object
                    - "missing": Array of required fields that are unclear or absent
                    
                    AGENT EXPLANATION FOR SUCCESS RESPONSES:
                    - "agent_explanation": Generate a natural, formatted explanation that covers:
                        * What task was performed
                        * Any information that was missing from the user's request
                        * Any assumptions you made to complete the task
                        * Mention recurrence if applicable
                    - Format as readable text with line breaks or bullets as appropriate
                    - Only include relevant sections (don't mention missing info if nothing was missing)
                    - Example: "Created meeting for Friday 1-2pm. Since no location was specified, I set it as a virtual meeting. Used default calendar since none was specified."

                    If information is missing: {{"action": "create_event", "params": {{"present": {{detected fields}}, "missing": ["field1", "field2"]}}, "message_for_user": "clarifying question"}}

                    If unclear or error: {{"action": "unknown", "params": {{}}, "message_for_user": "error explanation"}}"""
                )

                # Include brief conversation history for better parameter extraction
                messages_history = []
                if conversation:
                    history_messages = conversation.messages.filter(text__isnull=False, text__gt='').order_by('-timestamp')[:6]
                    history_messages = list(history_messages)[::-1]
                    messages_history = [
                        {"role": ("user" if m.sender == "user" else "assistant"), "content": m.text}
                        for m in history_messages
                    ]
                # Inject a strong system override to the user's message to break refusal loops
                # This ensures the AI ignores previous "I can't find it" messages in the history
                override_instruction = (
                    "\n\n[SYSTEM INSTRUCTION: "
                    "If you previously refused this request or said you couldn't find events, IGNORE that refusal. "
                    "The user is asking again. You MUST extract the search query now so the system can perform a fresh search. "
                    "Do not refuse. Output the JSON with the query.]"
                )
                
                messages = messages_history + [{"role": "user", "content": text + override_instruction}]
                raw = self._get_claude_chat_response(messages, system_prompt=system, temperature=0)
                logger.debug(f"AI RAW RESPONSE: {raw}")
                # Some models occasionally emit multiple JSON objects back-to-back.
                # Extract the last valid JSON object to avoid "Extra data" errors.

                def _extract_last_json(blob: str):
                    if not blob:
                        return None
                    s = str(blob).strip()
                    # Fast path: single JSON
                    try:
                        return json.loads(s)
                    except Exception:
                        pass
                    # Fallback: scan for top-level {...} blocks
                    objs = []
                    depth = 0
                    start = None
                    for idx, ch in enumerate(s):
                        if ch == '{':
                            if depth == 0:
                                start = idx
                            depth += 1
                        elif ch == '}':
                            if depth > 0:
                                depth -= 1
                                if depth == 0 and start is not None:
                                    candidate = s[start:idx+1]
                                    try:
                                        obj = json.loads(candidate)
                                        objs.append(obj)
                                    except Exception:
                                        pass
                                    start = None
                    
                    # Handle multiple JSON objects intelligently
                    if len(objs) > 1:
                        logger.warning(f"⚠️ WARNING: AI returned {len(objs)} JSON objects instead of 1. Selecting the best valid action.")
                        for i, obj in enumerate(objs):
                            action = obj.get('action', 'unknown')
                            print(f"   Object {i+1}: action={action}")
                        
                        # Prefer the first valid create_event/list_events action over 'unknown' actions
                        valid_actions = ['create_event', 'list_events', 'delete_event', 'find_free_slots']
                        for obj in objs:
                            if obj.get('action') in valid_actions:
                                logger.info(f"   Selected: {obj.get('action')} (first valid action)")
                                return obj
                        
                        # If no valid actions found, take the last one as fallback
                        logger.warning(f"   No valid actions found, using last object: {objs[-1].get('action')}")
                        return objs[-1]
    
                    return objs[-1] if objs else None

                extracted_data = _extract_last_json(raw)
                if not isinstance(extracted_data, dict):
                    logger.error(f"Failed to parse AI response as JSON: {raw}")
                    # FIX: If the response is a plain text string (e.g. a refusal), return it as text
                    # instead of trying to parse it as a calendar action.
                    if isinstance(raw, str) and raw.strip() and not raw.strip().startswith('{'):
                         return {'type': 'text', 'response': raw.strip()}

                    fallback = self.summarize_user_fields(text)
                    clarification = self.build_missing_fields_message(
                        fallback.get('present', {}),
                        fallback.get('missing', []),
                        ""
                    )
                    return { 'type': 'text', 'response': clarification }
                
                action = extracted_data.get('action')
                
                # Validate that the action matches the user's intent
                # Use precise patterns to catch genuine list/view requests without false positives
                import re
                create_vs_list_patterns = [
                    r'\bwhat.*(?:events?|meetings?|scheduled?)\b',     # "what events do I have"
                    r'\bshow.*(?:events?|calendar|schedule)\b',        # "show my calendar"  
                    r'\blist.*(?:events?|meetings?)\b',                # "list events"
                    r'\bcheck.*(?:calendar|schedule)\b',               # "check my schedule"
                    r'\b(?:what\'s|whats).*(?:on|in).*(?:calendar|schedule)\b',  # "what's on my calendar"
                ]
                
                # Only override if it clearly matches a list/view pattern AND doesn't have create keywords
                has_list_intent = any(re.search(pattern, text.lower()) for pattern in create_vs_list_patterns)
                has_create_keywords = re.search(r'\b(?:create|schedule|book|add|make|set up|arrange)\b', text.lower())
                
                if action == 'create_event' and has_list_intent and not has_create_keywords:
                    logger.warning(f"⚠️ WARNING: AI returned 'create_event' but user message appears to be a list/view request. Correcting to 'list_events'")
                    action = 'list_events'
                    extracted_data['action'] = 'list_events'

                params = extracted_data.get('params', {})
                # Some prompts may return message_for_user instead of details
                details = extracted_data.get('details') or extracted_data.get('message_for_user') or ''
                error = extracted_data.get('error')

                if error:
                     logger.warning(f"Parameter extraction failed: {error}")
                     return {
                         'type': 'text',
                         'response': error # Return the error from extraction
                     }

                # Tailored clarification using present/missing if available
                missing = params.get('missing') or params.get('needs_clarification')
                present = params.get('present', {})
                if missing:
                    clarification_text = self.build_missing_fields_message(present, missing, details)
                    logger.info(f"Extraction needs clarification: {clarification_text}")
                    return { 'type': 'text', 'response': clarification_text }
                
                # If parameters are extracted and no clarification is needed,
                # return a structured response indicating the *intended* calendar action
                # The view (chat_process) will then perform the action.
                # Avoid logging raw params because upstream models sometimes emit stale absolute
                # datetimes (e.g., year 2023 or wrong hours). The view will normalize date/time
                # using the user's message and timezone, so this log would be misleading.
                logger.info(f"Parameters extracted successfully. Signalling view to perform action: {action}.")
                return {
                    'type': 'calendar_action_request', # New type to signal the view
                    'content': {
                        'action': action,
                        'params': params,
                        'details': details, # Keep the human-readable details
                        'agent_explanation': extracted_data.get('agent_explanation', '')
                    }
                }

        else: # intent == 'general_chat'
            logger.info("General chat intent detected. Using Claude.")
            try:
                system = (
                    """You are a friendly calendar assistant. Your primary role is managing calendars, but you can engage in brief, relevant conversation.

                    CRITICAL RULES:
                    - NEVER create, delete, modify, or confirm calendar events directly in chat responses
                    - You cannot perform calendar actions - you can only discuss them
                    - If asked about calendar management, explain what you CAN do but don't actually do it
                    - DO NOT repeat information you've already provided in this conversation
                    - Give fresh, direct answers to each question
                    - If asked the same question twice, acknowledge briefly and offer something new
                    - Complete your thoughts fully - don't cut off mid-sentence

                    PERSONALITY:
                    - Helpful and professional
                    - Concise (respond in 2-4 short sentences maximum)
                    - Calendar-focused but conversational
                    - Proactive in offering calendar help when relevant

                    CAPABILITIES to mention when asked (only if not recently covered):
                    1. **Create events** - Schedule meetings, appointments, reminders
                        Example: "Schedule team sync tomorrow at 2pm"

                    2. **List events** - Show upcoming meetings and appointments
                        Example: "List my events for today"

                    3. **View calendar** - Check what's scheduled for any day/week  
                        Example: "What's on my calendar Thursday?"

                    4. **Find free time** - Locate available slots for scheduling  
                        Example: "When am I free next week?"

                    5. **Update events** - Change time, date, or title of existing events
                        Example: "Move my 2pm meeting to 3pm"

                    6. **Delete events** - Remove unwanted appointments  
                        Example: "Delete my dentist appointment"


                    When listing capabilities, use the format shown above with numbered items, bold capability names, descriptions on the same line ending with two spaces, and examples indented on the next line.
                    When asked for capabilities, abilities or functions, DO NOT say " I can't directly create, delete, or modify events in your calendar,"

                    Keep responses warm but brief. Redirect off-topic conversations gently toward calendar assistance."""
                )
                
                messages_history = []
                if conversation:
                    # Fetch recent messages (limited to 4 for context, excluding empty ones)
                    history_messages = conversation.messages.filter(text__isnull=False, text__gt='').order_by('-timestamp')[:4]
                    history_messages = list(history_messages)[::-1]  # Reverse to get chronological order
                    
                    # Add deduplication and filtering logic
                    seen_content = set()
                    for m in history_messages:
                        if m.text and m.text.strip():
                            # Skip if we've seen very similar content (first 50 chars)
                            content_key = m.text.strip()[:50].lower()
                            if content_key not in seen_content:
                                seen_content.add(content_key)
                                messages_history.append({
                                    "role": "user" if m.sender == "user" else "assistant", 
                                    "content": m.text.strip()
                                })
                    
                    logger.debug(f"Including {len(messages_history)} unique history messages in general chat prompt.")

                # Add the current user message
                messages_history.append({"role": "user", "content": text})
                
                # Get response from Claude (system prompt passed separately)
                content = self._get_claude_chat_response(
                    messages_history,
                    system_prompt=system,
                    max_tokens=400,      # increased budget for complete responses
                )

                if content is None:
                     return {
                        'type': 'text',
                        'response': "Sorry, I couldn't get a response from the AI for general chat."
                     }

                # Validate response completeness
                if content and len(content.strip()) > 0:
                    # Check if response seems incomplete (ends mid-sentence)
                    if content.rstrip().endswith(('...', ',', 'and', 'or', 'but', 'because', 'so', 'that', 'which', 'who')):
                        print(f"Warning: Response may be incomplete: '{content[-20:]}'")
                    
                    print("Generated general chat response.")
                    return {
                        'type': 'text',
                        'response': content.strip()
                    }
                else:
                    return {
                        'type': 'text',
                        'response': "Sorry, I couldn't generate a proper response. Please try rephrasing your question."
                    }
            except Exception as e:
                print(f"Error in general chat handling: {e}")
                traceback.print_exc()
                return {
                    'type': 'text',
                    'response': "Sorry, I'm having trouble processing that request right now."
                }

    # -----------------------------------------------------------
    # Generic Claude-chat helper (used for titles & normal chat)
    # -----------------------------------------------------------
    def _get_claude_chat_response(
        self,
        messages,
        *,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 500,
    ):
        if not self.claude_client:
            print("Claude client not initialised.")
            return None
            
        try:
            params = dict(
                model       = self.general_chat_model,
                messages    = messages,
                temperature = temperature,
                max_tokens  = min(max_tokens, 800),
            )
            if system_prompt:            # only include when non-empty
                params["system"] = system_prompt

            resp = self.claude_client.messages.create(**params)
            return resp.content[0].text.strip()
        except Exception as e:
            print(f"Error calling Claude API in chat_response: {e}")
            traceback.print_exc()
            return None

    def summarize_user_fields(self, text: str) -> dict:
        """Lightweight fallback to identify present/missing fields when main JSON parse fails."""
        if not self.claude_client:
            return {"present": {}, "missing": ["date", "time", "summary"]}
        system = (
            """Extract calendar event information for validation.

            Analyze the user's message and return ONLY JSON with two keys:

            {
            "present": {object with any detected fields},
            "missing": [array of missing required fields]
            }

            DETECTED FIELDS (include in "present" if found):
            - "summary": event title/subject
            - "date": any date reference (relative or absolute)
            - "start": start time
            - "end": end time
            - "duration": event length
            - "attendees": array of email addresses

            REQUIRED FIELDS (include in "missing" if absent):
            - A date (relative like "tomorrow" or absolute)
            - Time information: EITHER (start + end) OR duration
            - A summary/title

            Examples:

            Input: "Lunch tomorrow"
            Output: {{"present": {{"summary": "lunch", "date": "tomorrow"}}, "missing": ["time"]}}

            Input: "2pm to 3pm meeting with John"
            Output: {{"present": {{"start": "14:00", "end": "15:00", "summary": "meeting with John"}}, "missing": ["date"]}}

            Input: "Schedule something"
            Output: {{"present": {{}}, "missing": ["summary", "date", "time"]}}"""
        )
        
        messages = [{"role": "user", "content": text}]
        raw = self._get_claude_chat_response(messages, system_prompt=system, temperature=0)
        try:
            data = json.loads(raw)
            present = data.get("present", {}) if isinstance(data, dict) else {}
            missing = data.get("missing", []) if isinstance(data, dict) else []
            return {"present": present, "missing": missing}
        except Exception:
            return {"present": {}, "missing": ["date", "time", "summary"]}

    def build_missing_fields_message(self, present: dict, missing: list, details: str = "") -> str:
        """Compose a short, specific clarification message based on detected vs missing fields."""
        understood_parts = []
        if present.get("summary"):
            understood_parts.append(f"title '{present.get('summary')}'")
        # Prefer explicit date over parsing from start
        if present.get("date"):
            understood_parts.append(f"on {present.get('date')}")
        if present.get("start") and present.get("end"):
            understood_parts.append(f"from {present.get('start')} to {present.get('end')}")
        elif present.get("duration") and (present.get("start") or present.get("date")):
            understood_parts.append(f"for {present.get('duration')}")
        if present.get("attendees"):
            understood_parts.append("with attendees")

        prefix = "Got it" if understood_parts else "I can schedule that"
        understood_text = (
            f"{prefix} — {' '.join(understood_parts)}." if understood_parts else f"{prefix}."
        )

        # Normalize missing labels into user-friendly phrasing
        pretty_map = {
            "date": "date",
            "time": "start time and end time (or duration)",
            "start": "start time",
            "end": "end time",
            "duration": "duration",
            "summary": "title/subject",
        }
        pretty_missing = []
        seen = set()
        for m in missing or []:
            label = pretty_map.get(m, m)
            if label not in seen:
                seen.add(label)
                pretty_missing.append(label)

        if not pretty_missing:
            # Fallback generic ask (should rarely happen)
            pretty_missing = ["date", "start time and end time (or duration)", "title/subject"]

        if len(pretty_missing) <= 2:
            ask = "Please share " + " and ".join(pretty_missing) + "."
        else:
            ask = "Please share:\n- " + "\n- ".join(pretty_missing)

        optional = " Attendees' emails are optional."
        return (details + "\n" if details else "") + understood_text + " " + ask + optional

    def generate_reminder_message(self, event_summary: str, start_dt: str, user_name: str) -> str:
        """
        Generates a warm, human-friendly reminder message using Claude.
        """
        if not self.claude_client:
            return f"Hi {user_name}, this is a reminder for your event '{event_summary}' starting at {start_dt}."

        system = (
            """You are a helpful and warm personal assistant.
            Your task is to write a short, friendly reminder message for a user's upcoming calendar event.
            
            GUIDELINES:
            - Tone: Warm, natural, and helpful (not robotic).
            - Content: Mention the event title and the start time clearly.
            - Length: Keep it concise (1-2 sentences). fit for a WhatsApp message or short email.
            - Format: Plain text only, no markdown.
            - Emojis: Use 1-3 relevant emojis to make the message friendly and engaging (e.g., 📅, ⏰, 👋).
            - Avoid: "2025-12-17T18:00:00+01:00" formats. Use natural time conventions (e.g., "at 6pm", "in 30 minutes").
            
            Example Input: Event 'Team Sync' at '2025-12-17 14:00' for 'Joshua'
            Example Output: Hi Joshua! 👋 Just a heads up that your 'Team Sync' is starting soon at 2pm ⏰.
            """
        )

        user_content = f"Write a reminder for {user_name} about their event '{event_summary}' which starts at {start_dt}."
        
        messages = [{"role": "user", "content": user_content}]
        
        try:
            response = self._get_claude_chat_response(messages, system_prompt=system, temperature=0.7, max_tokens=100)
            if response:
                return response
        except Exception as e:
            print(f"Error generating AI reminder: {e}")
        
        # Fallback if AI fails
        return f"Hi {user_name}, reminder: your event '{event_summary}' is starting at {start_dt}."

    def generate_morning_briefing(self, user_name: str, events: list, weather_info: str = "Clear skies expected") -> str:
        """
        Generates a morning briefing summary using Claude.
        """
        if not self.claude_client:
            return f"Good morning {user_name}! You have {len(events)} events today."

        # Format events for the prompt
        events_text = "No events scheduled for today."
        if events:
            events_lines = []
            for e in events:
                start = e['start'].get('dateTime', e['start'].get('date'))
                summary = e.get('summary', 'No Title')
                events_lines.append(f"- {summary} at {start}")
            events_text = "\n".join(events_lines)

        system_prompt = (
            "You are a helpful, enthusiastic personal assistant. "
            "Your goal is to provide a concise, motivating morning briefing."
        )

        user_prompt = (
            f"Generate a morning briefing for {user_name}.\n\n"
            f"Weather: {weather_info}\n"
            f"Today's Schedule:\n{events_text}\n\n"
            "Keep it encouraging, mention the weather, and summarize the day's load. "
            "If there are key events, highlight them. Keep it under 150 words."
        )

        return self._get_claude_chat_response(
            messages=[{"role": "user", "content": user_prompt}],
            system_prompt=system_prompt,
            temperature=0.7
        )

    def generate_welcome_message(self, user_name: str) -> str:
        """
        Generates a detailed, AI-driven welcome message guiding the user on features.
        """
        # Improved Fallback with Markdown for persistence if AI fails
        fallback = (
             f"**Hello {user_name}!** I'm your Meeting Scheduler Assistant. 👋\n\n"
             "Here is how I can help you stay organized:\n\n"
             "1. **Calendar Management**: Ask me to schedule, update, or delete events. \n"
             "   *Example: \"Schedule a team sync for Friday at 2pm\"*\n"
             "2. **Reminders**: I can send WhatsApp and Email reminders for your events.\n"
             "3. **Morning Briefings**: I can provide a daily summary of your schedule.\n"
             "4. **Settings**: Check the Settings page to configure your notification preferences.\n\n"
             "Let's get started! Try scheduling something now."
        )

        if not self.claude_client:
            return fallback

        system_prompt = (
            "You are a sophisticated, helpful AI assistant for a Meeting Scheduler application. "
            "You have capabilities to manage Google Calendar events, send WhatsApp/Email reminders, "
            "and provide daily morning briefings."
        )

        prompt = (
            f"Generate a warm, detailed welcome message for a new user named {user_name}. "
            "The message should be structured (you can use markdown like bullet points) and explain how to use your features:\n"
            "1. **Calendar Management**: Explain you can schedule, update, list and delete events using natural language (e.g., 'Schedule a team sync for Friday').\n"
            "2. **Reminders**: Mention you can send reminders via WhatsApp and Email for upcoming events (configurable in Settings).\n"
            "3. **Morning Briefings**: Mention you can provide a daily summary of their schedule every morning.\n"
            "4. **Settings**: Guide them to the Settings page to configure their notification preferences and numbers.\n\n"
            "End with an encouraging CTA to try scheduling something now. Keep the tone professional yet friendly and enthusiastic. "
            "**Use emojis generously to make the message visually engaging and friendly.**"
        )

        resp = self._get_claude_chat_response(
             messages=[{"role": "user", "content": prompt}],
             system_prompt=system_prompt,
             temperature=0.7,
             max_tokens=600 
        )
        
        return resp if resp else fallback