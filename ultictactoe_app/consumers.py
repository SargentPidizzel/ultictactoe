# ultictactoe_app/consumers.py
import random
from channels.generic.websocket import AsyncWebsocketConsumer
import json
import re

# Raum -> {players: {channel_name: nickname}, host: channel_name|None}
rooms = {}


def generate_unique_code():
    # probiere, bis ein freier 4-stelliger Code gefunden ist
    # Achtung: bei >~9000 belegten Codes kann das dauern – dann besser Redis-Set benutzen
    while True:
        code = f"{random.randint(0, 9999):04d}"
        if code not in rooms:  # frei?
            return code

class LobbyAllocatorConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.accept()

    async def receive(self, text_data):
        try:
            data = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            return

        if data.get("action") == "request_code":
            code = generate_unique_code()
            await self.send(text_data=json.dumps({"event": "code_allocated", "code": code}))


def norm_room(name: str) -> str:
    """Gruppennamen-sicher machen (nur a-zA-Z0-9._- und max. ~90 Zeichen)."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", (name or "").strip())[:90]

class GameLobbyConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # room_name aus URL holen und Gruppen-Namen bauen
        raw_room = self.scope["url_route"]["kwargs"]["room_name"]
        self.room = norm_room(raw_room)
        self.group = f"game_{self.room}"

        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        # Spieler austragen; Raum löschen, wenn leer
        room = rooms.get(self.room)
        if room and self.channel_name in room["players"]:
            del room["players"][self.channel_name]
            await self._broadcast_players()
            if not room["players"]:
                rooms.pop(self.room, None)

        await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive(self, text_data):
        """
        Erwartet JSON:
        { "action": "create_or_join", "nickname": "Alice" }
        """
        try:
            data = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            return

        action = data.get("action")

        if action == "create_or_join":
            nickname = (data.get("nickname") or "Spieler").strip() or "Spieler"

            # Raum anlegen, falls nicht vorhanden
            if self.room not in rooms:
                rooms[self.room] = {"players": {}, "host": self.channel_name}

            # Spieler registrieren (überschreibt ggf. denselben Channel)
            rooms[self.room]["players"][self.channel_name] = nickname

            # Allen im Raum aktualisierte Liste schicken
            await self._broadcast_players()

            # Dem Sender sagen, ob er Host ist (optional, nützlich fürs UI)
            await self.send(text_data=json.dumps({
                "event": "joined",
                "room": self.room,
                "you_are_host": rooms[self.room]["host"] == self.channel_name
            }))

        # Weitere Actions (start/move/leave) kommen später

    async def _broadcast_players(self):
        players = list(rooms[self.room]["players"].values())
        await self.channel_layer.group_send(
            self.group,
            {"type": "players.update", "players": players, "count": len(players)}
        )

    async def players_update(self, event):
        await self.send(text_data=json.dumps({
            "event": "player_list",
            "players": event["players"],
            "count": event.get("count")
        }))
