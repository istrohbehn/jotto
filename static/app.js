const params = new URLSearchParams(window.location.search);

const state = {
  bootstrap: null,
  roomCode: params.get("room")?.toUpperCase() || "",
  currentView: params.get("view") === "game" ? "game" : "lobby",
  pollHandle: null,
  alphabetMarks: {},
};

const els = {
  authPanel: document.getElementById("authPanel"),
  dashboard: document.getElementById("dashboard"),
  lobbyView: document.getElementById("lobbyView"),
  gameView: document.getElementById("gameView"),
  usernameInput: document.getElementById("usernameInput"),
  passwordInput: document.getElementById("passwordInput"),
  signupBtn: document.getElementById("signupBtn"),
  loginBtn: document.getElementById("loginBtn"),
  meLabel: document.getElementById("meLabel"),
  navLobbyBtn: document.getElementById("navLobbyBtn"),
  createPrivateBtn: document.getElementById("createPrivateBtn"),
  findMatchBtn: document.getElementById("findMatchBtn"),
  logoutBtn: document.getElementById("logoutBtn"),
  joinCodeInput: document.getElementById("joinCodeInput"),
  joinCodeBtn: document.getElementById("joinCodeBtn"),
  winsLabel: document.getElementById("winsLabel"),
  lossesLabel: document.getElementById("lossesLabel"),
  finishedLabel: document.getElementById("finishedLabel"),
  waitingPublicLabel: document.getElementById("waitingPublicLabel"),
  dictionaryCount: document.getElementById("dictionaryCount"),
  roomsEmpty: document.getElementById("roomsEmpty"),
  roomsList: document.getElementById("roomsList"),
  invitePanel: document.getElementById("invitePanel"),
  inviteTitle: document.getElementById("inviteTitle"),
  inviteText: document.getElementById("inviteText"),
  joinInviteBtn: document.getElementById("joinInviteBtn"),
  gamePanel: document.getElementById("gamePanel"),
  roomCodeLabel: document.getElementById("roomCodeLabel"),
  copyLinkBtn: document.getElementById("copyLinkBtn"),
  restartBtn: document.getElementById("restartBtn"),
  statusText: document.getElementById("statusText"),
  turnText: document.getElementById("turnText"),
  roundText: document.getElementById("roundText"),
  opponentText: document.getElementById("opponentText"),
  playersList: document.getElementById("playersList"),
  secretInput: document.getElementById("secretInput"),
  secretBtn: document.getElementById("secretBtn"),
  secretWordDisplay: document.getElementById("secretWordDisplay"),
  guessInput: document.getElementById("guessInput"),
  guessBtn: document.getElementById("guessBtn"),
  alphabetTracker: document.getElementById("alphabetTracker"),
  clearAlphabetBtn: document.getElementById("clearAlphabetBtn"),
  guessesEmpty: document.getElementById("guessesEmpty"),
  guessesTable: document.getElementById("guessesTable"),
  guessesBody: document.getElementById("guessesBody"),
  historyEmpty: document.getElementById("historyEmpty"),
  historyList: document.getElementById("historyList"),
  toast: document.getElementById("toast"),
};

function showToast(message) {
  els.toast.textContent = message;
  els.toast.classList.remove("hidden");
  window.clearTimeout(showToast.timeoutId);
  showToast.timeoutId = window.setTimeout(() => els.toast.classList.add("hidden"), 2800);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Request failed.");
  }
  return data;
}

function sanitizeWord(value) {
  return value.trim().toLowerCase().replace(/[^a-z]/g, "").slice(0, 5);
}

function setBusy(isBusy) {
  [
    els.signupBtn,
    els.loginBtn,
    els.navLobbyBtn,
    els.createPrivateBtn,
    els.findMatchBtn,
    els.logoutBtn,
    els.joinCodeBtn,
    els.joinInviteBtn,
    els.secretBtn,
    els.guessBtn,
    els.restartBtn,
    els.copyLinkBtn,
  ].forEach((button) => {
    if (button) {
      button.disabled = isBusy;
    }
  });
}

