# ultictactoe_app/consumers.py
import random
from channels.generic.websocket import AsyncWebsocketConsumer
import json
import re

# Raum -> {players: {channel_name: nickname}, host: channel_name|None}
rooms = {}
# game = []


# Gewinn-Linien für 3x3
LINES = [
    (0,1,2),(3,4,5),(6,7,8),  # Reihen
    (0,3,6),(1,4,7),(2,5,8),  # Spalten
    (0,4,8),(2,4,6),          # Diagonalen
]

def small_board_result(small_board: dict) -> str | None:
    """
    Ermittelt das Ergebnis eines kleinen Boards.
    Rückgabe:
        'X' oder 'O' -> Gewinner
        'T'          -> Unentschieden (Tie)
        None         -> noch offen
    small_board ist dict: {pos(0..8): 'X'|'O'}
    """
    for a,b,c in LINES:
        va = small_board.get(a)
        if va and va == small_board.get(b) == small_board.get(c):
            return va  # 'X' oder 'O'
    if len(small_board) == 9:
        return 'T'  # voll, kein Dreier
    return None

def big_board_result(big_results: dict[int, str | None]) -> str | None:
    """
    Ermittelt das Gesamtergebnis des großen Boards auf Basis der
    Ergebnisse der 9 kleinen Boards (X/O/T/None).
    Rückgabe:
        'X' oder 'O' -> Gesamtsieger
        'T'          -> Gesamt-Unentschieden
        None         -> noch offen
    """
    # Für Sieg zählen nur echte Siege ('X'/'O') in den Linien
    for a,b,c in LINES:
        va = big_results.get(a)
        if va in ('X','O') and va == big_results.get(b) == big_results.get(c):
            return va
    # Gesamt-Remis, wenn alle 9 Felder abgeschlossen (X/O/T) und kein Sieger
    if all(big_results.get(i) in ('X','O','T') for i in range(9)):
        return 'T'
    return None



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

            # Raum anlegen, falls neu – mit eigenem Board/Phase
            if self.room not in rooms:
                rooms[self.room] = {
                    "players": {},
                    "host": None,
                    "board": {i: {} for i in range(9)},
                    "phase": "lobby",
                    "currentPlayer": "X",   # <-- hier!
                    "big_field_to_click": "",
                    "finished_fields": [],
                }

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
                "phase": room.get("phase", "lobby"),
                # "board": sorted(list(room.get("board", set()))),  # <- wichtig
                "board": [(int(b), int(s), v)
                    for b, cells in room["board"].items()
                    for s, v in cells.items()],
                            "currentPlayer" : "X",
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

            # room["phase"] = "starting"  # <— wichtig
            # Phase & Board zurücksetzen
            # current_player = rooms[self.room].setdefault("currentPlayer", "X")
            room["phase"] = "playing"
            # room["board"] = set()
            room["board"] = {i: {} for i in range(9)}   # statt: set()
            room["currentPlayer"] = "X"
            print("Start Spiel")
            # room[""]

            game_url = f"/play/lobby/{self.room}/"  # oder dein Game-Pfad
            await self.channel_layer.group_send(
                self.group, {"type": "game.start", "url": game_url, "board": []}
            )
        # elif action == "game_move":
        #     print("Spielzug")
        #     big = int(data.get("big"))
        #     small = int(data.get("small"))
            
        #     room = rooms[self.room]
            
        #     game = room["board"]
            
        #     id = f"{big}_{small}"
            
        #     if not id in game:
        #         print(id)
        #         game.add(f"{big}_{small}")

        #         await self.channel_layer.group_send(
        #             self.group, {"type": "game.move", "big": big, "small":small, "game":game}
        #         )
        #     else:
        #         print("ID schon drin")
        elif action == "game_move":
            room = rooms.get(self.room)
            if not room:
                return

            # if room.get("phase") != "playing":
            #     await self.send(text_data=json.dumps({"event":"error","message":"Spiel läuft nicht."}))
            #     return

            big_field_to_click = room.get("big_field_to_click", "")
            print("Jetzt anklicken: ", big_field_to_click)

            try:
                big = int(data.get("big"))
                small = int(data.get("small"))
            except (TypeError, ValueError):
                await self.send(text_data=json.dumps({"event":"error","message":"Ungültiger Zug."}))
                return

            if not (0 <= big <= 8 and 0 <= small <= 8):
                await self.send(text_data=json.dumps({"event":"error","message":"Außerhalb des Boards."}))
                return

            if big_field_to_click != "" and big != big_field_to_click:
                print("Klick ins richtige Feld!")
                await self.send(text_data=json.dumps({"event":"error","message":"Bitte im vorgegebenen Großfeld spielen."}))
                return

            board = room.setdefault("board", {i: {} for i in range(9)})                # {int: {int: 'X'|'O'}}
            big_results = room.setdefault("big_results", {i: None for i in range(9)})  # {int: 'X'|'O'|'T'|None}
            current_player = room.setdefault("currentPlayer", "X")

            if big_results.get(big) in ('X', 'O', 'T'):
                await self.send(text_data=json.dumps({"event":"error","message":"Dieses Großfeld ist bereits abgeschlossen."}))
                return

            if small in board[big]:
                await self.send(text_data=json.dumps({"event":"error","message":"Feld bereits belegt."}))
                return

            # Zug eintragen
            board[big][small] = current_player

            # Kleines Board auswerten
            res_small = small_board_result(board[big])   # 'X'|'O'|'T'|None
            if res_small and big_results[big] is None:
                big_results[big] = res_small

            # Gesamtergebnis prüfen
            res_big = big_board_result(big_results)      # 'X'|'O'|'T'|None

            # Nächstes Pflicht-Großfeld
            next_big = small
            if big_results.get(next_big) in ('X', 'O', 'T') or len(board[next_big]) == 9:
                next_big = ""  # freie Wahl
            room["big_field_to_click"] = next_big

            print("Im nächsten Zug anklicken: ", room["big_field_to_click"])
            print("Board: ", room["board"])

            # Spieler wechseln, falls nicht beendet
            if res_big is None:
                room["currentPlayer"] = "O" if current_player == "X" else "X"

            # ❗ Serializer-Fix: Keys als Strings serialisieren
            big_results_json = {str(k): v for k, v in big_results.items()}

            await self.channel_layer.group_send(
                self.group,
                {
                    "type": "game.move",
                    "big": big,
                    "small": small,
                    "symbol": current_player,
                    "currentPlayer": room["currentPlayer"] if res_big is None else None,
                    "bigFieldToClick": room["big_field_to_click"],
                    "smallBoardResult": res_small,
                    "bigResults": big_results_json,  # ← string keys!
                },
            )

            if res_big is not None:
                room["phase"] = "ended"
                room["big_field_to_click"] = ""

                # (Optional) auch hier stringifizierte Keys senden
                big_results_json = {str(k): v for k, v in big_results.items()}
                await self.channel_layer.group_send(
                    self.group,
                    {
                        "type": "game.over",
                        "winner": res_big if res_big in ("X", "O") else None,
                        "draw": True if res_big == "T" else False,
                        "bigResults": big_results_json,
                    },
                )


        # Weitere Actions (start/move/leave) kommen später
        
    # def checkWin():
    #     return

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
        await self.send(text_data=json.dumps({
            "event": "move",
            "big": event["big"],
            "small": event["small"],
            "symbol": event["symbol"],
            "currentPlayer": event["currentPlayer"],
        }))
    
    # await self.send(text_data=json.dumps({
    #     "event": "joined",
    #     "room": self.room,
    #     "you_are_host": (room["host"] == self.channel_name),
    #     "your_id": self.channel_name,
    #     "phase": room.get("phase", "lobby"),
    #     "board": sorted(list(room.get("board", set()))),  # <- wichtig
    # }))

    
