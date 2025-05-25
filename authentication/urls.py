from django.urls import path
from . import views

urlpatterns = [
    path("", views.loginUser, name="login"),
    path("signup/", views.signUpUser, name="signup"),
    path("home/", views.home, name="home"),
]