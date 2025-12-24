from datetime import datetime, timezone, timedelta
from allauth.socialaccount.models import SocialAccount, SocialToken
from django.conf import settings
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import base64


class GoogleCalendarService: # helper class to encapsulate Calendar API calls per user
    def __init__(self, user): 
        try:
            token = SocialToken.objects.filter(account__user=user, account__provider='google').first()
            if token is None:
                raise Exception('No SocialToken found for Google. Please reconnect your Google account.')
            account = SocialAccount.objects.get(user=user, provider='google') # to fetch the linked social account so as to inspect profile data if needed
            social_app = token.app # social app instance in the db

            creds = Credentials(  # putting the tokens into an object i.e building credentials with tokens and client_id/secret from the db
                token=token.token,
                refresh_token=token.token_secret,
                token_uri='https://oauth2.googleapis.com/token',
                client_id=social_app.client_id,
                client_secret=social_app.secret,
            )

            self.creds = creds

            # Test the credentials and refresh if needed
            if not creds.valid:
                if creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                    token.token = creds.token
                    token.save()
                elif creds.expired and not creds.refresh_token:
                    # Cannot refresh without a refresh token; instruct caller to reconnect
                    raise Exception("Your Google connection expired and no refresh token is on file. Please reconnect your Google account.")
            self.service = build('calendar', 'v3', credentials=self.creds) # to build an authenticated version 3 Calendar API client 

        except Exception as e:
            raise Exception(f'Failed to initialize Google Calendar service: {e}')

    def list_events(self, calendar_id='primary', time_min=None, time_max=None, q=None, queries=None):
        """
        List events from the calendar.
        
        Args:
            calendar_id: ID of the calendar to list events from
            time_min: Start time (inclusive) in RFC3339 format
            time_max: End time (exclusive) in RFC3339 format
            q: Single free text search term (legacy)
            queries: List of free text search terms. If provided, searches for EACH term and merges results (OR logic).
        """
        # Allow searching past events by not defaulting to 'now'
        # If time_min is not provided, use a date far in the past
        if time_min is None:
            # Default to 1 year ago to allow retrieving past events
            one_year_ago = datetime.now(timezone.utc) - timedelta(days=365)
            time_min = one_year_ago.isoformat()
        
        # Ensure proper RFC3339 format with 'Z' suffix for UTC
        if time_min.endswith('+00:00'):
            time_min = time_min[:-6] + 'Z'

        # Helper to fetch events for a single query
        def fetch(query_term):
            return self.service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                q=query_term,
                singleEvents=True,
                orderBy='startTime'
            ).execute().get('items', [])

        all_events = {}
        
        # Handle multiple queries (OR logic)
        if queries and isinstance(queries, list):
            for query in queries:
                if query:
                    items = fetch(query)
                    for item in items:
                        all_events[item['id']] = item
        
        # Handle single query if provided (and no list queries, or in addition)
        if q and not queries:
             items = fetch(q)
             for item in items:
                all_events[item['id']] = item
        
        # If no queries at all, just list everything (default behavior)
        if not q and not queries:
            return fetch(None)

        # Convert dict back to list and sort by start time
        unique_events = list(all_events.values())
        unique_events.sort(key=lambda x: x.get('start', {}).get('dateTime') or x.get('start', {}).get('date') or '')
        
        return unique_events
    
    def list_calendars(self):
        return self.service.calendarList().list().execute().get("items", [])
    
    def create_event(self, calendar_id, event_body):
        return self.service.events().insert(calendarId=calendar_id, body=event_body).execute()
    
    def update_event(self, calendar_id, event_id, event_body):
        return self.service.events().update(calendarId=calendar_id, eventId=event_id, body=event_body,).execute()
    
    def delete_event(self, calendar_id, event_id):
        return self.service.events().delete(calendarId=calendar_id, eventId=event_id).execute()

    def get_event(self, calendar_id, event_id):
        return self.service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    
    def find_free_slots(
        self,
        start_date: str,
        end_date:   str,
        duration:   int = 60,            # minutes
        attendees:  list[str] | None = None,
        interval_minutes: int = 30
    ):
        """
        Returns Google Calendar free/busy data between start_date and end_date.
        • start_date / end_date   ISO date-strings or "YYYY-MM-DD".
        """
        if attendees is None:
            attendees = ["primary"]

        # Ensure RFC3339 format. If it's just a date (YYYY-MM-DD), append time.
        if len(start_date) == 10:
            start_date = start_date + "T00:00:00Z"
        if len(end_date) == 10:
            end_date = end_date + "T23:59:59Z"

        body = {
            "timeMin": start_date,
            "timeMax": end_date,
            "timeZone": getattr(settings, "TIME_ZONE", "UTC") or "UTC",
            "items": [{"id": cal_id} for cal_id in attendees],
        }
        resp = (
            self.service.freebusy()
            .query(body=body)
            .execute()
            .get("calendars", {})
        )

        # Flatten busy periods into a single sorted list
        busy = []
        for cal_data in resp.values():
            busy.extend(cal_data.get("busy", []))
        busy.sort(key=lambda b: b["start"])

        return busy         # list[{"start": "...", "end": "..."}]
    
    
    def send_email(self, to, subject, body):
        """Send email using Gmail API"""
        try:
            service = build('gmail', 'v1', credentials=self.creds)
            message = {
                'raw': base64.urlsafe_b64encode(f'From: Reminder Agent <{to}>\nTo: {to}\nSubject: {subject}\n\n{body}'.encode()).decode()
            }
            return service.users().messages().send(userId='me', body=message).execute()
        except Exception as e:
            raise Exception(f"Failed to send email: {e}")