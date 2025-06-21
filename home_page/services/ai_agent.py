import json
from openai import OpenAI
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
        
        # Initialize API clients
        openai_api_key = os.getenv('OPENAI_API_KEY')
        claude_api_key = os.getenv('CLAUDE_API_KEY')
        
        self.openai_client = OpenAI(api_key=openai_api_key) if openai_api_key else None
        self.claude_client = Anthropic(api_key=claude_api_key) if claude_api_key else None

        # Define models here or load from settings if preferred
        self.general_chat_model = "gpt-4o-mini"
        self.calendar_intent_model = "claude-3-5-sonnet-20240620" # Or another suitable Claude model
        self.calendar_param_model = "claude-3-5-sonnet-20240620" # Or another suitable Claude model
        self.title_generation_model = "gpt-4o-mini"

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
                model=model_to_use,
                messages=messages,
                temperature=temp_to_use,
                max_tokens=tokens_to_use
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
        """Checks if the user has a connected Google account."""
        try:
            # Check if a SocialToken for Google exists for the user
            return SocialToken.objects.filter(account__user=self.user, account__provider='google').exists()
        except Exception:
            # Handle potential database errors gracefully
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
            "Identify the action (e.g., 'create_event', 'list_events', 'delete_event', 'find_free_slots', 'list_calendars'). "
            "Extract relevant parameters such as event 'summary', 'start' time/date, 'end' time/date, 'attendees' (list of emails). "
            "If date/time/attendees are ambiguous or missing for a 'create_event', include 'needs_clarification': ['field1', 'field2'] in the params. "
            "Return a JSON object ONLY. Format: {'action': 'action_name', 'params': {... parameters ...}, 'details': 'human-readable summary of intended action'}. "
            "Example for 'create_event': {'action': 'create_event', 'params': {'summary': 'Meeting with John', 'start': 'tomorrow 2pm', 'end': 'tomorrow 3pm', 'attendees': ['john@example.com']}, 'details': 'create a meeting with John tomorrow from 2 to 3 pm'}."
             "Example for 'list_events': {'action': 'list_events', 'params': {'time_min': 'today 9am'}, 'details': 'list my events from 9 am today'}."
             "Example for needing clarification: {'action': 'create_event', 'params': {'summary': 'Lunch', 'needs_clarification': ['time', 'date']}, 'details': 'schedule a lunch meeting'}."
             "If the action or parameters are unclear or not calendar-related, use action: 'unknown'."
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
            if not self.openai_client:
                return {'type': 'text', 'response': "AI client not initialized for title generation."}
            try:
                messages = [{"role": "user", "content": text}] # Text is the title prompt here
                # Use specific parameters for title generation handled in _get_openai_response
                title = self._get_openai_response(messages)
                return {'type': 'text', 'response': title}
            except Exception as e:
                print(f"Error generating title: {e}")
                # Return a fallback or error message for title generation
                return {'type': 'text', 'response': "Error generating title."}

        # --- Main message handling logic ---
        if not self.openai_client and not self.claude_client:
            print("Neither OpenAI nor Claude client is initialized.")
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
                        'email': self.get_google_account_email() # Pass email if available
                    }
                }
            else:
                # If connected, proceed to extract calendar parameters
                print("Google connected. Extracting calendar parameters...")
                extracted_data = self.extract_calendar_parameters(text)
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

                # Check if AI indicated needing clarification on parameters
                if params.get('needs_clarification'):
                     missing_fields = ", ".join(params['needs_clarification'])
                     clarification_text = f"I need more information to {details}. Could you please provide the {missing_fields}?"
                     print(f"Extraction needs clarification: {clarification_text}")
                     return {
                         'type': 'text',
                         'response': clarification_text
                     }
                
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
            print("General chat intent detected. Using OpenAI.")
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
                    "After all your explanation, examples and points, leave a line before giving your closing statement or remark.\n"
                    "\n"
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
                
                # Get response from OpenAI
                content = self._get_openai_response([
                    {"role": "system", "content": system},
                    *messages_history
                ])

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