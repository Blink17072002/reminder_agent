from datetime import datetime, timezone
from allauth.socialaccount.models import SocialAccount, SocialToken
from django.conf import settings
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


class GoogleCalendarService: # helper class to encapsulate Calendar API calls per user
    def __init__(self, user): 
        token = SocialToken.objects.get(account__user=user, account__provider='google') # to fetch the stored tokens (by allauth) for the user and provider
        account = SocialAccount.objects.get(user=user, provider='google') # to fetch the linked social account so as to inspect profile data if needed
        social_app = token.app # social app instance in the db

        creds = Credentials(  # putting the tokens into an object i.e building credentials with tokens and client_id/secret from the db
            token=token.token,
            refresh_token=token.token_secret,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=social_app.client_id,
            client_secret=social_app.secret,
        )
        self.service = build('calendar', 'v3', credentials=creds) # to build an authenticated version 3 Calendar API client 

    def list_events(self, calendar_id='primary', time_min=None, time_max=None): # to list the user's calendar events in a time period i.e from time_min to time_max
        now = time_min or datetime.now(timezone.utc).isoformat()  # if the user passes in a time_min, use that. Otherwise use current timestamp
        # Google still expects the trailing “Z” for RFC3339 UTC
        if now.endswith('+00:00'):
            now = now[:-6] + 'Z'

        return self.service.events().list(
            calendarId=calendar_id,
            timeMin=now,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute().get('items',[]) # send the request to Google's servers with a list of event objects or an empty list if none is found
    
    def list_calendars(self):
        return self.service.calendarList().list().execute().get("items", [])
    
    def create_event(self, calendar_id, event_body):
        return self.service.events().insert(calendarId=calendar_id, body=event_body).execute()
    
    def update_event(self, calendar_id, event_id, event_body):
        return self.service.events().update(calendarId=calendar_id, eventId=event_id, body=event_body,).execute()
    
    def delete_event(self, calendar_id, event_id):
        return self.service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    
    def find_free_slots(self, attendees, time_min, time_max, interval_minutes=30):
        body = { 
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": cal} for cal in attendees]
        }
        return self.service.freebusy().query(body=body).execute()  # using the freebusy api to get free slots in the body payload for each attendees' calendar
    
    