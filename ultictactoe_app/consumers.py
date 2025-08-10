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

    # async def disconnect(self, code):
    #     # Spieler austragen; Raum löschen, wenn leer
    #     room = rooms.get(self.room)
    #     if room and self.channel_name in room["players"]:
    #         del room["players"][self.channel_name]
    #         await self._broadcast_players()
    #         if not room["players"]:
    #             rooms.pop(self.room, None)

    #     await self.channel_layer.group_discard(self.group, self.channel_name)
    async def disconnect(self, code):
        room = rooms.get(self.room)
        if room and self.channel_name in room["players"]:
            was_host = (room["host"] == self.channel_name)
            del room["players"][self.channel_name]

            if not room["players"]:
                rooms.pop(self.room, None)
            else:
                if was_host:
                    # Nächster verbliebener Channel wird Host
                    room["host"] = next(iter(room["players"].keys()))

            # Nach Änderungen broadcasten
            await self._broadcast_players()

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

        MAX_PLAYERS = 2  # dein Limit
        print("Action: ", action)

        if action == "create_or_join":
            nickname = (data.get("nickname") or "Spieler").strip() or "Spieler"

            # Raum anlegen, falls nicht vorhanden
            if self.room not in rooms:
                rooms[self.room] = {"players": {}, "host": None}

            room = rooms[self.room]

            # --- Limit prüfen ---
            if len(room["players"]) >= MAX_PLAYERS:
                await self.send(text_data=json.dumps({
                    "event": "error",
                    "message": f"Lobby ist voll (max. {MAX_PLAYERS} Spieler)."
                }))
                return
            # --------------------

            # Spieler hinzufügen
            room["players"][self.channel_name] = nickname

            # Host setzen, falls noch keiner vorhanden
            if room["host"] is None:
                room["host"] = self.channel_name

            # Liste an alle senden
            await self._broadcast_players()

            # Dem Sender seine Join-Bestätigung schicken
            await self.send(text_data=json.dumps({
                "event": "joined",
                "room": self.room,
                "you_are_host": (room["host"] == self.channel_name),
                "your_id": self.channel_name,
            }))
        elif action == "start_game":
            room = rooms.get(self.room)
            if not room:
                return
            if room["host"] != self.channel_name:
                await self.send(text_data=json.dumps({"event": "error", "message": "Nur der Host darf starten."}))
                return
            if len(room["players"]) < 2:
                await self.send(text_data=json.dumps({"event": "error", "message": "Mindestens 2 Spieler nötig."}))
                return

            room["phase"] = "starting"  # <— wichtig

            game_url = f"/play/lobby/{self.room}/"  # oder dein Game-Pfad
            await self.channel_layer.group_send(
                self.group, {"type": "game.start", "url": game_url}
            )
        elif action == "game_move":
            print("Spielzug")
            big = int(data.get("big"))
            small = int(data.get("small"))

            await self.channel_layer.group_send(
                self.group, {"type": "game.move", "big": big, "small":small}
            )

        # Weitere Actions (start/move/leave) kommen später

    async def _broadcast_players(self):
        room = rooms.get(self.room)
        if not room:
            return
        players = [
            {"id": ch, "name": nick, "is_host": (ch == room["host"])}
            for ch, nick in room["players"].items()
        ]
        await self.channel_layer.group_send(
            self.group,
            {"type": "players.update", "players": players, "count": len(players)}
        )

    async def players_update(self, event):
        await self.send(text_data=json.dumps({
            "event": "player_list",
            "players": event["players"],  # [{id, name, is_host}, ...]
            "count": event.get("count"),
        }))

    async def game_start(self, event):
        await self.send(text_data=json.dumps({
            "event": "start",
            "url": event["url"]
        }))

    async def game_move(self, event):
        print("EVENT: ", event)
        await self.send(text_data=json.dumps({
            "event": "move",
            "big": event["big"],
            "small": event["small"],
        }))

    async def disconnect(self, code):
        # Erst aus der Gruppe austragen (egal was passiert)
        await self.channel_layer.group_discard(self.group, self.channel_name)

        room = rooms.get(self.room)
        if not room:
            return

        # Spieler austragen (idempotent)
        room["players"].pop(self.channel_name, None)

        # Wenn gerade ein Start läuft, nicht mehr broadcasten
        if room.get("phase") == "starting":
            if not room["players"]:
                rooms.pop(self.room, None)
            return

        # Normalfall Lobby:
        if room["players"]:
            await self._broadcast_players()
        else:
            rooms.pop(self.room, None)
