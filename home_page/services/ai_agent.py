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
            print("Warning: OpenAI client not initialized. Cannot get response.")
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
            print(f"Error calling OpenAI API: {e}")
            traceback.print_exc()
            # Depending on criticality, re-raise or return None
            raise e # Re-raise to be caught by calling handle method

    def _get_claude_response(self, messages):
        """Helper to call Claude API."""
        if not self.claude_client:
            print("Warning: Claude client not initialized. Cannot get response.")
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
            print(f"Error calling Claude API: {e}")
            traceback.print_exc()
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
        
    def determine_intent(self, text: str) -> str:
        """Uses Claude to classify the user's intent (calendar vs general_chat)."""
        if not self.claude_client:
            print("Claude client not initialized, defaulting intent to general_chat.")
            return 'general_chat'

        intent_prompt = (
            "Classify the user's intent based on the following message. "
            "Choose one of the following categories: 'calendar', 'general_chat'. "
            "If the intent is unclear or doesn't fit 'calendar', default to 'general_chat'. "
            "Reply ONLY with the category name, e.g., 'calendar' or 'general_chat'."
        )
        messages = [
             # Optional: include some conversation history for better context
            # {"role": "user" if m.sender == "user" else "assistant", "content": m.text}
            # for m in conversation.messages.order_by('-timestamp')[:5][::-1] if conversation and m.text and m.text.strip()
            # Always include the current message
            {"role": "user", "content": f"{intent_prompt}\n\nUser message: {text}"}
        ]
        
        try:
            # Use the specific model and low temperature for intent
            intent = self._get_claude_response(messages) # Model/temp handled in helper based on prompt
            intent = intent.strip().lower()
            if intent in ['calendar', 'general_chat']:
                print(f"Intent detected: {intent}")
                return intent
            else:
                print(f"AI returned unknown intent '{intent}', defaulting to general_chat.")
                return 'general_chat'
        except Exception as e:
            print(f"Error determining intent: {e}, defaulting to general_chat.")
            # Log the full traceback if needed, but return a default to keep the app running
            # traceback.print_exc()
            return 'general_chat'

    def extract_calendar_parameters(self, text: str) -> dict:
        """Uses Claude to extract parameters for calendar actions."""
        if not self.claude_client:
            print("Claude client not initialized. Cannot extract calendar parameters.")
            return {"action": "unknown", "params": {}, "details": "AI client not initialized."}

        parameter_prompt = (
            "Extract the primary calendar action, parameters, and a brief human-readable summary from the user's message. "
            "Identify the action (e.g., \"create_event\", \"list_events\", \"delete_event\", \"find_free_slots\", \"list_calendars\"). "
            "Extract relevant parameters such as event \"summary\", \"start\" time/date, \"end\" time/date, \"duration\", \"attendees\" (list of emails). "
            "Additionally, return two lists within params: \"present\" (object of any values confidently detected among summary/date/start/end/duration/attendees) and \"missing\" (array of required fields still needed). "
            "Required fields for create_event are: a date (or explicit start date), time info (either start+end or duration), and a title/summary. Attendees are optional. "
            "If the user gives only a date, mark start/end or duration as missing accordingly. If they give only time without date, mark date as missing. "
            "Return a JSON object ONLY, no markdown/back-ticks, using DOUBLE quotes. "
            "Format: {\"action\": \"action_name\", \"params\": {\"summary\": \"…\", \"start\": \"…\", \"end\": \"…\", \"duration\": …, \"attendees\": [\"…\"], \"present\": {…}, \"missing\": [ … ]}, \"details\": \"human-readable summary\"}."
            "Example create_event: {\"action\": \"create_event\", \"params\": {\"summary\": \"Meeting with John\", \"start\": \"tomorrow 14:00\", \"end\": \"tomorrow 15:00\", \"attendees\": [\"john@example.com\"]}, \"details\": \"create a meeting with John tomorrow from 2 to 3 pm\"}."
            "If the action or parameters are unclear or not calendar-related, use \"action\": \"unknown\"."
        )
        
        messages = [
            # Optional: include some conversation history for context
            # {"role": "user" if m.sender == "user" else "assistant", "content": m.text}
            # for m in conversation.messages.order_by('-timestamp')[:5][::-1] if conversation and m.text and m.text.strip()
            # Always include the current message
            {"role": "user", "content": f"{parameter_prompt}\n\nUser message: {text}"}
        ]

        try:
            # Use the specific model for parameter extraction
            json_str = self._get_claude_response(messages) # Model/temp handled in helper based on prompt
            print(f"Claude parameter extraction raw response: {json_str}")
            # Attempt to parse the JSON string
            try:
                 extracted_data = json.loads(json_str)
                 # Basic validation of the JSON structure
                 if not isinstance(extracted_data, dict) or 'action' not in extracted_data or 'params' not in extracted_data or 'details' not in extracted_data:
                      print(f"AI returned invalid JSON structure: {extracted_data}")
                      return {"action": "unknown", "params": {}, "details": "Failed to extract details."} # Default to unknown if format is wrong
                 return extracted_data
            except json.JSONDecodeError:
                 print(f"AI returned non-JSON response for parameter extraction: {json_str}")
                 # If AI doesn't return valid JSON, treat as unknown intent
                 return {"action": "unknown", "params": {}, "details": "Failed to extract details."}

        except Exception as e:
            print(f"Error extracting calendar parameters: {e}")
            traceback.print_exc()
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
                print(f"Error generating title: {e}")
                # Return a fallback or error message for title generation
                return {'type': 'text', 'response': "Error generating title."}

        # --- Main message handling logic ---
        if not self.claude_client:
            print("Claude client is not initialized.")
            return {
                'type': 'text',
                'response': "AI services are not configured. Please check the server settings."
            }

        # 1. Determine Intent (Calendar or General Chat)
        intent = self.determine_intent(text)
        print(f"Message intent: {intent}")

        # 2. Handle based on Intent
        if intent == 'calendar':
            # Check Google Connection Status FIRST for calendar intents
            if not self.is_google_connected():
                print("Calendar intent detected, but Google not connected. Requesting connection.")
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
                print("Google connected. Extracting calendar parameters with context...")
                system = (
                    "You are a calendar assistant. Use the dialogue context to resolve pronouns like 'that day', 'then', 'the previous time', etc. "
                    "When the user requests a calendar task, reply with a SINGLE JSON object only – no markdown, no back-ticks. "
                    "Use DOUBLE quotes for every key and string value. "
                    "Format: {\"action\": \"create_event\"|\"find_free_slots\"|\"list_events\", \"params\": { … }, \"message_for_user\": \"<short sentence>\"}"
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
                messages = messages_history + [{"role": "user", "content": text}]
                raw = self._get_claude_chat_response(messages, system_prompt=system, temperature=0)
                try:
                    extracted_data = json.loads(raw)
                except json.JSONDecodeError as e:
                    print(f"Failed to parse AI response as JSON: {raw}")
                    print(f"JSON decode error: {e}")
                    fallback = self.summarize_user_fields(text)
                    clarification = self.build_missing_fields_message(
                        fallback.get('present', {}),
                        fallback.get('missing', []),
                        ""
                    )
                    return { 'type': 'text', 'response': clarification }
                action = extracted_data.get('action')
                params = extracted_data.get('params', {})
                details = extracted_data.get('details', '')
                error = extracted_data.get('error')

                if error:
                     print(f"Parameter extraction failed: {error}")
                     return {
                         'type': 'text',
                         'response': error # Return the error from extraction
                     }

                # Tailored clarification using present/missing if available
                missing = params.get('missing') or params.get('needs_clarification')
                present = params.get('present', {})
                if missing:
                    clarification_text = self.build_missing_fields_message(present, missing, details)
                    print(f"Extraction needs clarification: {clarification_text}")
                    return { 'type': 'text', 'response': clarification_text }
                
                # If parameters are extracted and no clarification is needed,
                # return a structured response indicating the *intended* calendar action
                # The view (chat_process) will then perform the action.
                print(f"Parameters extracted successfully. Signalling view to perform action: {action} with params: {params}")
                return {
                    'type': 'calendar_action_request', # New type to signal the view
                    'content': {
                        'action': action,
                        'params': params,
                        'details': details # Keep the human-readable details
                    }
                }

        else: # intent == 'general_chat'
            print("General chat intent detected. Using Claude.")
            try:
                system = (
                    "You are a friendly and knowledgeable calendar assistant. "
                    "While your primary focus is on calendar management, you can also engage in general conversation. "
                    "Always maintain a helpful, professional tone and be ready to assist with calendar-related tasks. "
                    "For general chat, keep responses concise and relevant to the context of calendar and scheduling assistance."
                    "\n\n"
                    "When you output a list of capabilities, follow these rules exactly:\n"
                    "  1. Use an ordered list (1., 2., 3., …).\n"
                    "  2. On each numbered line, put the **Capability name:** and its short description on the **same line**.\n"
                    "     Then end that line with two spaces (to force a Markdown line-break).\n"
                    "  3. On the very next line (indented by four spaces), write Example: and its text.\n"
                    "  4. Only use bullets (• or ●) if you really need a second-level list under an example.\n"
                    "After all your explanation, examples and points, leave a line before "
                    "giving your closing statement or remark.\n\n"
                    "Respond in at most four short sentences."
                )
                
                messages_history = []
                if conversation:
                    # Fetch recent messages (e.g., last 10) for context, excluding empty ones
                    history_messages = conversation.messages.filter(text__isnull=False, text__gt='').order_by('-timestamp')[:10]
                    history_messages = list(history_messages)[::-1] # Reverse to get chronological order
                    messages_history = [
                        {"role": "user" if m.sender == "user" else "assistant", "content": m.text}
                        for m in history_messages if m.text and m.text.strip() # Double check text is not empty
                    ]
                    print(f"Including {len(messages_history)} history messages in general chat prompt.")

                # Add the current user message
                messages_history.append({"role": "user", "content": text})
                
                # Get response from Claude (system prompt passed separately)
                content = self._get_claude_chat_response(
                    messages_history,
                    system_prompt=system,
                    max_tokens=200,      # soft budget
                )

                if content is None:
                     return {
                        'type': 'text',
                        'response': "Sorry, I couldn't get a response from the AI for general chat."
                     }

                print("Generated general chat response.")
                return {
                    'type': 'text',
                    'response': content
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
        params = dict(
            model       = self.general_chat_model,
            messages    = messages,
            temperature = temperature,
            max_tokens  = min(max_tokens, 250),   # hard cap ≈ 1-2 short paragraphs
        )
        if system_prompt:            # only include when non-empty
            params["system"] = system_prompt

        resp = self.claude_client.messages.create(**params)
        return resp.content[0].text.strip()

    def summarize_user_fields(self, text: str) -> dict:
        """Lightweight fallback to identify present/missing fields when main JSON parse fails."""
        if not self.claude_client:
            return {"present": {}, "missing": ["date", "time", "summary"]}
        system = (
            "You extract calendar info for creating an event. "
            "Reply ONLY JSON with keys 'present' and 'missing'. "
            "present is an object possibly containing: summary, date, start, end, duration, attendees (array). "
            "missing is an array of required fields still needed for creating the event. "
            "Required: a date, time info (start+end or duration), and a summary/title. Attendees optional."
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