function syncUrl() {
  const url = new URL(window.location.href);
  if (state.roomCode) {
    url.searchParams.set("room", state.roomCode);
  } else {
    url.searchParams.delete("room");
  }
  if (state.currentView === "game" && state.roomCode) {
    url.searchParams.set("view", "game");
  } else {
    url.searchParams.delete("view");
  }
  window.history.replaceState({}, "", url);
}

function alphabetStorageKey() {
  return state.roomCode ? `jotto-alphabet-${state.roomCode}` : "";
}

function loadAlphabetMarks() {
  const key = alphabetStorageKey();
  if (!key) {
    state.alphabetMarks = {};
    return;
  }
  try {
    state.alphabetMarks = JSON.parse(localStorage.getItem(key) || "{}");
  } catch (_error) {
    state.alphabetMarks = {};
  }
}

function saveAlphabetMarks() {
  const key = alphabetStorageKey();
  if (!key) {
    return;
  }
  localStorage.setItem(key, JSON.stringify(state.alphabetMarks));
}

function goToLobby() {
  state.currentView = "lobby";
  syncUrl();
  render();
}

function goToGame(roomCode) {
  state.roomCode = roomCode ? roomCode.toUpperCase() : "";
  state.currentView = state.roomCode ? "game" : "lobby";
  loadAlphabetMarks();
  syncUrl();
}

function cycleLetterState(letter) {
  const current = state.alphabetMarks[letter] || "clear";
  const next = current === "clear" ? "present" : current === "present" ? "absent" : "clear";
  if (next === "clear") {
    delete state.alphabetMarks[letter];
  } else {
    state.alphabetMarks[letter] = next;
  }
  saveAlphabetMarks();
  renderAlphabetTracker();
}

function renderAlphabetTracker() {
  if (!els.alphabetTracker) {
    return;
  }
  els.alphabetTracker.innerHTML = "";
  const letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".split("");
  for (const letter of letters) {
    const button = document.createElement("button");
    const mark = state.alphabetMarks[letter] || "clear";
    button.type = "button";
    button.className = `alphabet-letter ${mark}`;
    button.textContent = letter;
    button.addEventListener("click", () => cycleLetterState(letter));
    els.alphabetTracker.appendChild(button);
  }
}

function renderRooms(rooms) {
  els.roomsList.innerHTML = "";
  const hasRooms = rooms.length > 0;
  els.roomsEmpty.classList.toggle("hidden", hasRooms);

  for (const room of rooms) {
    const card = document.createElement("button");
    card.className = "room-card";
    card.type = "button";
    card.innerHTML = `
      <div>
        <strong>${room.room_code}</strong>
        <p>${room.visibility === "public" ? "Public match" : "Private room"}${room.opponent_name ? ` vs ${room.opponent_name}` : ""}</p>
      </div>
      <div class="room-meta">
        <span>${room.status}</span>
        <span>Round ${room.round_number}</span>
      </div>
    `;
    card.addEventListener("click", async () => {
      goToGame(room.room_code);
      await refresh();
    });
    els.roomsList.appendChild(card);
  }
}

function renderPlayers(players) {
  els.playersList.innerHTML = "";
  for (const player of players) {
    const item = document.createElement("li");
    const name = document.createElement("strong");
    name.textContent = player.is_you ? `${player.username} (You)` : player.username;

    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = player.has_secret ? "Ready" : "Not ready";

    item.append(name, badge);
    els.playersList.appendChild(item);
  }
}

