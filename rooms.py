"""
In-memory room registry.

The registry is intentionally dumb — it stores Room objects and looks them
up by code. All game rules live in `game_logic.py`; everything here is
infrastructure that would be swapped for Redis (or any shared store) when
the app needs to scale across processes.
"""

from __future__ import annotations

from typing import Dict, Optional

import game_logic
from game_logic import Player, Room


ROOMS: Dict[str, Room] = {}


# ---- Create / join ----

def create_room(host_name: str) -> Room:
    """Create a room with unique code (vs. currently-registered rooms)."""
    room = game_logic.create_room(host_name, existing_codes=set(ROOMS.keys()))
    ROOMS[room.code] = room
    return room


def join_room(code: str, name: str) -> tuple[Room, Player]:
    """Resolve the room by code and add the player via `game_logic.add_player`."""
    room = get_room(code)
    if not room:
        raise LookupError("Room not found")
    player = game_logic.add_player(room, name)
    return room, player


# ---- Lookup / cleanup ----

def get_room(code: str) -> Optional[Room]:
    """Case-insensitive lookup. None if no such room."""
    return ROOMS.get(code.upper())


def delete_room_if_empty(room: Room) -> None:
    """Drop the room from the registry once the last player disconnects."""
    if not room.players:
        ROOMS.pop(room.code, None)
