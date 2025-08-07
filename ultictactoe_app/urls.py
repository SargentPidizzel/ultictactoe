from django.contrib import admin
from django.urls import path

from ultictactoe_app.views import Game

urlpatterns = [
    path("test/", Game.as_view(), name="game"),
]