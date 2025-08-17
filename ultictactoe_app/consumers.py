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

def _winner_in(arr):
    """
    arr: Liste der Länge 9 mit Werten "X", "O" oder None.
    Gibt (winner, line) zurück, z. B. ("X", (0,1,2)) oder (None, None).
    """
    for a, b, c in LINES:
        v = arr[a]
        if v and v == arr[b] == arr[c]:
            return v, (a, b, c)
    return None, None

def small_result(cells: dict):
    """
    cells: Dict eines kleinen Feldes, z. B. {0:"X", 4:"X", 8:"X"}.
    Rückgabe:
      ("win", "X", (0,4,8))  -> Spieler X hat gewonnen (mit Linie)
      ("draw", None, None)   -> alle 9 Felder belegt, kein Gewinner
      ("ongoing", None, None)-> noch nicht entschieden
    """
    arr = [cells.get(i) for i in range(9)]  # index 0..8
    w, line = _winner_in(arr)
    if w:
        return "win", w, line
    if all(v in ("X", "O") for v in arr):
        return "draw", None, None
    return "ongoing", None, None

def _is_big_finished(rm, big_idx): #rm ist room
    # direkt aus finished_fields lesen (schnell)
    if big_idx in rm.get("finished_fields", {}):
        return True
    # falls noch nicht eingetragen: aktuellen Zustand berechnen
    st, _, _ = small_result(rm.get("board", {}).get(big_idx, {}))
    return st in ("win", "draw")


def big_board_winner(finished_fields: dict):
    """
    Prüft, ob X/O das große 3x3-Brett gewonnen hat.
    finished_fields: {big_index: "X"/"O"/"D"}
    Rückgabe:
      ("X", (0,1,2))  -> X hat gewonnen (Gewinnlinie)
      ("O", (2,4,6))  -> O hat gewonnen
      (None, None)    -> noch kein Gesamtsieg
    """
    arr = [
        finished_fields.get(i) if finished_fields.get(i) in ("X", "O") else None
        for i in range(9)
    ]
    # nutzt deine bestehende LINES-Logik
    for a, b, c in LINES:
        v = arr[a]
        if v and v == arr[b] == arr[c]:
            return v, (a, b, c)
    return None, None

