from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User

user = get_user_model()

class SignUpForm(UserCreationForm):
    full_name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            'placeholder': 'Full name',
            'class': 'auth-input',
            'id': 'fullNameField'
        })
    )
    email = forms.EmailField(
        max_length=254, required=True,
        widget=forms.EmailInput(attrs={
            'placeholder': 'Email address',
            'class': 'auth-input'
        })
    )
    password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Password',
            'class': 'auth-input',
            'id': 'passwordField'
        })
    )
    password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'placeholder': 'Confirm password',
            'class': 'auth-input'
        })
    )

    class Meta:
        model = user
        fields = ('full_name', 'email', 'password1', 'password2')

    def clean_email(self):
        email = self.cleaned_data.get('email')
        # Check if a user already exists with this email as username
        if User.objects.filter(username__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True): # to get the first name and last name from username
        user = super().save(commit=False)
        full_name = self.cleaned_data['full_name'].strip()
        parts = full_name.split(' ', 1)
        user.first_name = parts[0]
        user.last_name = parts[1] if len(parts) > 1 else ''
        user.username = self.cleaned_data['email']  # django doesn't recognize email when logging in, so we pass the value of the email to the username
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user