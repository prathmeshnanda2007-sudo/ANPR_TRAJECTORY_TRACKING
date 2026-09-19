"""
WebSocket connection manager for broadcasting real-time events to connected clients.
Supports separate channels for live feed events and alert notifications.
"""

import asyncio
import json
import datetime
from typing import Dict, Set
from fastapi import WebSocket


class ConnectionManager:
    """Manages WebSocket connections across multiple channels."""

    def __init__(self):
        # channel_name -> set of WebSocket connections
        self._channels: Dict[str, Set[WebSocket]] = {
            "live-feed": set(),
            "alerts": set(),
            "live": set(),
        }
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, channel: str = "live-feed"):
        """Accept and register a WebSocket connection to a channel."""
        await websocket.accept()
        async with self._lock:
            if channel not in self._channels:
                self._channels[channel] = set()
            self._channels[channel].add(websocket)

    async def disconnect(self, websocket: WebSocket, channel: str = "live-feed"):
        """Remove a WebSocket connection from a channel."""
        async with self._lock:
            self._channels.get(channel, set()).discard(websocket)

    async def broadcast(self, channel: str, data: dict):
        """Broadcast a JSON message to all connections on a channel."""
        message = json.dumps(data, default=_json_serializer)
        async with self._lock:
            connections = list(self._channels.get(channel, set()))

        stale = []
        for ws in connections:
            try:
                await ws.send_text(message)
            except Exception:
                stale.append(ws)

        # Clean up broken connections
        if stale:
            async with self._lock:
                for ws in stale:
                    self._channels.get(channel, set()).discard(ws)

    async def broadcast_event(self, event_data: dict):
        """Broadcast a plate event to the live-feed and unified live channels."""
        msg = {
            "type": "detection",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "data": event_data,
        }
        await self.broadcast("live-feed", msg)
        await self.broadcast("live", msg)

    async def broadcast_alert(self, alert_data: dict):
        """Broadcast an alert to the alerts and unified live channels."""
        msg = {
            "type": "alert",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "data": alert_data,
        }
        await self.broadcast("alerts", msg)
        await self.broadcast("live", msg)

    async def broadcast_reid_upgrade(self, reid_data: dict):
        """Broadcast a vehicle provisional Re-ID upgrade event."""
        msg = {
            "type": "provisional_upgraded",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "data": reid_data,
        }
        await self.broadcast("live-feed", msg)
        await self.broadcast("live", msg)
        await self.broadcast("alerts", msg)

    def get_connection_count(self, channel: str = None) -> int:
        """Returns the number of active connections, optionally filtered by channel."""
        if channel:
            return len(self._channels.get(channel, set()))
        return sum(len(conns) for conns in self._channels.values())


def _json_serializer(obj):
    """Custom JSON serializer for datetime objects."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# Singleton instance
ws_manager = ConnectionManager()
