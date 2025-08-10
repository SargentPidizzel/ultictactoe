function handleJoinSubmit(e) {
            e.preventDefault();
            const code = document.getElementById('joinCode').value.trim();
            const name_join = document.getElementById('username_join').value;

            connectToRoom(code, name_join);
            closeModal("joinModal");
            // ❌ openLobbyModal(code);   // <-- ENTFERNEN
        }

let socket = null;

        function wsBase() {
            return (location.protocol === "https:" ? "wss://" : "ws://") + window.location.host;
        }

        function handleCreateSubmit(e) {
            e.preventDefault();
            const nickname = (document.getElementById('username_create').value || "").trim() || "Spieler";
            closeModal('createModal');
            createGame(nickname);   // 1) Code anfordern  2) Raum-WS verbinden  3) Warteraum anzeigen
        }

        function createGame(nickname) {
            const alloc = new WebSocket(`${wsBase()}/ws/lobby/`);
            alloc.onopen = () => alloc.send(JSON.stringify({ action: "request_code" }));
            alloc.onmessage = (e) => {
                const msg = JSON.parse(e.data);
                if (msg.event === "code_allocated") {
                const code = msg.code;
                alloc.close();
                // ❌ openLobbyModal(code);  // <-- ENTFERNEN
                connectToRoom(code, nickname);
                }
            };
            alloc.onerror = () => alert("Konnte keinen Code anfordern.");
        }

        function openLobbyModal(code) {
            document.getElementById('createdCode').textContent = code;
            renderPlayerList([]);
            setLobbyStatus("Verbinde…");
            openModal('lobbyModal');
        }

        function connectToRoom(roomCode, nickname) {
            window.myNickname = nickname;
            const url = `${wsBase()}/ws/game/${encodeURIComponent(roomCode)}/`;
            socket = new WebSocket(url);
            console.log("NICKNAME: ", nickname)

            socket.onopen = () => {
                console.log('WS open', { roomCode, nickname});
                socket.send(JSON.stringify({ action: "create_or_join", nickname }));
                setLobbyStatus(`Verbunden mit Lobby ${roomCode} – warte auf Spieler…`);
            };

            socket.onmessage = (e) => {
                const msg = JSON.parse(e.data);

                if (msg.event === "joined") {
                    window.myId = msg.your_id;            // falls du das wie besprochen sendest
                    // jetzt ist der Join bestätigt -> Lobby anzeigen
                    openLobbyModal(msg.room);
                    console.log('joined; host?', msg.you_are_host);
                }

                if (msg.event === "player_list") {
                    renderPlayerList(msg.players);
                    updateLobbyCount?.(msg.count ?? msg.players.length);

                    const startBtn = document.getElementById("startGame_btn");
                    const iAmHost = window.myId && msg.players.some(p => p.id === window.myId && p.is_host);
                    if (iAmHost && (msg.count ?? msg.players.length) >= 2) {
                    startBtn.classList.remove("hidden");
                    } else {
                    startBtn.classList.add("hidden");
                    }
                }

                if (msg.event === "error") {
                    // sicherstellen, dass keine leere Lobby offen bleibt
                    closeModal('lobbyModal');
                    alert(msg.message);                  // oder eigenes Fehler-UI
                    // optional: Join/Create-Modal wieder öffnen
                    openModal('playModal');
                }

                if (msg.event === "start") {
                    // optional: Modal schließen
                    closeModal('lobbyModal');
                    // Weiterleitung
                    window.location.href = msg.url;
                }
            };

            socket.onclose = () => {
                // wenn aus irgendeinem Grund geschlossen wurde, bevor joined kam:
                const lobbyOpen = !document.getElementById('lobbyModal').classList.contains('hidden');
                if (lobbyOpen && !window.myId) closeModal('lobbyModal');
            };
        }

        function leaveLobby() {
            try { if (socket && socket.readyState === WebSocket.OPEN) socket.close(1000, "leave"); } catch (e) { }
            socket = null;
        }

        // UI-Helfer
        function renderPlayerList(players) {
            const ul = document.getElementById("playerList");
            if (!ul) return;
            ul.innerHTML = "";

            players.forEach(p => {
                const li = document.createElement("li");
                // Name setzen
                li.textContent = p.name;

                // Krone nur für den *einen* Host
                if (p.is_host) {
                    const icon = document.createElement("i");
                    icon.className = "fa-solid fa-crown text-yellow-400";
                    icon.style.marginLeft = "0.4rem";
                    li.appendChild(icon);
                    // document.getElementById("startGame_btn").classList.remove("hidden")
                }

                ul.appendChild(li);
            });
        }
        function setLobbyStatus(text) {
            const el = document.getElementById("lobbyStatus");
            if (el) el.textContent = text;
        }

function openModal(id) {
            const modal = document.getElementById(id);
            const overlay = modal.querySelector('[data-close].absolute');
            const panel = modal.querySelector('[role="dialog"]');

            modal.classList.remove('hidden');

            requestAnimationFrame(() => {
                overlay.classList.add('opacity-100');
                panel.classList.remove('opacity-0', 'scale-95');
                panel.classList.add('opacity-100', 'scale-100');
                document.documentElement.classList.add('overflow-hidden'); // Scroll lock
            });
        }

        function closeModal(id) {
            const modal = document.getElementById(id);
            const overlay = modal.querySelector('[data-close].absolute');
            const panel = modal.querySelector('[role="dialog"]');

            overlay.classList.remove('opacity-100');
            panel.classList.remove('opacity-100', 'scale-100');
            panel.classList.add('opacity-0', 'scale-95');

            setTimeout(() => {
                modal.classList.add('hidden');
                document.documentElement.classList.remove('overflow-hidden');
            }, 200);
        }

        function actionAndClose(action, id) {
            console.log('Action:', action); // z. B. join/create
            closeModal(id);
        }

        // Schließen über Overlay/Buttons
        document.addEventListener('click', (e) => {
            if (e.target.matches('[data-close]')) {
                const modal = e.target.closest('.fixed.inset-0');
                if (modal) closeModal(modal.id);
            }
        });

        // ESC schließt
        document.addEventListener('keydown', (e) => {
            const openModalEl = document.querySelector('.fixed.inset-0:not(.hidden)');
            if (openModalEl && e.key === 'Escape') {
                closeModal(openModalEl.id);
            }
        });


        function updateLobbyCount(n) {
            const el = document.getElementById('lobbyCount');
            if (el) el.textContent = n;
        }

        // Um den klick auf den Start Game Button zu registrieren
        document.getElementById("startGame_btn")?.addEventListener("click", () => {
            socket?.send(JSON.stringify({ action: "start_game" }));
        });