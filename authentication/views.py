from django.shortcuts import render, HttpResponse, redirect
from .forms import SignUpForm
from django.contrib.auth import login, logout, authenticate
from django.contrib import messages
from django.contrib.messages import get_messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from home_page.services.ai_agent import AIAgent


# Create your views here.
@login_required(login_url="login")  # protecting the home page from unregistered users
def home(request):
    return redirect("home_page:assistant")


def signUpUser(request):
    if request.method == "POST":
        form = SignUpForm(request.POST)
        form.fields['password1'].widget.attrs.update({'id': 'id_password1'})
        form.fields['password2'].widget.attrs.update({'id': 'id_password2'})

        if form.is_valid():
            user = form.save()
            # messages.success(request, 'Account created successfully! You can now log in.')
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            return redirect("home")
        else:
            # Turn all field errors into one clean string
            error_list = []
            for field, errs in form.errors.items():
                for e in errs:
                    error_list.append(e)
            messages.error(request, " ".join(error_list))
    else:
        form = SignUpForm()
        form.fields['password1'].widget.attrs.update({'id': 'id_password1'})  # eye toggler
        form.fields['password2'].widget.attrs.update({'id': 'id_password2'})
           
    context = { 'form': form } 
    return render(request, "authentication/signup.html", context)


def loginUser(request):
    # only attempt auth and add messages when the form is submitted
    if request.method == "POST":
        email = request.POST.get("email")
        password = request.POST.get("password")
        user = authenticate(request, username=email, password=password)

        if user is not None:
            login(request, user)
            # messages.success(request, f'Welcome back, {user.first_name}!')
            return redirect("home")
        else:
            messages.error(request, "Email or Password incorrect")
    else:
        # If GET, clear any existing messages so success-only appears immediately after login
        storage = get_messages(request)
        for _ in storage:
            pass

    # for GET (or after a failed POST), just render the page without adding extra messages
    return render(request, "authentication/login.html", {})