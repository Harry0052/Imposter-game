// =========================================================
// Imposter Media — client.
// Actions go through REST (POST with X-Player-Id header).
// WebSocket is a one-way push channel: the server fires a
// "state" message after every mutation and we re-render.
// =========================================================

const $ = (id) => document.getElementById(id);

const state = {
  ws: null,
  code: null,
  playerId: null,
  me: null,
  latest: null,
  timerInterval: null,
};

// ---------- Screen + phase ----------

function showScreen(id) {
  document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
  $(id).classList.add("active");
}

function showPhase(state) {
  // State values use snake_case (e.g. "showing_content"); element ids use
  // dashes ("phase-showing-content"). This keeps both sides readable.
  const id = "phase-" + state.replaceAll("_", "-");
  const current = document.querySelector(".phase.active");
  const next = $(id);
  if (!next || current === next) return;
  if (current) current.classList.remove("active");
  // Force restart of the phase-in animation.
  next.classList.remove("active");
  void next.offsetWidth;
  next.classList.add("active");
}

// ---------- REST helper ----------

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (state.playerId) headers["X-Player-Id"] = state.playerId;
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
}

// ---------- Toast + haptics ----------

let toastTimer = null;
function toast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 1600);
}

function haptic(ms = 10) {
  if (navigator.vibrate) try { navigator.vibrate(ms); } catch {}
}

// ---------- Home: create / join ----------

async function createRoom() {
  const name = $("name-input").value.trim();
  if (!name) return setError("Enter your name first.");
  setError("");
  try {
    const data = await api("POST", "/api/rooms", { name });
    state.playerId = data.player_id;
    haptic(20);
    connect(data.code, data.player_id);
  } catch (e) {
    setError(e.message);
  }
}

async function joinRoom() {
  const name = $("name-input").value.trim();
  const code = $("code-input").value.trim().toUpperCase();
  if (!name) return setError("Enter your name first.");
  if (code.length < 4) return setError("Enter the room code.");
  setError("");
  try {
    const data = await api("POST", `/api/rooms/${code}/join`, { name });
    state.playerId = data.player_id;
    haptic(20);
    connect(data.code, data.player_id);
  } catch (e) {
    setError(e.message);
  }
}

function setError(msg) { $("landing-error").textContent = msg; }

// ---------- WebSocket (receive-only) ----------

function connect(code, playerId) {
  state.code = code;
  state.playerId = playerId;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/${code}/${playerId}`);
  state.ws = ws;

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "state") render(msg.state);
  };
  ws.onopen = () => showScreen("screen-room");
  ws.onclose = () => {
    showScreen("screen-home");
    setError("Disconnected. Rejoin to keep playing.");
    clearInterval(state.timerInterval);
  };
}

// ---------- Actions ----------

const actions = {
  start:             () => api("POST",  `/api/rooms/${state.code}/start`),
  word:        (word) => api("POST",  `/api/rooms/${state.code}/word`, { word }),
  startDiscussion:   () => api("POST",  `/api/rooms/${state.code}/start-discussion`),
  startVote:         () => api("POST",  `/api/rooms/${state.code}/start-voting`),
  vote:      (target) => api("POST",  `/api/rooms/${state.code}/vote`, { target_id: target }),
  imposterGuess: (g) => api("POST",  `/api/rooms/${state.code}/imposter-guess`, { guess: g }),
  showScoreboard:    () => api("POST",  `/api/rooms/${state.code}/show-scoreboard`),
  next:              () => api("POST",  `/api/rooms/${state.code}/next`),
  reset:             () => api("POST",  `/api/rooms/${state.code}/reset`),
  updateSettings: (patch) => api("PATCH", `/api/rooms/${state.code}/settings`, patch),
};

// ---------- Render ----------

function render(s) {
  state.latest = s;
  state.me = s.me;

  $("room-code").textContent = s.code;
  $("round-number").textContent = s.round;

  // Haptic on phase transition for feedback
  const prevPhase = document.body.dataset.phase;
  if (prevPhase && prevPhase !== s.phase) haptic(15);
  document.body.dataset.phase = s.phase;

  updateTimer(s.phase_ends_at);
  showPhase(s.phase);

  switch (s.phase) {
    case "waiting_lobby":     renderLobby(s); break;
    case "showing_content":   renderViewing(s); break;
    case "submitting_words":  renderWord(s); break;
    case "revealing_words":   renderReveal(s); break;
    case "discussion":        renderDiscussion(s); break;
    case "voting":            renderVoting(s); break;
    case "revealing_results": renderResult(s); break;
    case "scoreboard":        renderScoreboard(s); break;
  }
}

// ---- Lobby ----

function renderLobby(s) {
  $("room-code-big").textContent = s.code;
  const cap = s.settings?.max_players ?? s.max_players;
  $("player-count").textContent = `${s.players.length}/${cap}`;

  const list = $("player-list");
  list.innerHTML = "";
  s.players.forEach((p) => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${esc(p.name)}</span><div class="tags"></div>`;
    const tags = li.querySelector(".tags");
    if (p.is_host) tags.innerHTML += `<span class="tag host">host</span>`;
    if (p.id === state.playerId) tags.innerHTML += `<span class="tag me">you</span>`;
    list.appendChild(li);
  });

  const amHost = state.me?.is_host;
  const enough = s.players.filter((p) => p.connected).length >= s.min_players;
  $("btn-start").classList.toggle("hidden", !amHost);
  $("btn-start").disabled = !enough;

  if (!enough) {
    $("lobby-hint").textContent =
      `Need at least ${s.min_players} players to start (${s.players.length}/${s.min_players}).`;
  } else if (amHost) {
    $("lobby-hint").textContent = "You're the host. Tap Start when everyone is in.";
  } else {
    $("lobby-hint").textContent = "Waiting for the host to start…";
  }

  renderSettings(s, amHost);
}

