from django.contrib import admin
from django.urls import path

from user_app.views import Login, Register

urlpatterns = [
    path("login/", Login.as_view(), name="login"),
    path("register/", Register.as_view(), name="register"),
    
]