def is_global_draw(finished_fields: dict) -> bool:
    """
    Globales Unentschieden: alle 9 Großfelder sind entschieden (X/O/D),
    aber kein Spieler hat das große Brett gewonnen.
    """
    if len(finished_fields) < 9:
        return False
    w, _ = big_board_winner(finished_fields)
    return w is None




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
        # immer erst aus der Gruppe raus
        await self.channel_layer.group_discard(self.group, self.channel_name)

        room = rooms.get(self.room)
        if not room:
            return

        phase = room.get("phase", "lobby")

        # >>> neu: während Redirect/Spiel NICHT aufräumen
        if phase in ("starting", "playing"):
            return
        # <<<

        # Lobby: Spieler austragen und ggf. Raum löschen
        room["players"].pop(self.channel_name, None)
        room.get("symbols", {}).pop(self.channel_name, None)

        if room["players"]:
            await self._broadcast_players()
        else:
            rooms.pop(self.room, None)

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

            if self.room not in rooms:
                rooms[self.room] = {
                    "players": {}, "host": None,
                    "board": {i: {} for i in range(9)},
                    "phase": "lobby", "currentPlayer": "X",
                    "big_field_to_click": "", "finished_fields": {},
                    "symbols": {},
                }

            room = rooms[self.room]

            # >>> neu: Rejoin-Pfad, wenn Spiel im Gange / Redirect
            if room.get("phase") in ("starting", "playing") and "symbol_by_name" in room:
                desired = room["symbol_by_name"].get(nickname)
                if desired in ("X", "O"):
                    # alten Channel für dieses Symbol entfernen
                    for ch, s in list(room["symbols"].items()):
                        if s == desired:
                            room["symbols"].pop(ch, None)
                            room["players"].pop(ch, None)
                    # aktuellen Channel setzen
                    room["players"][self.channel_name] = nickname
                    room["symbols"][self.channel_name] = desired
                    # Host beibehalten, falls noch keiner
                    if room["host"] is None:
                        room["host"] = self.channel_name

                    # vollständige Bestätigung zurück
                    names_by_symbol = {
                        s: room["players"].get(ch, "")
                        for ch, s in room["symbols"].items() if s in ("X","O")
                    }
                    await self._broadcast_players()
                    await self.send(text_data=json.dumps({
                        "event": "joined",
                        "room": self.room,
                        "you_are_host": (room["host"] == self.channel_name),
                        "your_id": self.channel_name,
                        "phase": room.get("phase"),
                        "your_symbol": room["symbols"][self.channel_name],
                        "board": [(int(b), int(s), v)
                                for b, cells in room["board"].items()
                                for s, v in cells.items()],
                        "currentPlayer": room.get("currentPlayer", "X"),
                        "players": room["players"],
                        "symbols": room["symbols"],
                        "names_by_symbol": names_by_symbol,
                    }))
                    return
            # <<< Rejoin-Pfad Ende

            # --- normaler Lobby-Join wie gehabt (MAX_PLAYERS etc.) ---
            # MAX_PLAYERS = 2
            if len(room["players"]) >= MAX_PLAYERS:
                await self.send(text_data=json.dumps({
                    "event":"error","message":f"Lobby ist voll (max. {MAX_PLAYERS})."
                }))
                return

            room["players"][self.channel_name] = nickname
            if room["host"] is None:
                room["host"] = self.channel_name

            sym = room["symbols"].get(self.channel_name)
            if sym is None:
                taken = set(room["symbols"].values())
                sym = "X" if "X" not in taken else ("O" if "O" not in taken else None)
                if not sym:
                    await self.send(text_data=json.dumps({"event":"error","message":"Es sind bereits 2 Spieler verbunden."}))
                    return
                room["symbols"][self.channel_name] = sym

            await self._broadcast_players()

            names_by_symbol = {
                s: room["players"].get(ch, "")
                for ch, s in room["symbols"].items() if s in ("X","O")
            }
            await self.send(text_data=json.dumps({
                "event": "joined",
                "room": self.room,
                "you_are_host": (room["host"] == self.channel_name),
                "your_id": self.channel_name,
                "phase": room.get("phase", "lobby"),
                "your_symbol": room["symbols"][self.channel_name],
                "board": [(int(b), int(s), v)
                        for b, cells in room["board"].items()
                        for s, v in cells.items()],
                "currentPlayer": room.get("currentPlayer","X"),
                "players": room["players"],
                "symbols": room["symbols"],
                "names_by_symbol": names_by_symbol,
            }))

        elif action == "start_game":
            room = rooms.get(self.room)
            if not room: return
            if room["host"] != self.channel_name:
                await self.send(text_data=json.dumps({"event":"error","message":"Nur der Host darf starten."}))
                return
            if len(room["players"]) < 2:
                await self.send(text_data=json.dumps({"event":"error","message":"Mindestens 2 Spieler nötig."}))
                return

            # >>> neu:
            room["phase"] = "starting"
            room["symbol_by_name"] = {}
            for ch, sym in room.get("symbols", {}).items():
                nick = room["players"].get(ch)
                if nick and sym in ("X","O"):
                    room["symbol_by_name"][nick] = sym
            # <<<

            # Board & Status für Spiel vorbereiten
            room["board"] = {i: {} for i in range(9)}
            room["currentPlayer"] = "X"

            game_url = f"/play/lobby/{self.room}/"
            await self.channel_layer.group_send(
                self.group,
                {"type": "game.start", "url": game_url, "board": []}
            )
      
        elif action == "game_move":
            room = rooms.get(self.room)
            if not room:
                return
            # if room.get("phase") != "playing":
            #     await self.send(text_data=json.dumps({"event":"error","message":"Spiel läuft nicht."}))
            #     return

            # Field to click in rooms speichern und dann prüfen, ob Big gleich ist. Wenn ja go, sonst nichts
            
            my_symbol = room.get("symbols", {}).get(self.channel_name)
            if my_symbol is None:
                await self.send(text_data=json.dumps({"event": "error", "message": "Du bist nicht in diesem Spiel."}))
                return

            if my_symbol != room.get("currentPlayer", "X"):
                await self.send(text_data=json.dumps({"event": "error", "message": "Du bist nicht dran."}))
                return

            
            big_field_to_click = room["big_field_to_click"]
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
            
            if big_field_to_click == "" or big == big_field_to_click: #Prüfen ob das angeklickte Feld auch das richtige war

                board = room.setdefault("board", {i: {} for i in range(9)})   # dict→dict
                current_player = room.setdefault("currentPlayer", "X")

                finished_fields = room["finished_fields"]
                print("Finished_fields: ", finished_fields)

                if big in finished_fields:
                    print("FELD IST BELEGT")
                    await self.send(text_data=json.dumps({"event":"error","message":"Das große Feld ist bereits belegt."}))
                    return

                if small in board[big]:
                    await self.send(text_data=json.dumps({"event":"error","message":"Feld bereits belegt."}))
                    return

                # Zug eintragen
                board[big][small] = current_player
                


                state, small_win, small_line = small_result(board[big])


                if state == "win":
                    # txt = small_win + " hat das feld "+ big + " gewonnen"
                    print(small_win , " hat das feld ", big , " gewonnen")
                    # kleines Feld 'big' ist gewonnen von small_win ("X"/"O")
                    # -> hier kannst du z. B. markieren oder ein Event senden
                    room["finished_fields"][big] = small_win
                    # await self.channel_layer.group_send(... "small_over" ...)
                    pass
                elif state == "draw":
                    # kleines Feld 'big' ist unentschieden
                    # print(small_win + " endet unentschieden")
                    room["finished_fields"][big] = "D"
                    pass
                else:
                    # "ongoing" – noch kein Abschluss
                    pass
                
                print("Board: ", room["board"])
                next_big = small

                # Spieler wechseln und persistieren
                room["currentPlayer"] = "O" if current_player == "X" else "X"


                if _is_big_finished(room, next_big):
                    # Ziel-Großfeld ist fertig -> freier Zug
                    room["big_field_to_click"] = ""
                else:
                    room["big_field_to_click"] = next_big


                winner, big_line = big_board_winner(room["finished_fields"])

                # Optional: globales Unentschieden (alle 9 großfelder fertig, aber kein Sieger)
                if not winner and is_global_draw(room["finished_fields"]):
                    winner, big_line = "D", None
                    room["phase"] = "finished"
                    room["big_field_to_click"] = ""
                else:
                    if winner:
                        room["phase"] = "finished"
                        room["big_field_to_click"] = ""

               
                # room["big_field_to_click"] = next_big
                print("Im nächsten Zug anklicken: ", room["big_field_to_click"])

                # Inkrementell broadcasten (kein Dict serialisieren)
                finished_fields_list = [
                    {"big": int(b), "winner": w}
                    for b, w in room.get("finished_fields", {}).items()
                ]

                await self.channel_layer.group_send(
                    self.group,
                    {
                        "type": "game.move",
                        "big": int(big),
                        "small": int(small),
                        "symbol": current_player,
                        "currentPlayer": room["currentPlayer"],
                        "finished_fields": finished_fields_list,  # <--- LISTE statt Dict
                    },
                )

                # 2) Falls Gesamtsieg / globaler Draw
                if winner:
                    await self.channel_layer.group_send(
                        self.group,
                        {
                            "type": "game.over",
                            "winner": winner,                            # "X" / "O" / "D"
                            "line": list(big_line) if big_line else None # große Sieglinie
                        },
                    )

            else:
                print("Klick ins richtige Feld!")

        elif action == "reset":
            room = rooms.get(self.room)
            if not room:
                return

            # Spiellogik zurücksetzen
            room["board"] = {i: {} for i in range(9)}
            room["finished_fields"] = {}
            room["big_field_to_click"] = ""
            room["currentPlayer"] = "X"
            room["phase"] = "playing"

            # Broadcast an alle Spieler
            await self.channel_layer.group_send(
                self.group,
                {
                    "type": "game.reset",
                    "board": [],
                    "currentPlayer": room["currentPlayer"],
                    "finished_fields": [],
                    "big_field_to_click": room["big_field_to_click"],
                    "message": "Spiel wurde neugestartet.",
                }
            )
        elif action == "get_state":
            room = rooms.get(self.room)
            if not room:
                await self.send(text_data=json.dumps({"event": "error", "message": "Raum existiert nicht."}))
                return

            # Symbol->Name Abbildung frisch ableiten
            names_by_symbol = {}
            for ch, s in room["symbols"].items():
                if s in ("X", "O"):
                    names_by_symbol[s] = room["players"].get(ch, "")

            await self.send(text_data=json.dumps({
                "event": "state",
                "room": self.room,
                "phase": room.get("phase", "lobby"),
                "your_id": self.channel_name,
                "your_symbol": room["symbols"].get(self.channel_name),  # kann None sein, wenn diese Verbindung nur „Zuschauen“ ist
                "players": room["players"],
                "symbols": room["symbols"],
                "names_by_symbol": names_by_symbol,
                "board": [(int(b), int(s), v)
                        for b, cells in room["board"].items()
                        for s, v in cells.items()],
                "currentPlayer": room.get("currentPlayer", "X"),
            }))


        # Weitere Actions (start/move/leave) kommen später
        
    # def checkWin():
    #     return

    # async def _broadcast_players(self):
    #     room = rooms.get(self.room)
    #     if not room:
    #         return
    #     players = [
    #         {"id": ch, "name": nick, "is_host": (ch == room["host"])}
    #         for ch, nick in room["players"].items()
    #     ]
    #     await self.channel_layer.group_send(
    #         self.group,
    #         {"type": "players.update", "players": players, "count": len(players)}
    #     )
    async def _broadcast_players(self):
        room = rooms.get(self.room)
        if not room:
            return

        players = [
            {"id": ch, "name": nick, "is_host": (ch == room["host"])}
            for ch, nick in room["players"].items()
        ]

        # Symbol -> Name vorbereiten
        names_by_symbol = {}
        for ch, s in room.get("symbols", {}).items():
            if s in ("X", "O"):
                names_by_symbol[s] = room["players"].get(ch, "")

        await self.channel_layer.group_send(
            self.group,
            {
                "type": "players.update",
                "players": players,
                "count": len(players),
                "symbols": room.get("symbols", {}),       # <— neu
                "names_by_symbol": names_by_symbol,       # <— neu
            }
        )



    # async def players_update(self, event):
    #     await self.send(text_data=json.dumps({
    #         "event": "player_list",
    #         "players": event["players"],  # [{id, name, is_host}, ...]
    #         "count": event.get("count"),
    #     }))

    async def players_update(self, event):
        await self.send(text_data=json.dumps({
            "event": "player_list",
            "players": event["players"],
            "count": event.get("count"),
            "symbols": event.get("symbols", {}),
            "names_by_symbol": event.get("names_by_symbol", {}),
        }))

    async def game_start(self, event):
        await self.send(text_data=json.dumps({
            "event": "start",
            "url": event["url"]
        }))

    # async def game_move(self, event):
    #     await self.send(text_data=json.dumps({
    #         "event": "move",
    #         "big": event["big"],
    #         "small": event["small"],
    #         "symbol": event["symbol"],
    #         "currentPlayer": event["currentPlayer"],
    #         "finished_fields": event["finished_fields"],
    #     }))

    async def game_move(self, event):
        await self.send(text_data=json.dumps({
            "event": "move",
            "big": event["big"],
            "small": event["small"],
            "symbol": event["symbol"],
            "currentPlayer": event["currentPlayer"],
            # event["finished_fields"] ist jetzt eine Liste von {big, winner}
            "finished_fields": event.get("finished_fields", []),
        }))

    async def game_over(self, event):
        await self.send(text_data=json.dumps({
            "event": "game_over",
            "winner": event["winner"],   # "X" / "O" / "D"
            "line": event["line"],       # [a,b,c] auf dem großen Brett oder null
        }))
    
   
    async def game_reset(self, event):
        await self.send(text_data=json.dumps({
            "event": "reset",
            "message": event.get("message"),
            "board": event.get("board", []),
            "currentPlayer": event.get("currentPlayer", "X"),
            "finished_fields": event.get("finished_fields", []),
            "big_field_to_click": event.get("big_field_to_click", ""),
        }))

    
