from django.contrib import admin
from django.urls import path

from ultictactoe_app.views import Game, Index, Board

urlpatterns = [
    path("board/", Board.as_view(), name="game"),
    path("lobby/<int:room_code>/", Game.as_view(), name="game_view"),
    path("", Index.as_view(), name="index"),
    
]