// ---- Settings panel (in lobby) ----

const CATEGORY_LABELS = {
  influencers: "Influencers", sports: "Sports", music: "Music",
  youtube: "YouTube", tiktok: "TikTok", memes: "Memes",
  movies: "Movies", random_objects: "Random objects",
  pictures: "Pictures", videos: "Videos",
};

function renderSettings(s, amHost) {
  const settings = s.settings || {};
  const badge = $("settings-role");
  badge.textContent = amHost ? "Host" : "Read-only";
  badge.classList.toggle("editable", !!amHost);

  // Populate the category dropdown once (keep the "Any" option on top).
  const catSel = $("setting-category");
  const known = Array.from(catSel.options).map(o => o.value);
  (settings.available_categories || []).forEach(cat => {
    if (!known.includes(cat)) {
      const o = document.createElement("option");
      o.value = cat;
      o.textContent = CATEGORY_LABELS[cat] || cat;
      catSel.appendChild(o);
    }
  });

  // Sync values from server state.
  $("setting-rounds").value             = String(settings.total_rounds ?? 5);
  $("setting-category").value           = settings.category ?? "any";
  $("setting-max").value                = String(settings.max_players ?? 10);
  $("setting-word-seconds").value       = String(settings.word_submission_seconds ?? 25);
  $("setting-discussion-seconds").value = String(settings.discussion_seconds ?? 30);
  $("setting-voting-seconds").value     = String(settings.voting_seconds ?? 30);

  // Host can edit; everyone else sees read-only.
  [
    "setting-rounds", "setting-category", "setting-max",
    "setting-word-seconds", "setting-discussion-seconds", "setting-voting-seconds",
  ].forEach(id => { $(id).disabled = !amHost; });
}

function settingsChanged() {
  // Build a partial patch and send it. Ignores errors so users don't get
  // blocked by a race (e.g. player joins right as host tweaks max_players).
  const patch = {
    total_rounds: Number($("setting-rounds").value),
    category: $("setting-category").value,   // "any" is handled on the server
    max_players: Number($("setting-max").value),
    word_submission_seconds: Number($("setting-word-seconds").value),
    discussion_seconds: Number($("setting-discussion-seconds").value),
    voting_seconds: Number($("setting-voting-seconds").value),
  };
  actions.updateSettings(patch).catch(e => toast(e.message));
}

// ---- Viewing ----

