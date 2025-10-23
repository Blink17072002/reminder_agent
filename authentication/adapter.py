from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

class AutoSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_auto_signup_allowed(self, request, sociallogin):
        # Returning True here tells allauth to never show the signup form
        return True
    
    def get_connect_redirect_url(self, request, socialaccount):
        return request.GET.get("next", "/agent/assistant") # to send user back to chat page after granting all google scope permissions