from django.urls import re_path

from .consumers import GameLobbyConsumer, LobbyAllocatorConsumer

websocket_urlpatterns = [
    re_path(r"ws/lobby/$", LobbyAllocatorConsumer.as_asgi()),  # <- NEU
    re_path(r"ws/game/(?P<room_name>[^/]+)/$", GameLobbyConsumer.as_asgi()),
]