function renderViewing(s) {
  const box = $("content-box");
  const me = s.me;
  box.innerHTML = "";
  if (!me || !me.content) { box.textContent = "Loading…"; return; }

  if (me.content_kind === "image") {
    const img = document.createElement("img");
    img.src = me.content;
    img.alt = "";
    box.appendChild(img);
  } else if (me.content_kind === "video") {
    const v = document.createElement("video");
    v.src = me.content;
    v.controls = true;
    v.autoplay = true;
    v.muted = true;
    v.playsInline = true;
    box.appendChild(v);
  } else {
    box.textContent = me.content;
  }
}

// ---- Word ----

function renderWord(s) {
  const input = $("word-input");
  const btn = $("btn-submit-word");
  const locked = !!state.me?.word;

  input.disabled = locked;
  btn.disabled = locked;

  $("word-status").textContent = locked
    ? `Locked in: "${state.me.word}". Waiting for the others…`
    : "";
  if (!locked) $("word-error").textContent = "";

  const prog = $("word-progress");
  prog.innerHTML = "";
  s.players.forEach((p) => {
    const li = document.createElement("li");
    const tag = p.has_word
      ? `<span class="tag ready">ready</span>`
      : `<span class="tag thinking">thinking…</span>`;
    li.innerHTML = `<span>${esc(p.name)}</span>${tag}`;
    prog.appendChild(li);
  });
}

// ---- Reveal ----

function renderReveal(s) {
  const list = $("word-reveal");
  list.innerHTML = "";
  s.players.forEach((p) => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${esc(p.name)}</span>
                    <span class="word">${esc(p.word || "—")}</span>`;
    list.appendChild(li);
  });
  const amHost = state.me?.is_host;
  // Reveal-phase button is "Start discussion" now; voting follows discussion.
  $("btn-start-discussion").classList.toggle("hidden", !amHost);
  $("reveal-hint").textContent = amHost
    ? "When everyone has read the words, open discussion."
    : "Waiting for the host to start discussion…";
}

// ---- Discussion ----

function renderDiscussion(s) {
  const list = $("discussion-words");
  list.innerHTML = "";
  s.players.forEach((p) => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${esc(p.name)}</span>
                    <span class="word">${esc(p.word || "—")}</span>`;
    list.appendChild(li);
  });
  const amHost = state.me?.is_host;
  $("btn-start-vote").classList.toggle("hidden", !amHost);
  $("discussion-hint").textContent = amHost
    ? "Let the group talk. Voting starts when the timer ends, or you can skip."
    : "Talk it out. Voting starts when the timer ends.";
}

// ---- Voting ----

function renderVoting(s) {
  const list = $("vote-list");
  list.innerHTML = "";
  const myVote = state.me?.vote;

  s.players.forEach((p) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = p.name + (p.id === state.playerId ? " (you)" : "");
    btn.disabled = p.id === state.playerId || !!myVote;
    if (myVote === p.id) btn.classList.add("selected");
    btn.onclick = () => {
      haptic(15);
      actions.vote(p.id).catch((err) => { $("vote-status").textContent = err.message; });
    };
    li.appendChild(btn);
    list.appendChild(li);
  });

  const votedCount = s.players.filter((p) => p.has_voted).length;
  $("vote-status").textContent = myVote
    ? `Vote locked. ${votedCount}/${s.players.length} have voted.`
    : `${votedCount}/${s.players.length} have voted.`;
}

// ---- Result ----

function renderResult(s) {
  const r = s.last_result;
  if (!r) return;

  const headline = $("result-headline");
  if (r.group_won) {
    headline.textContent = "🎉 Group wins!";
    headline.className = "result-headline win";
  } else {
    headline.textContent = "🫣 Imposter wins!";
    headline.className = "result-headline lose";
  }
  $("result-imposter").textContent = `The imposter was ${r.imposter_name}.`;

  // Full content reveal — side by side
  renderContentCardBody($("result-normal"), r.normal_content, r.content_kind);
  renderContentCardBody($("result-imposter-content"), r.imposter_content, r.content_kind);

  // Words list, imposter row highlighted
  const words = $("result-words");
  words.innerHTML = "";
  s.players.forEach((p) => {
    const li = document.createElement("li");
    if (p.was_imposter) li.classList.add("imposter");
    const label = p.was_imposter ? " (imposter)" : "";
    li.innerHTML = `<span>${esc(p.name)}${label}</span>
                    <span class="word">${esc(p.word || "—")}</span>`;
    words.appendChild(li);
  });

  // Vote tallies
  const counts = $("result-vote-counts");
  counts.innerHTML = "";
  const entries = Object.entries(r.vote_counts);
  if (entries.length === 0) {
    counts.innerHTML = `<div><span>No votes cast</span><span>—</span></div>`;
  } else {
    entries.forEach(([name, n]) => {
      const row = document.createElement("div");
      row.innerHTML = `<span>${esc(name)}</span><span>${n} vote${n === 1 ? "" : "s"}</span>`;
      counts.appendChild(row);
    });
  }

  const amHost = state.me?.is_host;
  const amImposter = state.me?.is_imposter === true;
  const guessSection = $("imposter-guess-section");
  const guessInput = $("imposter-guess-input");
  const guessBtn = $("btn-imposter-guess");
  const guessStatus = $("imposter-guess-status");

  // Only the imposter sees the bonus guess form. Everyone else: hide it.
  guessSection.classList.toggle("hidden", !amImposter);
  if (amImposter) {
    const alreadyGuessed = !!state.me?.imposter_guess;
    guessInput.disabled = alreadyGuessed;
    guessBtn.disabled = alreadyGuessed;
    if (alreadyGuessed) {
      const ok = r.imposter_guess_correct;
      guessStatus.textContent = ok
        ? `Correct! "${state.me.imposter_guess}" — +2 points.`
        : `You guessed "${state.me.imposter_guess}". No bonus.`;
      guessStatus.style.color = ok ? "var(--good)" : "var(--muted)";
    } else {
      guessStatus.textContent = "";
    }
  }

  // Host advances to the scoreboard.
  $("btn-show-scoreboard").classList.toggle("hidden", !amHost);
  $("btn-reset").classList.toggle("hidden", !amHost);
}

// ---- Scoreboard ----

function renderScoreboard(s) {
  const head = $("scoreboard-head");
  const sub = $("scoreboard-sub");
  const totalRounds = s.settings?.total_rounds ?? s.total_rounds ?? 0;
  const gameOver = !!s.game_over;

  if (gameOver) {
    head.textContent = "Final scoreboard";
    const top = [...s.players].sort((a, b) => (b.score || 0) - (a.score || 0))[0];
    sub.textContent = top ? `${top.name} wins with ${top.score} points!` : "";
  } else {
    head.textContent = `Round ${s.round} of ${totalRounds}`;
    sub.textContent = "Scores so far. Next round starts when the host is ready.";
  }

  const list = $("scoreboard-list");
  list.innerHTML = "";
  const sorted = [...s.players].sort((a, b) => (b.score || 0) - (a.score || 0));
  const topScore = sorted[0]?.score ?? 0;
  const deltas = s.last_result?.score_delta || {};

  sorted.forEach((p, i) => {
    const li = document.createElement("li");
    if ((p.score || 0) === topScore && topScore > 0) li.classList.add("leader");
    const d = deltas[p.name] || 0;
    li.innerHTML = `
      <div class="rank">#${i + 1}</div>
      <div>
        <div>${esc(p.name)}${p.was_imposter ? ' <span class="tag host">imposter</span>' : ""}</div>
      </div>
      <div class="delta ${d > 0 ? "" : "none"}">${d > 0 ? `+${d}` : "—"}</div>
      <div class="score">${p.score || 0}</div>
    `;
    list.appendChild(li);
  });

  const amHost = state.me?.is_host;
  $("btn-next").classList.toggle("hidden", !amHost || gameOver);
  $("btn-play-again").classList.toggle("hidden", !amHost || !gameOver);
  $("btn-reset-from-scoreboard").classList.toggle("hidden", !amHost || gameOver);
}

function renderContentCardBody(el, content, kind) {
  el.innerHTML = "";
  if (kind === "image") {
    const img = document.createElement("img");
    img.src = content;
    img.alt = "";
    el.appendChild(img);
  } else if (kind === "video") {
    const v = document.createElement("video");
    v.src = content;
    v.controls = true;
    v.muted = true;
    v.playsInline = true;
    el.appendChild(v);
  } else {
    el.textContent = content;
  }
}

// ---------- Timer ----------

