# Authentication App

This Django app provides email/password and Google‐OAuth2 authentication via **django-allauth**. Users can sign up or log in with email/password, or click “Log in/Sign up with Google” to authenticate via Google.

## Table of Contents

1. [Requirements](#requirements)
2. [Installation](#installation)
3. [Settings Changes](#settings-changes)
4. [Custom Social Adapter](#custom-social-adapter)
5. [URL Configuration](#url-configuration)
6. [Views](#views)
7. [Templates](#templates)
8. [Static Assets & Styling](#static-assets--styling)
9. [Migrations](#migrations)

---

## Requirements

- Python 3.x  
- Django 5.2  
- django-allauth  
- requests  

Install dependencies:

```bash
pip install django django-allauth requests
```

---

## Installation

1. Add the `authentication` app folder alongside your main `project/` directory.  
2. Add `authentication` to your `INSTALLED_APPS` (see next section).  
3. Run `python manage.py migrate` to create the allauth tables.

---

## Settings Changes

Edit `project/settings.py` and update/insert the following sections:

```python:project/settings.py
# INSTALLED_APPS
INSTALLED_APPS = [
    # Django built-ins...
    'django.contrib.sites',              # required by allauth
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    # your apps
    'authentication',
]

# Site ID (matches the Django Sites framework entry)
SITE_ID = 2

# URL to redirect to after login/signup
LOGIN_REDIRECT_URL = '/home/'

# Authentication backends
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# Middleware – make sure this line appears after AuthenticationMiddleware
MIDDLEWARE = [
    # ...
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'allauth.account.middleware.AccountMiddleware',
    # ...
]

# Django-allauth settings
ACCOUNT_LOGIN_METHODS    = {'email'}
ACCOUNT_SIGNUP_FIELDS    = ['email*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = 'optional'

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {
            'access_type': 'online',
            'prompt': 'select_account',     # always show Google’s account chooser
        },
    }
}

SOCIALACCOUNT_LOGIN_ON_GET  = True   # immediately log in if social account exists
SOCIALACCOUNT_AUTO_SIGNUP   = True   # auto-create user w/o extra form
SOCIALACCOUNT_ADAPTER       = 'authentication.adapter.AutoSocialAccountAdapter'
```

---

## Custom Social Adapter

We override the default allauth adapter to always auto‐approve social signups:

```python:authentication/adapter.py
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

class AutoSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_auto_signup_allowed(self, request, sociallogin):
        # Skip the intermediate signup form
        return True
```

---

## URL Configuration

In `project/urls.py`:

```python:project/urls.py
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('authentication.urls')),   # your login/signup/home views
    path('accounts/', include('allauth.urls')), # allauth endpoints
]
```

In `authentication/urls.py`:

```python:authentication/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('',       views.loginUser,  name='login'),
    path('signup/',views.signUpUser,name='signup'),
    path('home/',  views.home,       name='home'),
]
```

---

## Views

- `loginUser` — handles email/password login  
- `signUpUser` — handles email/password signup  
- `home` — a protected view that requires login  

See `authentication/views.py` for full details.

---

## Templates

### Login (`authentication/templates/authentication/login.html`)

- Standard email/password form  
- Google OAuth button wrapped in a POST form:

  ```django
  <form action="{% provider_login_url 'google' process='signup' next='/home/' %}"
        method="post" class="oauth-form">
    {% csrf_token %}
    <button type="submit" class="btn btn-google">
      <img src="{% static 'authentication/images/google.png' %}" alt="Google">
      Log in with Google
    </button>
  </form>
  ```

### Signup (`authentication/templates/authentication/signup.html`)

Same pattern for “Sign up with Google”:

```django
<form action="{% provider_login_url 'google' process='signup' next='/home/' %}"
      method="post" class="oauth-form">
  {% csrf_token %}
  <button type="submit" class="btn btn-google">
    <img src="{% static 'authentication/images/google.png' %}" alt="Google">
    Sign up with Google
  </button>
</form>
```

---

## Static Assets & Styling

All button styles, form layouts, and message styles live in:
