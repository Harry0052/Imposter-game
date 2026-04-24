"""
Imposter Media — MVP.

Single-file FastAPI backend. Actions go over one WebSocket per player.
Host advances each phase manually (no timers). In-memory state only.

Run:  python app.py   (or: uvicorn app:app --reload)
Open: http://localhost:8000
"""

import json
import os
import random
import string
from dataclasses import dataclass, field
from typing import Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

app = FastAPI()

# Exactly the four sample pairs from the spec.
CONTENT = [
    ("LeBron James dunking",        "Stephen Curry shooting threes"),
    ("MrBeast giving away money",   "Kai Cenat livestreaming"),
    ("A cat knocking over a cup",   "A dog stealing food"),
    ("Drake performing on stage",   "The Weeknd performing on stage"),
]

MIN_PLAYERS = 3
MAX_PLAYERS = 10


# ----- Data -----

@dataclass
class Player:
    id: str
    name: str
    ws: WebSocket
    content: Optional[str] = None
    word: Optional[str] = None
    vote: Optional[str] = None        # player id they voted for
    is_imposter: bool = False


@dataclass
class Room:
    code: str
    phase: str = "lobby"              # lobby | viewing | word | reveal | voting | result
    host_id: str = ""
    players: Dict[str, Player] = field(default_factory=dict)
    imposter_id: Optional[str] = None
    normal_prompt: Optional[str] = None
    imposter_prompt: Optional[str] = None
    last_result: Optional[dict] = None


rooms: Dict[str, Room] = {}


def new_code() -> str:
    while True:
        c = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=4))
        if c not in rooms:
            return c


def new_player_id() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=12))


# ----- Game transitions -----

def start_round(room: Room) -> None:
    """Pick a random imposter + content pair and move to `viewing`."""
    if len(room.players) < MIN_PLAYERS:
        return
    imposter_id = random.choice(list(room.players))
    normal, imposter = random.choice(CONTENT)

    room.imposter_id = imposter_id
    room.normal_prompt = normal
    room.imposter_prompt = imposter
    room.last_result = None
    room.phase = "viewing"
    for p in room.players.values():
        p.is_imposter = (p.id == imposter_id)
        p.content = imposter if p.is_imposter else normal
        p.word = None
        p.vote = None


def tally(room: Room) -> None:
    """Count votes and decide the winner. Ties => imposter wins."""
    counts: Dict[str, int] = {}
    for p in room.players.values():
        if p.vote:
            counts[p.vote] = counts.get(p.vote, 0) + 1
    top = max(counts.values()) if counts else 0
    top_ids = [pid for pid, c in counts.items() if c == top]

    group_won = len(top_ids) == 1 and top_ids[0] == room.imposter_id
    room.last_result = {
        "imposter_name": room.players[room.imposter_id].name,
        "normal": room.normal_prompt,
        "imposter_content": room.imposter_prompt,
        "votes": {room.players[pid].name: c for pid, c in counts.items()},
        "group_won": group_won,
        "tied": len(top_ids) > 1,
    }
    room.phase = "result"


def reset_room(room: Room) -> None:
    room.phase = "lobby"
    room.imposter_id = None
    room.normal_prompt = None
    room.imposter_prompt = None
    room.last_result = None
    for p in room.players.values():
        p.content = None
        p.word = None
        p.vote = None
        p.is_imposter = False


# ----- State serialization -----

def snapshot(room: Room, viewer_id: str) -> Optional[dict]:
    me = room.players.get(viewer_id)
    if not me:
        return None

    public = []
    for p in room.players.values():
        row = {
            "id": p.id,
            "name": p.name,
            "is_host": p.id == room.host_id,
            "has_word": bool(p.word),
            "has_voted": bool(p.vote),
        }
        # Words are public once we hit the reveal phase.
        if room.phase in ("reveal", "voting", "result"):
            row["word"] = p.word
        public.append(row)

    return {
        "code": room.code,
        "phase": room.phase,
        "host_id": room.host_id,
        "players": public,
        "me": {
            "id": me.id,
            "name": me.name,
            "is_host": me.id == room.host_id,
            # Private content is sent only to its owner and only during `viewing`.
            "content": me.content if room.phase == "viewing" else None,
            "word": me.word,
            "vote": me.vote,
        },
        "last_result": room.last_result,
    }


async def broadcast(room: Room) -> None:
    """Push current state to every player, with their own view of `me`."""
    dead = []
    for p in list(room.players.values()):
        snap = snapshot(room, p.id)
        try:
            await p.ws.send_json({"type": "state", "state": snap})
        except Exception:
            dead.append(p.id)
    for pid in dead:
        room.players.pop(pid, None)


# ----- HTTP -----

HERE = os.path.dirname(os.path.abspath(__file__))


@app.get("/")
async def index():
    return FileResponse(os.path.join(HERE, "index.html"))


@app.post("/api/room")
async def create_room_api():
    """Reserve a fresh room. The creator still has to connect via WS to join."""
    code = new_code()
    rooms[code] = Room(code=code)
    return {"code": code}


# ----- WebSocket -----

@app.websocket("/ws/{code}/{name}")
async def ws_endpoint(ws: WebSocket, code: str, name: str):
    code = code.upper()
    room = rooms.get(code)

    await ws.accept()

    if not room:
        await ws.send_json({"type": "error", "message": "Room not found"})
        await ws.close()
        return
    if len(room.players) >= MAX_PLAYERS:
        await ws.send_json({"type": "error", "message": "Room is full"})
        await ws.close()
        return
    if room.phase != "lobby":
        await ws.send_json({"type": "error", "message": "Game already started"})
        await ws.close()
        return

    # Add the player; first player becomes host.
    pid = new_player_id()
    me = Player(id=pid, name=(name.strip() or "Player")[:20], ws=ws)
    if not room.players:
        room.host_id = pid
    room.players[pid] = me

    await broadcast(room)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            action = msg.get("action")
            is_host = pid == room.host_id

            # ----- Host actions -----
            if action == "start" and is_host and room.phase == "lobby":
                start_round(room)

            elif action == "done_viewing" and is_host and room.phase == "viewing":
                # Hide everyone's private content and ask for words.
                for p in room.players.values():
                    p.content = None
                room.phase = "word"

            elif action == "start_vote" and is_host and room.phase == "reveal":
                room.phase = "voting"

            elif action == "next" and is_host and room.phase == "result":
                start_round(room)

            elif action == "reset" and is_host:
                reset_room(room)

            # ----- Player actions -----
            elif action == "word" and room.phase == "word" and not me.word:
                tokens = str(msg.get("word", "")).strip().split()
                if tokens:
                    me.word = tokens[0][:30].lower()
                    # Auto-advance when everyone's in.
                    if all(p.word for p in room.players.values()):
                        room.phase = "reveal"

            elif action == "vote" and room.phase == "voting" and not me.vote:
                target = msg.get("target")
                if (target and target != pid
                        and target in room.players):
                    me.vote = target
                    if all(p.vote for p in room.players.values()):
                        tally(room)

            await broadcast(room)

    except WebSocketDisconnect:
        pass
    finally:
        room.players.pop(pid, None)
        if not room.players:
            # Last player left — drop the room.
            rooms.pop(code, None)
        else:
            # If the host left, promote someone else so the room still works.
            if pid == room.host_id:
                room.host_id = next(iter(room.players))
            await broadcast(room)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
