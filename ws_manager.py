"""
WebSocket connection manager.

The WebSocket is a one-way push channel: server -> client. Game actions
go through REST in `app.py`. Here we just track which sockets belong to
which room/player and broadcast state snapshots tailored per-socket so
each player receives only their own private content.
"""

from __future__ import annotations

from typing import Dict, Optional, Set, Tuple

from fastapi import WebSocket

import game_logic
from game_logic import Room


class ConnectionManager:
    def __init__(self) -> None:
        self._by_room: Dict[str, Set[WebSocket]] = {}
        self._owner: Dict[WebSocket, Tuple[str, str]] = {}

    async def connect(self, ws: WebSocket, room_code: str, player_id: str) -> None:
        await ws.accept()
        self._by_room.setdefault(room_code, set()).add(ws)
        self._owner[ws] = (room_code, player_id)

    def disconnect(self, ws: WebSocket) -> Optional[Tuple[str, str]]:
        owner = self._owner.pop(ws, None)
        if owner:
            code, _ = owner
            self._by_room.get(code, set()).discard(ws)
        return owner

    async def broadcast(self, room: Room) -> None:
        """Send the current state to every socket in the room, tailored per player."""
        for ws in list(self._by_room.get(room.code, set())):
            owner = self._owner.get(ws)
            pid = owner[1] if owner else None
            try:
                await ws.send_json({
                    "type": "state",
                    "state": game_logic.serialize_room(room, for_player_id=pid),
                })
            except Exception:
                # The socket is dead; FastAPI will fire WebSocketDisconnect
                # on the endpoint side and we'll clean up there.
                pass
