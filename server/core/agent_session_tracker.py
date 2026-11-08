# -*- coding: utf-8 -*-
"""
Agent Session Tracker & Governance.
Tracks files touched by AI agents, manages idle debouncing (6s),
and notifies the Bridge Server / IDE plugin when the agent becomes idle.
"""

import os
import sys
import json
import time
import threading
import asyncio
from pathlib import Path
from typing import Set, List, Optional, Dict, Any

from core.project_root import get_project_root


class AgentSessionTracker:
    """Manages active AI agent modification sessions with 6-second idle debouncing."""
    
    IDLE_TIMEOUT_SECONDS = 6.0

    def __init__(self):
        self._lock = threading.Lock()
        self._touched_files: Set[str] = set()
        self._session_id: str = f"sess_{int(time.time())}"
        self._idle_timer: Optional[threading.Timer] = None
        self._last_activity_time: float = 0.0
        self._is_active: bool = False

    def record_touched_file(self, file_path: str, project_root: Optional[Path] = None) -> None:
        """
        Records a modified file path into the current agent session
        and restarts the 6-second idle debounce timer.
        """
        root = project_root or get_project_root()
        try:
            p = Path(file_path)
            if p.is_absolute():
                rel_path = str(p.relative_to(root)).replace("\\", "/")
            else:
                rel_path = str(file_path).replace("\\", "/").lstrip("/")
        except Exception:
            rel_path = str(file_path).replace("\\", "/").lstrip("/")

        with self._lock:
            self._touched_files.add(rel_path)
            self._last_activity_time = time.time()
            self._is_active = True
            
            # Save session snapshot to disk for persistence
            self._save_session_file(root, status="active")

            # Cancel previous debounce timer and restart
            if self._idle_timer is not None:
                self._idle_timer.cancel()

            self._idle_timer = threading.Timer(
                self.IDLE_TIMEOUT_SECONDS,
                self._on_idle_timeout,
                args=[root]
            )
            self._idle_timer.daemon = True
            self._idle_timer.start()

    def _save_session_file(self, root: Path, status: str = "active") -> None:
        """Persists agent session metadata to .composebridge-state/agent_session.json."""
        try:
            state_dir = root / ".composebridge-state"
            state_dir.mkdir(parents=True, exist_ok=True)
            session_file = state_dir / "agent_session.json"
            
            data = {
                "session_id": self._session_id,
                "status": status,
                "last_activity": self._last_activity_time,
                "touched_files": sorted(list(self._touched_files)),
            }
            session_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[AgentSessionTracker] Failed to save session: {e}", file=sys.stderr)

    def _on_idle_timeout(self, root: Path) -> None:
        """Called when agent stops editing for IDLE_TIMEOUT_SECONDS."""
        with self._lock:
            files_list = sorted(list(self._touched_files))
            sess_id = self._session_id
            self._is_active = False
            self._save_session_file(root, status="idle")

        print(f"[AgentSessionTracker] 🤖 Agent Idle detected: {len(files_list)} file(s) touched.", file=sys.stderr)
        
        # Broadcast agent_idle event to Bridge Server / IDE plugin
        self._broadcast_idle_event(sess_id, files_list)

    def _broadcast_idle_event(self, session_id: str, touched_files: List[str]) -> None:
        """Sends agent_idle payload over WebSocket to ws://127.0.0.1:8711."""
        async def _send():
            try:
                import websockets
                from core.auth_manager import get_or_create_auth_token
                
                async with websockets.connect("ws://127.0.0.1:8711", open_timeout=2) as ws:
                    # Authenticate
                    root = get_project_root()
                    token = get_or_create_auth_token(root)
                    await ws.send(json.dumps({"type": "auth", "token": token}))
                    await asyncio.wait_for(ws.recv(), timeout=2)

                    # Send agent_idle
                    await ws.send(json.dumps({
                        "type": "agent_idle",
                        "session_id": session_id,
                        "touched_files": touched_files,
                    }))
                    await asyncio.wait_for(ws.recv(), timeout=2)
            except Exception as e:
                print(f"[AgentSessionTracker] Broadcast idle failed (Bridge may be offline): {e}", file=sys.stderr)

        # Run in separate thread/event loop
        try:
            threading.Thread(target=lambda: asyncio.run(_send()), daemon=True).start()
        except Exception as e:
            print(f"[AgentSessionTracker] Thread dispatch failed: {e}", file=sys.stderr)

    def get_touched_files(self) -> List[str]:
        with self._lock:
            return sorted(list(self._touched_files))

    def clear_session(self) -> None:
        with self._lock:
            if self._idle_timer:
                self._idle_timer.cancel()
                self._idle_timer = None
            self._touched_files.clear()
            self._session_id = f"sess_{int(time.time())}"
            self._is_active = False


# Global singleton instance
SESSION_TRACKER = AgentSessionTracker()