function renderGuesses(guesses) {
  els.guessesBody.innerHTML = "";
  const hasGuesses = guesses.length > 0;
  els.guessesEmpty.classList.toggle("hidden", hasGuesses);
  els.guessesTable.classList.toggle("hidden", !hasGuesses);

  for (const guess of guesses) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${guess.player_name}</td>
      <td>${guess.guess.toUpperCase()}</td>
      <td>${guess.score}</td>
    `;
    els.guessesBody.appendChild(row);
  }
}

function renderHistory(rounds) {
  els.historyList.innerHTML = "";
  const hasHistory = rounds.length > 0;
  els.historyEmpty.classList.toggle("hidden", hasHistory);

  for (const round of rounds) {
    const card = document.createElement("article");
    card.className = "history-card";
    card.innerHTML = `
      <strong>Round ${round.round_number}</strong>
      <p>${round.winner_name ? `${round.winner_name} finished it` : "In progress"} with ${round.guess_count} total guesses logged.</p>
    `;
    els.historyList.appendChild(card);
  }
}

function renderInvite(invite, user) {
  const visible = Boolean(invite && user && !invite.is_participant);
  els.invitePanel.classList.toggle("hidden", !visible);
  if (!visible) {
    return;
  }

  els.inviteTitle.textContent = `Room ${invite.room_code}`;
  els.inviteText.textContent = invite.can_join
    ? `${invite.players.join(" vs ")}${invite.players.length === 1 ? " is waiting for an opponent." : ""}`
    : "This room is full, but you can still open it later if you join from one of its players’ accounts.";
  els.joinInviteBtn.disabled = !invite.can_join;
}

function renderRoom(room) {
  if (!room) {
    els.gamePanel.classList.add("hidden");
    return;
  }

  els.gamePanel.classList.remove("hidden");
  renderPlayers(room.players);
  renderGuesses(room.guesses);
  renderHistory(room.round_history);

  els.roomCodeLabel.textContent = room.room_code;
  els.roundText.textContent = String(room.round_number);
  els.opponentText.textContent = room.opponent_name || "Waiting";
  els.secretWordDisplay.textContent = room.my_secret_word ? room.my_secret_word.toUpperCase() : "Not set";

  let statusMessage = "Waiting for a second player.";
  let turnMessage = "-";

  if (room.status === "setup") {
    statusMessage = room.my_secret_set ? "Waiting for the other player to lock in a word." : "Choose your secret word.";
    turnMessage = "Secrets first";
  } else if (room.status === "playing") {
    statusMessage = room.is_your_turn ? "Your turn to guess." : `${room.current_turn_name || "Opponent"} is up.`;
    turnMessage = room.is_your_turn ? "You" : (room.current_turn_name || "Opponent");
  } else if (room.status === "finished") {
    statusMessage = `${room.winner_name} won with ${room.winning_word.toUpperCase()}.`;
    turnMessage = "Round over";
  }

  els.statusText.textContent = statusMessage;
  els.turnText.textContent = turnMessage;
  els.secretInput.disabled = room.my_secret_set;
  els.secretBtn.disabled = room.my_secret_set;
  els.guessInput.disabled = !(room.status === "playing" && room.is_your_turn);
  els.guessBtn.disabled = !(room.status === "playing" && room.is_your_turn);
  els.restartBtn.classList.toggle("hidden", !(room.status === "finished" && room.can_restart));
  renderAlphabetTracker();
}

function renderViews(user, room) {
  const showGame = Boolean(user && state.currentView === "game" && state.roomCode);
  els.navLobbyBtn.classList.toggle("hidden", !showGame);
  els.lobbyView.classList.toggle("hidden", showGame);
  els.gameView.classList.toggle("hidden", !showGame);

  if (showGame && !room) {
    els.gamePanel.classList.add("hidden");
  }
}

function render() {
  const data = state.bootstrap || {};
  const user = data.user;
  const lobby = data.lobby;
  const room = data.room;

  els.authPanel.classList.toggle("hidden", Boolean(user));
  els.dashboard.classList.toggle("hidden", !user);
  els.dictionaryCount.textContent = `${data.words_count || 0} playable words loaded`;

  if (!user) {
    renderInvite(data.invite, user);
    renderViews(null, null);
    return;
  }

  els.meLabel.textContent = user.username;
  els.winsLabel.textContent = String(lobby?.stats?.wins || 0);
  els.lossesLabel.textContent = String(lobby?.stats?.losses || 0);
  els.finishedLabel.textContent = String(lobby?.stats?.finished_games || 0);
  els.waitingPublicLabel.textContent = String(lobby?.public_waiting_count || 0);
  renderRooms(lobby?.rooms || []);
  renderInvite(data.invite, user);
  renderViews(user, room);
  renderRoom(room);
}

async function refresh() {
  try {
    const roomQuery = state.roomCode ? `?room=${encodeURIComponent(state.roomCode)}` : "";
    state.bootstrap = await api(`/api/bootstrap${roomQuery}`);
    if (state.currentView === "game" && state.roomCode && !state.bootstrap.room) {
      state.currentView = "lobby";
      syncUrl();
    }
    render();
  } catch (error) {
    showToast(error.message);
  }
}

function startPolling() {
  window.clearInterval(state.pollHandle);
  state.pollHandle = window.setInterval(refresh, 2500);
}

async function auth(path) {
  const username = els.usernameInput.value.trim();
  const password = els.passwordInput.value;
  if (!username || !password) {
    showToast("Enter a username and password.");
    return;
  }

  setBusy(true);
  try {
    await api(path, {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    els.passwordInput.value = "";
    await refresh();
    startPolling();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function createPrivateRoom() {
  setBusy(true);
  try {
    const data = await api("/api/private-room", { method: "POST", body: "{}" });
    goToGame(data.room_code);
    await refresh();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function findMatch() {
  setBusy(true);
  try {
    const data = await api("/api/matchmaking", { method: "POST", body: "{}" });
    goToGame(data.room_code);
    await refresh();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function joinRoom(roomCode) {
  const normalized = roomCode.trim().toUpperCase();
  if (!normalized) {
    showToast("Enter a room code first.");
    return;
  }

  setBusy(true);
  try {
    const data = await api("/api/join-room", {
      method: "POST",
      body: JSON.stringify({ room_code: normalized }),
    });
    els.joinCodeInput.value = "";
    goToGame(data.room_code);
    await refresh();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function saveSecret() {
  const secret = sanitizeWord(els.secretInput.value);
  if (secret.length !== 5) {
    showToast("Enter a valid 5-letter secret word.");
    return;
  }

  setBusy(true);
  try {
    await api("/api/set-secret", {
      method: "POST",
      body: JSON.stringify({ room_code: state.roomCode, secret }),
    });
    els.secretInput.value = "";
    await refresh();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function submitGuess() {
  const guess = sanitizeWord(els.guessInput.value);
  if (guess.length !== 5) {
    showToast("Enter a valid 5-letter guess.");
    return;
  }

  setBusy(true);
  try {
    await api("/api/guess", {
      method: "POST",
      body: JSON.stringify({ room_code: state.roomCode, guess }),
    });
    els.guessInput.value = "";
    await refresh();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function restartRoom() {
  setBusy(true);
  try {
    await api("/api/restart", {
      method: "POST",
      body: JSON.stringify({ room_code: state.roomCode }),
    });
    els.secretInput.disabled = false;
    await refresh();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function logout() {
  setBusy(true);
  try {
    await api("/api/logout", { method: "POST", body: "{}" });
    state.roomCode = "";
    state.currentView = "lobby";
    state.alphabetMarks = {};
    syncUrl();
    state.bootstrap = null;
    render();
  } catch (error) {
    showToast(error.message);
  } finally {
    setBusy(false);
  }
}

async function copyInviteLink() {
  const url = new URL(window.location.origin);
  url.searchParams.set("room", state.roomCode);
  try {
    await navigator.clipboard.writeText(url.toString());
    showToast("Invite link copied.");
  } catch (_error) {
    showToast(url.toString());
  }
}

function bindEvents() {
  els.signupBtn.addEventListener("click", () => auth("/api/signup"));
  els.loginBtn.addEventListener("click", () => auth("/api/login"));
  els.navLobbyBtn.addEventListener("click", goToLobby);
  els.createPrivateBtn.addEventListener("click", createPrivateRoom);
  els.findMatchBtn.addEventListener("click", findMatch);
  els.logoutBtn.addEventListener("click", logout);
  els.joinCodeBtn.addEventListener("click", () => joinRoom(els.joinCodeInput.value));
  els.joinInviteBtn.addEventListener("click", () => joinRoom(state.roomCode));
  els.secretBtn.addEventListener("click", saveSecret);
  els.guessBtn.addEventListener("click", submitGuess);
  els.restartBtn.addEventListener("click", restartRoom);
  els.copyLinkBtn.addEventListener("click", copyInviteLink);
  els.clearAlphabetBtn.addEventListener("click", () => {
    state.alphabetMarks = {};
    saveAlphabetMarks();
    renderAlphabetTracker();
  });
}

async function boot() {
  bindEvents();
  loadAlphabetMarks();
  syncUrl();
  await refresh();
  startPolling();
}

boot();
