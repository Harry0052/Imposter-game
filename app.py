"""
FastAPI app: thin web adapter over `game_logic`.

Every mutating endpoint does three things:
  1. Load the room (`rooms.get_room`).
  2. Call a function in `game_logic` (all rules live there).
  3. Broadcast the new state and return `{ok: True}`.

`GameError` raised by `game_logic` becomes HTTP 400. Other errors pass
through to FastAPI's normal handler.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import game_logic
from content import pick_random_pair
from game_logic import (
    GameError,
    GameState,
    Room,
)
from rooms import (
    ROOMS,
    create_room,
    delete_room_if_empty,
    get_room,
    join_room,
)
from ws_manager import ConnectionManager


app = FastAPI(title="Imposter Media")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

manager = ConnectionManager()

# One active timer task per room. Starting a new phase cancels any previous.
_phase_timers: Dict[str, asyncio.Task] = {}


# ============================================================
# Request schemas
# ============================================================

class CreateRoomRequest(BaseModel):
    name: str = Field(min_length=1, max_length=20)


class JoinRoomRequest(BaseModel):
    name: str = Field(min_length=1, max_length=20)


class WordRequest(BaseModel):
    word: str


class VoteRequest(BaseModel):
    target_id: str


class GuessRequest(BaseModel):
    guess: str


class SettingsRequest(BaseModel):
    # Every field is optional so the host can PATCH any subset of settings.
    total_rounds: Optional[int] = None
    category: Optional[str] = None   # None or "any" = no filter; otherwise a category name
    max_players: Optional[int] = None
    number_of_imposters: Optional[int] = None
    showing_content_seconds: Optional[int] = None
    word_submission_seconds: Optional[int] = None
    discussion_seconds: Optional[int] = None
    voting_seconds: Optional[int] = None


# ============================================================
# Guards
# ============================================================

def _require_room(code: str) -> Room:
    room = get_room(code)
    if not room:
        raise HTTPException(404, "Room not found")
    return room


def _require_player(room: Room, player_id: Optional[str]):
    if not player_id or player_id not in room.players:
        raise HTTPException(403, "You are not a member of this room")
    return room.players[player_id]


def _require_host(room: Room, player_id: Optional[str]):
    player = _require_player(room, player_id)
    if not player.is_host:
        raise HTTPException(403, "Host only")
    return player


def _handle(callable_, *args, **kwargs):
    """Invoke a game_logic function and map GameError -> 400."""
    try:
        return callable_(*args, **kwargs)
    except GameError as e:
        raise HTTPException(400, str(e))


# ============================================================
# Phase timers
# ============================================================

def _schedule_phase_end(room: Room, seconds: float) -> None:
    """Replace any existing timer for this room with a new one."""
    prev = _phase_timers.get(room.code)
    if prev and not prev.done():
        prev.cancel()
    _phase_timers[room.code] = asyncio.create_task(
        _phase_timer(room, room.state, seconds)
    )


def _cancel_timer(room: Room) -> None:
    t = _phase_timers.get(room.code)
    if t and not t.done():
        t.cancel()


async def _phase_timer(room: Room, expected: GameState, seconds: float) -> None:
    try:
        await asyncio.sleep(seconds)
    except asyncio.CancelledError:
        return
    # Stale timer — the phase moved on (all words in, host advanced, etc.).
    if room.state != expected:
        return

    # Each timed phase is responsible for kicking off the next timer when
    # it auto-advances. Phases without a timer (revealing_words, scoreboard,
    # revealing_results) are driven by host clicks.
    if expected == GameState.SHOWING_CONTENT:
        game_logic.end_showing_content(room)
        _schedule_phase_end(room, room.settings.word_submission_seconds)
    elif expected == GameState.SUBMITTING_WORDS:
        game_logic.reveal_words(room)
    elif expected == GameState.DISCUSSION:
        game_logic.start_voting(room)
        _schedule_phase_end(room, room.settings.voting_seconds)
    elif expected == GameState.VOTING:
        game_logic.calculate_results(room)

    await manager.broadcast(room)


# ============================================================
# Page
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ============================================================
# REST API
# ============================================================

@app.post("/api/rooms")
async def create_room_endpoint(req: CreateRoomRequest):
    """Create a new room; caller becomes the host."""
    room = create_room(req.name)
    host = next(iter(room.players.values()))
    return {"code": room.code, "player_id": host.id, "is_host": True}


@app.post("/api/rooms/{code}/join")
async def join_room_endpoint(code: str, req: JoinRoomRequest):
    try:
        room, player = join_room(code, req.name)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except GameError as e:
        raise HTTPException(400, str(e))
    await manager.broadcast(room)
    return {"code": room.code, "player_id": player.id, "is_host": player.is_host}


@app.get("/api/rooms/{code}")
async def get_room_state_endpoint(
    code: str,
    x_player_id: Optional[str] = Header(default=None),
):
    room = _require_room(code)
    return game_logic.serialize_room(room, for_player_id=x_player_id)


@app.post("/api/rooms/{code}/start")
async def start_round_endpoint(
    code: str,
    x_player_id: str = Header(...),
):
    room = _require_room(code)
    _require_host(room, x_player_id)
    _handle(game_logic.start_round, room, pick_random_pair(room.settings.category))
    _schedule_phase_end(room, room.settings.showing_content_seconds)
    await manager.broadcast(room)
    return {"ok": True}


@app.post("/api/rooms/{code}/word")
async def submit_word_endpoint(
    code: str,
    req: WordRequest,
    x_player_id: str = Header(...),
):
    room = _require_room(code)
    _require_player(room, x_player_id)
    _handle(game_logic.submit_word, room, x_player_id, req.word)

    # Jump straight to reveal if this was the last word.
    if game_logic.all_words_submitted(room):
        game_logic.reveal_words(room)
        _cancel_timer(room)

    await manager.broadcast(room)
    return {"ok": True}


@app.post("/api/rooms/{code}/start-discussion")
async def start_discussion_endpoint(
    code: str,
    x_player_id: str = Header(...),
):
    """Host action: reveal -> discussion (timed). Frontend flow is
    reveal words → discussion → voting."""
    room = _require_room(code)
    _require_host(room, x_player_id)
    _handle(game_logic.start_discussion, room)
    _schedule_phase_end(room, room.settings.discussion_seconds)
    await manager.broadcast(room)
    return {"ok": True}


@app.post("/api/rooms/{code}/start-voting")
async def start_voting_endpoint(
    code: str,
    x_player_id: str = Header(...),
):
    """Host action: discussion -> voting. Accepts revealing_words too as a
    backwards-compat 'skip discussion' shortcut."""
    room = _require_room(code)
    _require_host(room, x_player_id)
    _handle(game_logic.start_voting, room)
    _schedule_phase_end(room, room.settings.voting_seconds)
    await manager.broadcast(room)
    return {"ok": True}


@app.post("/api/rooms/{code}/vote")
async def submit_vote_endpoint(
    code: str,
    req: VoteRequest,
    x_player_id: str = Header(...),
):
    room = _require_room(code)
    _require_player(room, x_player_id)
    _handle(game_logic.submit_vote, room, x_player_id, req.target_id)

    if game_logic.all_votes_submitted(room):
        game_logic.calculate_results(room)
        _cancel_timer(room)

    await manager.broadcast(room)
    return {"ok": True}


@app.post("/api/rooms/{code}/next")
async def next_round_endpoint(
    code: str,
    x_player_id: str = Header(...),
):
    room = _require_room(code)
    _require_host(room, x_player_id)
    _handle(game_logic.next_round, room, pick_random_pair(room.settings.category))
    _schedule_phase_end(room, room.settings.showing_content_seconds)
    await manager.broadcast(room)
    return {"ok": True}


@app.post("/api/rooms/{code}/imposter-guess")
async def imposter_guess_endpoint(
    code: str,
    req: GuessRequest,
    x_player_id: str = Header(...),
):
    """The imposter (and only the imposter) may submit a guess at the
    normal content during the results phase for a bonus."""
    room = _require_room(code)
    _require_player(room, x_player_id)
    correct = _handle(game_logic.submit_imposter_guess, room, x_player_id, req.guess)
    await manager.broadcast(room)
    return {"ok": True, "correct": correct}


@app.post("/api/rooms/{code}/show-scoreboard")
async def show_scoreboard_endpoint(
    code: str,
    x_player_id: str = Header(...),
):
    """Host action: results -> scoreboard."""
    room = _require_room(code)
    _require_host(room, x_player_id)
    _handle(game_logic.show_scoreboard, room)
    await manager.broadcast(room)
    return {"ok": True}


@app.patch("/api/rooms/{code}/settings")
async def update_settings_endpoint(
    code: str,
    req: SettingsRequest,
    x_player_id: str = Header(...),
):
    """Host can tweak room settings from the lobby (category, rounds, timers…)."""
    room = _require_room(code)
    _require_host(room, x_player_id)
    # Drop None-valued keys — they mean "leave this setting alone". Category
    # is an exception: explicit None / "any" clears the filter, which
    # `game_logic.update_settings` handles itself.
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if req.category is not None:
        patch["category"] = req.category
    _handle(game_logic.update_settings, room, patch)
    await manager.broadcast(room)
    return {"ok": True, "settings": game_logic._serialize_settings(room.settings)}


@app.post("/api/rooms/{code}/reset")
async def reset_room_endpoint(
    code: str,
    x_player_id: str = Header(...),
):
    room = _require_room(code)
    _require_host(room, x_player_id)
    game_logic.reset_game(room)
    _cancel_timer(room)
    await manager.broadcast(room)
    return {"ok": True}


# ============================================================
# WebSocket (state push)
# ============================================================

@app.websocket("/ws/{code}/{player_id}")
async def ws_endpoint(ws: WebSocket, code: str, player_id: str):
    room = get_room(code)
    if not room or player_id not in room.players:
        await ws.close(code=4404)
        return

    await manager.connect(ws, code, player_id)
    room.players[player_id].connected = True

    await manager.broadcast(room)

    try:
        while True:
            # Client messages are ignored; actions go through REST. Reading
            # keeps the coroutine alive so WebSocketDisconnect fires on close.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws)
        game_logic.remove_player(room, player_id)
        if not room.players:
            delete_room_if_empty(room)
        else:
            await manager.broadcast(room)


# ============================================================
# Local dev
# ============================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