function updateTimer(endsAt) {
  clearInterval(state.timerInterval);
  const el = $("timer");
  if (!endsAt) { el.textContent = ""; el.classList.remove("urgent"); return; }
  const tick = () => {
    const remaining = Math.max(0, Math.ceil(endsAt - Date.now() / 1000));
    el.textContent = remaining + "s";
    el.classList.toggle("urgent", remaining <= 5 && remaining > 0);
    if (remaining <= 0) { clearInterval(state.timerInterval); }
  };
  tick();
  state.timerInterval = setInterval(tick, 250);
}

// ---------- Utility ----------

function esc(s) {
  if (s == null) return "";
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function copyRoomCode() {
  if (!state.code) return;
  try {
    if (navigator.share) {
      // Prefer native share sheet on mobile
      await navigator.share({
        title: "Imposter Media",
        text: `Join my game with code ${state.code}`,
        url: location.origin,
      });
      return;
    }
    await navigator.clipboard.writeText(state.code);
    toast("Code copied!");
    haptic(15);
  } catch {
    // User cancelled share or clipboard unavailable — quiet fail.
  }
}

// ---------- Wire up ----------

$("btn-create").onclick = createRoom;
$("btn-join").onclick   = joinRoom;
$("code-input").addEventListener("input", (e) => {
  e.target.value = e.target.value.toUpperCase();
});

// Room code hero: tap to copy / share
const codeHero = $("code-hero");
codeHero.addEventListener("click", copyRoomCode);
codeHero.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); copyRoomCode(); }
});

$("btn-start").onclick            = () => { haptic(20); actions.start().catch((e) => toast(e.message)); };
$("btn-start-discussion").onclick = () => { haptic(20); actions.startDiscussion().catch((e) => toast(e.message)); };
$("btn-start-vote").onclick       = () => { haptic(20); actions.startVote().catch((e) => toast(e.message)); };
$("btn-show-scoreboard").onclick  = () => { haptic(20); actions.showScoreboard().catch((e) => toast(e.message)); };
$("btn-next").onclick             = () => { haptic(20); actions.next().catch((e) => toast(e.message)); };
$("btn-play-again").onclick       = () => { haptic(20); actions.reset().catch((e) => toast(e.message)); };
$("btn-reset").onclick            = () => { haptic(20); actions.reset().catch((e) => toast(e.message)); };
$("btn-reset-from-scoreboard").onclick = () => { haptic(20); actions.reset().catch((e) => toast(e.message)); };

// Settings panel: host changes are pushed with a short debounce.
let _settingsDebounce = null;
["setting-rounds", "setting-category", "setting-max",
 "setting-word-seconds", "setting-discussion-seconds", "setting-voting-seconds"
].forEach(id => {
  $(id).addEventListener("change", () => {
    clearTimeout(_settingsDebounce);
    _settingsDebounce = setTimeout(settingsChanged, 150);
  });
});

// Imposter guess submission.
$("btn-imposter-guess").onclick = () => {
  const g = $("imposter-guess-input").value.trim();
  if (!g) { $("imposter-guess-status").textContent = "Type a guess first."; return; }
  haptic(15);
  actions.imposterGuess(g).catch(err => {
    $("imposter-guess-status").textContent = err.message;
  });
};
$("imposter-guess-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("btn-imposter-guess").click();
});

// --- Word input: enforce one-word rule client-side ---
// 1. Block spacebar entirely so typing cannot add a space.
// 2. Strip any whitespace pasted into the input, and show an inline error
//    so the user understands why their "two words" got collapsed.
const wordInput = $("word-input");
wordInput.addEventListener("keydown", (e) => {
  if (e.key === " " || e.key === "Spacebar") {
    e.preventDefault();
    $("word-error").textContent = "One word only.";
  }
});
wordInput.addEventListener("input", (e) => {
  const had = e.target.value;
  const clean = had.replace(/\s+/g, "");
  if (clean !== had) {
    e.target.value = clean;
    $("word-error").textContent = "One word only.";
  } else {
    $("word-error").textContent = "";
  }
});

$("btn-submit-word").onclick = () => {
  const raw = wordInput.value;
  const w = raw.trim();
  if (!w) { $("word-error").textContent = "Type a word first."; return; }
  if (/\s/.test(w)) { $("word-error").textContent = "One word only."; return; }
  haptic(15);
  actions.word(w).catch((err) => { $("word-error").textContent = err.message; });
};
wordInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("btn-submit-word").click();
});
