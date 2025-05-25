from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

class AutoSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_auto_signup_allowed(self, request, sociallogin):
        # Returning True here tells allauth to never show the signup form
        return True
