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

    def _get_openai_response(self, messages, json_mode: bool = False, temperature: float = 0.7, max_tokens: int   = 500):
        """Helper to call OpenAI API."""
        if not self.openai_client:
            raise ValueError("OpenAI client not initialized (API key missing?)")

        try:
            response_format = {"type": "json_object"} if json_mode else {"type": "text"}
            resp = self.openai_client.chat.completions.create(
                model="gpt-4o-mini",  # Using GPT-4o-mini for cost-effective performance
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"Error calling OpenAI API: {e}")
            import traceback
            traceback.print_exc()
            raise e

    def _get_claude_response(self, messages):
        """Helper to call Claude API."""
        if not self.claude_client:
            raise ValueError("Claude client not initialized (API key missing?)")
        try:
            resp = self.claude_client.messages.create(
                model="claude-3-sonnet-20240229",
                messages=messages,
                temperature=0.7,
                max_tokens=150
            )
            return resp.content[0].text.strip()
        except Exception as e:
            print(f"Error calling Claude API: {e}")
            import traceback
            traceback.print_exc()
            raise e

    def get_google_account_email(self):
        try:
            social_account = SocialToken.objects.select_related('account').get(
                account__user=self.user,
                account__provider='google'
            ).account
            return social_account.extra_data.get('email')
        except (SocialToken.DoesNotExist, SocialAccount.DoesNotExist, AttributeError):
            return None

    def is_google_connected(self) -> bool:
        try:
            return SocialToken.objects.filter(account__user=self.user, account__provider='google').exists()
        except Exception:
            return False
        
    def determine_intent(self, text: str) -> str:
        """Uses Claude to classify the user's intent."""
        if not self.claude_client:
            return 'general_chat'

        intent_prompt = (
            "Classify the user's intent based on the following message. "
            "Choose one of the following categories: 'calendar', 'general_chat'. "
            "If the intent is unclear or doesn't fit 'calendar', default to 'general_chat'. "
            "Reply ONLY with the category name."
        )
        messages = [{"role": "user", "content": f"{intent_prompt}\n\nUser message: {text}"}]
        
        try:
            intent = self._get_claude_response(messages)
            intent = intent.strip().lower()
            return intent if intent in ['calendar', 'general_chat'] else 'general_chat'
        except Exception as e:
            print(f"Error determining intent: {e}, defaulting to general_chat.")
            return 'general_chat'

    def extract_calendar_parameters(self, text: str) -> dict:
        """Uses Claude to extract parameters for calendar actions."""
        if not self.claude_client:
            return {"action": "unknown", "params": {}, "error": "AI client not initialized."}

        parameter_prompt = (
            "Extract the calendar action and parameters from the following message. "
            "Identify the action (e.g., 'create_event', 'list_events', 'delete_event', 'find_free_slots', 'list_calendars'). "
            "Extract relevant parameters such as event 'summary', 'start' time/date, 'end' time/date, 'attendees' (list of emails). "
            "Return a JSON object like: {'action': 'action_name', 'params': {... parameters ...}, 'details': 'human-readable summary'}. "
            "If date/time/attendees are ambiguous or missing for a create action, include 'needs_clarification': ['field1', 'field2'] in params."
        )
        
        try:
            json_str = self._get_claude_response([
                {"role": "user", "content": f"{parameter_prompt}\n\nUser message: {text}"}
            ])
            return json.loads(json_str)
        except Exception as e:
            print(f"Error extracting calendar parameters: {e}")
            return {"action": "unknown", "params": {}, "error": f"Failed to extract details: {e}"}

    def process_calendar_intent(self, message_text: str):
        """Handles messages with calendar intent."""
        google_account_email = self.get_google_account_email()
        is_connected = self.is_google_connected()

        # Even if connected, check if API clients are initialized
        if not self.openai_client:
             return {
                'type': 'text',
                'response': "My AI capabilities are not configured. Please check the server settings."
            }

        if not is_connected:
            # Return response indicating connection is needed
            return {
                'type': 'needs_connection',
                'content': {
                    'email': google_account_email # Pass email if we could retrieve it before hitting token error
                    # You might pass the connect URL here from the backend view's context if needed,
                    # but for now JS reads it from a data attribute on the body.
                }
            }
        else:
            # User is connected - extract parameters using AI
            extracted_data = self.extract_calendar_parameters(message_text)
            action = extracted_data.get('action')
            params = extracted_data.get('params', {})
            details = extracted_data.get('details', '') # Human-readable summary from AI

            if extracted_data.get('error'):
                 return {
                     'type': 'text',
                     'response': extracted_data['error'] # Return error from extraction
                 }

            # Check if AI indicated needing clarification
            if params.get('needs_clarification'):
                 missing_fields = ", ".join(params['needs_clarification'])
                 clarification_text = f"I need more information to {details}. Could you please provide the {missing_fields}?"
                 return {
                     'type': 'text',
                     'response': clarification_text
                 }


            # --- START: Call GoogleCalendarService methods based on AI action ---
            try:
                svc = GoogleCalendarService(self.user) # Initialize service now that we know user is connected

                if action == "list_events":
                    print(f"Calling list_events with params: {params}")
                    # You need to handle date/time parsing from AI output here or refine extraction prompt
                    # Example: handle 'today', 'tomorrow', 'next week', etc.
                    # For now, pass params directly, assuming AI extracted valid API format (unlikely)
                    # A robust implementation would parse params like 'start_time_str' into RFC3339 format.
                    # Let's simulate success with hardcoded data for now.
                    # events = svc.list_events(**params) # Pass extracted params
                    simulated_events = [
                        {"summary": "Sample Event 1", "start": {"dateTime": "...", "timeZone": "..."}, "end": {"dateTime": "...", "timeZone": "..."}},
                        {"summary": "Sample Event 2", "start": {"dateTime": "...", "timeZone": "..."}, "end": {"dateTime": "...", "timeZone": "..."}},
                    ]
                    response_text = "Here are your events:\n" + json.dumps(simulated_events, indent=2) # Format nicely
                    return {
                         'type': 'text',
                         'response': response_text
                     }


                elif action == "create_event":
                    print(f"Calling create_event with params: {params}")
                    # Requires careful parsing of start, end, attendees from AI's params
                    # E.g., convert "tomorrow at 3pm" to ISO format "2024-05-22T15:00:00Z"
                    # This is where the complexity lies. You might need date/time parsing libraries.
                    # For now, simulate success.
                    simulated_event_title = params.get('summary', 'New Event')
                    # event_body = { ... construct from parsed params ... }
                    # created_event = svc.create_event('primary', event_body) # Example call


                    # Simulate success response using the structure from your HTML
                    return {
                        'type': 'event_success',
                        'content': {
                            'event_title': simulated_event_title, # Use AI-extracted title
                            'connected_email': google_account_email # Pass the connected email
                        }
                    }

                elif action == "list_calendars":
                     print("Calling list_calendars.")
                     calendars = svc.list_calendars()
                     response_text = "Here are your calendars:\n" + json.dumps(calendars, indent=2)
                     return {
                         'type': 'text',
                         'response': response_text
                     }

                # Add elif for delete_event, update_event, find_free_slots, etc.
                # These will also require parameter parsing and calling the corresponding svc methods.


                elif action == "unknown":
                    # AI couldn't determine a specific calendar action
                     return {
                        'type': 'text',
                         'response': f"I'm connected to your Google Calendar, but I'm not sure how to help with that request. Could you please rephrase?"
                     }

                else:
                    # AI returned a known action name but it's not implemented in this elif block yet
                     return {
                        'type': 'text',
                         'response': f"I understood you want to {details}, but that specific calendar action ({action}) is not yet implemented."
                     }


            except SocialToken.DoesNotExist:
                # This *shouldn't* be hit if is_connected check works, but good safety
                return {"error": "Please connect your Google account to enable calendar features."}
            except Exception as e:
                # Catch any errors during calendar service calls or parameter parsing/conversion
                print(f"Error during calendar service call or processing: {e}")
                import traceback
                traceback.print_exc()
                return {
                    'type': 'text',
                    'response': f"Sorry, I encountered an error while trying to process your calendar request: {e}"
                }


    def handle(self, text: str, conversation=None, is_title_generation=False) -> dict:
        """Processes the user's message and returns a structured response."""
        if is_title_generation:
            if not self.openai_client:
                return {'type': 'text', 'response': "AI client not initialized for title generation."}
            try:
                messages = [{"role": "user", "content": text}]
                title = self._get_openai_response(messages, temperature=0.1, max_tokens=20)
                return {'type': 'text', 'response': title}
            except Exception as e:
                print(f"Error generating title: {e}")
                return {'type': 'text', 'response': "Error generating title."}

        if not self.openai_client and not self.claude_client:
            return {
                'type': 'text',
                'response': "AI services are not configured. Please check the server settings."
            }

        intent = self.determine_intent(text)
        print(f"Processing message with primary intent: {intent}")
        
        if intent == 'calendar':
            if not self.is_google_connected():
                return {
                    'type': 'needs_connection',
                    'content': {'email': self.get_google_account_email()}
                }

            extracted_data = self.extract_calendar_parameters(text)
            action = extracted_data.get('action')
            params = extracted_data.get('params', {})
            details = extracted_data.get('details', '')

            if params.get('needs_clarification'):
                missing_fields = ", ".join(params['needs_clarification'])
                return {
                    'type': 'text',
                    'response': f"I need more information to {details}. Could you please provide the {missing_fields}?"
                }

            try:
                svc = GoogleCalendarService(self.user)
                
                if action == "create_event":
                    # Use Claude to format the event details
                    event_prompt = (
                        "Format the following event details into a proper calendar event structure. "
                        "Include summary, start, end, and attendees if provided. "
                        "Use ISO 8601 format for dates. "
                        f"Details: {json.dumps(params)}"
                    )
                    event_details = self._get_claude_response([
                        {"role": "user", "content": event_prompt}
                    ])
                    event_data = json.loads(event_details)
                    
                    created_event = svc.create_event('primary', event_data)
                    return {
                        'type': 'event_success',
                        'content': {
                            'event_title': event_data.get('summary', 'New Event'),
                            'connected_email': self.get_google_account_email()
                        }
                    }
                
                elif action == "list_events":
                    events = svc.list_events(**params)
                    return {
                        'type': 'text',
                        'response': f"Here are your events:\n{json.dumps(events, indent=2)}"
                    }
                
                elif action == "list_calendars":
                    calendars = svc.list_calendars()
                    return {
                        'type': 'text',
                        'response': f"Here are your calendars:\n{json.dumps(calendars, indent=2)}"
                    }
                
                else:
                    return {
                        'type': 'text',
                        'response': f"I understood you want to {details}, but that specific calendar action ({action}) is not yet implemented."
                    }

            except Exception as e:
                print(f"Error in calendar handling: {e}")
                return {
                    'type': 'text',
                    'response': f"Sorry, I encountered an error while processing your calendar request: {e}"
                }
        
        else:  # general_chat
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
                    "  3. On the very next line (indented by four spaces), write **Example:** and its text.\n"
                    "  4. Only use bullets (• or ●) if you really need a second-level list under an example.\n"
                    "After all your explanation, examples and points, leave a line before giving your closing statement or remark.\n"
                    "\n"
                )
                
                messages_history = []
                if conversation:
                    history_messages = conversation.messages.order_by('-timestamp')[:10]
                    history_messages = list(history_messages)[::-1]
                    messages_history = [
                        {"role": "user" if m.sender == "user" else "assistant", "content": m.text}
                        for m in history_messages if m.text and m.text.strip()
                    ]

                messages_history.append({"role": "user", "content": text})
                
                content = self._get_openai_response([
                    {"role": "system", "content": system},
                    *messages_history
                ])
                
                return {
                    'type': 'text',
                    'response': content
                }
            except Exception as e:
                print(f"Error in general chat handling: {e}")
                return {
                    'type': 'text',
                    'response': "Sorry, I'm having trouble processing that request right now."
                }