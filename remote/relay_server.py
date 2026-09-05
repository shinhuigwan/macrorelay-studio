"""MacroRelay Remote relay and PWA host.

Runs with the Python standard library only. For internet use, place it behind
an HTTPS reverse proxy; the PC agent and mobile client both initiate outbound
HTTP requests, so the controlled PC never needs an inbound port.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import secrets
import sqlite3
import threading
import time
import urllib.parse
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "mobile"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class RelayStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _db(self):
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    secret_hash TEXT NOT NULL,
                    name TEXT NOT NULL,
                    pairing_code TEXT NOT NULL,
                    pair_expires REAL NOT NULL,
                    app_token_hash TEXT NOT NULL DEFAULT '',
                    last_seen REAL NOT NULL DEFAULT 0,
                    state_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created REAL NOT NULL,
                    expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_commands_device ON commands(device_id, status, id);
                CREATE INDEX IF NOT EXISTS idx_events_device ON events(device_id, id);
                """
            )

    def register(self, device_id: str, secret: str, name: str) -> dict[str, Any]:
        now = time.time()
        with self.lock, self._db() as db:
            row = db.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
            if row and not secrets.compare_digest(row["secret_hash"], _hash(secret)):
                raise PermissionError("device secret mismatch")
            if row:
                code = row["pairing_code"]
                expires = float(row["pair_expires"])
                if not row["app_token_hash"] and expires < now:
                    code = f"{secrets.randbelow(1_000_000):06d}"
                    expires = now + 600
                db.execute(
                    "UPDATE devices SET name=?, pairing_code=?, pair_expires=?, last_seen=? WHERE device_id=?",
                    (name, code, expires, now, device_id),
                )
                paired = bool(row["app_token_hash"])
            else:
                code = f"{secrets.randbelow(1_000_000):06d}"
                expires = now + 600
                db.execute(
                    "INSERT INTO devices(device_id,secret_hash,name,pairing_code,pair_expires,last_seen) VALUES(?,?,?,?,?,?)",
                    (device_id, _hash(secret), name, code, expires, now),
                )
                paired = False
            return {"pairing_code": code, "pair_expires": expires, "paired": paired}

    def verify_agent(self, device_id: str, secret: str) -> bool:
        if not device_id or not secret:
            return False
        with self._db() as db:
            row = db.execute("SELECT secret_hash FROM devices WHERE device_id=?", (device_id,)).fetchone()
        return bool(row and secrets.compare_digest(row["secret_hash"], _hash(secret)))

    def pair(self, code: str) -> dict[str, Any] | None:
        now = time.time()
        token = secrets.token_urlsafe(32)
        with self.lock, self._db() as db:
            row = db.execute(
                "SELECT device_id,name FROM devices WHERE pairing_code=? AND pair_expires>=?",
                (code.strip(), now),
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE devices SET app_token_hash=?, pairing_code='', pair_expires=0 WHERE device_id=?",
                (_hash(token), row["device_id"]),
            )
            return {"device_id": row["device_id"], "device_name": row["name"], "token": token}

    def verify_app(self, device_id: str, token: str) -> bool:
        if not device_id or not token:
            return False
        with self._db() as db:
            row = db.execute("SELECT app_token_hash FROM devices WHERE device_id=?", (device_id,)).fetchone()
        return bool(row and row["app_token_hash"] and secrets.compare_digest(row["app_token_hash"], _hash(token)))

    def update_status(self, device_id: str, state: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE devices SET state_json=?,last_seen=? WHERE device_id=?",
                (json.dumps(state, ensure_ascii=False), time.time(), device_id),
            )

    def device(self, device_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT name,last_seen,state_json FROM devices WHERE device_id=?", (device_id,)).fetchone()
        if not row:
            return None
        try:
            state = json.loads(row["state_json"] or "{}")
        except ValueError:
            state = {}
        last_seen = float(row["last_seen"] or 0)
        return {"device_id": device_id, "name": row["name"], "online": time.time() - last_seen < 20, "last_seen": last_seen, "state": state}

    def add_command(self, device_id: str, action: str, payload: dict[str, Any]) -> int:
        now = time.time()
        with self.changed, self._db() as db:
            cursor = db.execute(
                "INSERT INTO commands(device_id,action,payload_json,created,expires) VALUES(?,?,?,?,?)",
                (device_id, action, json.dumps(payload, ensure_ascii=False), now, now + 300),
            )
            command_id = int(cursor.lastrowid)
            self.changed.notify_all()
            return command_id

    def poll_commands(self, device_id: str, timeout: float) -> list[dict[str, Any]]:
        deadline = time.monotonic() + max(0.0, min(timeout, 25.0))
        with self.changed:
            while True:
                now = time.time()
                with self._db() as db:
                    db.execute("UPDATE commands SET status='expired' WHERE device_id=? AND status='queued' AND expires<?", (device_id, now))
                    rows = db.execute(
                        "SELECT id,action,payload_json,created FROM commands WHERE device_id=? AND status='queued' ORDER BY id LIMIT 10",
                        (device_id,),
                    ).fetchall()
                    if rows:
                        ids = [int(row["id"]) for row in rows]
                        db.executemany("UPDATE commands SET status='delivered' WHERE id=?", ((item,) for item in ids))
                        return [
                            {"id": int(row["id"]), "action": row["action"], "payload": json.loads(row["payload_json"]), "created": row["created"]}
                            for row in rows
                        ]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self.changed.wait(remaining)

    def command_result(self, device_id: str, command_id: int, result: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE commands SET status='done',result_json=? WHERE id=? AND device_id=?",
                (json.dumps(result, ensure_ascii=False), command_id, device_id),
            )

    def add_event(self, device_id: str, event_type: str, message: str, payload: dict[str, Any]) -> int:
        with self._db() as db:
            cursor = db.execute(
                "INSERT INTO events(device_id,type,message,payload_json,created) VALUES(?,?,?,?,?)",
                (device_id, event_type, message[:2000], json.dumps(payload, ensure_ascii=False), time.time()),
            )
            return int(cursor.lastrowid)

    def events(self, device_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id,type,message,payload_json,created FROM events WHERE device_id=? AND id>? ORDER BY id DESC LIMIT 100",
                (device_id, max(after, 0)),
            ).fetchall()
        return [
            {"id": int(row["id"]), "type": row["type"], "message": row["message"], "payload": json.loads(row["payload_json"]), "created": row["created"]}
            for row in rows
        ]


class RelayHandler(BaseHTTPRequestHandler):
    server_version = "MacroRelayRemote/1.0"
    _pair_attempts: dict[str, list[float]] = {}
    _pair_lock = threading.Lock()

    @property
    def store(self) -> RelayStore:
        return self.server.store  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _json_body(self) -> dict[str, Any]:
        length = min(int(self.headers.get("Content-Length", "0") or 0), 2_000_000)
        if length <= 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _agent(self) -> str | None:
        device_id = self.headers.get("X-MacroRelay-Device", "")
        secret = self.headers.get("X-MacroRelay-Secret", "")
        return device_id if self.store.verify_agent(device_id, secret) else None

    def _app(self, device_id: str) -> bool:
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return self.store.verify_app(device_id, token)

    def _pair_rate_limited(self) -> bool:
        address = str(self.client_address[0])
        now = time.monotonic()
        with self._pair_lock:
            recent = [stamp for stamp in self._pair_attempts.get(address, []) if now - stamp < 300]
            self._pair_attempts[address] = recent
            return len(recent) >= 10

    def _record_pair_failure(self) -> None:
        address = str(self.client_address[0])
        with self._pair_lock:
            self._pair_attempts.setdefault(address, []).append(time.monotonic())

    def _clear_pair_failures(self) -> None:
        with self._pair_lock:
            self._pair_attempts.pop(str(self.client_address[0]), None)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path
        if path == "/health":
            self._send_json(200, {"ok": True, "service": "MacroRelay Remote"})
            return
        if path == "/api/agent/commands":
            device_id = self._agent()
            if not device_id:
                self._send_json(401, {"ok": False, "error": "agent_unauthorized"})
                return
            timeout = float(query.get("timeout", ["20"])[0] or 20)
            self._send_json(200, {"ok": True, "commands": self.store.poll_commands(device_id, timeout)})
            return
        if path.startswith("/api/devices/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) < 3:
                self._send_json(404, {"ok": False})
                return
            device_id = parts[2]
            if not self._app(device_id):
                self._send_json(401, {"ok": False, "error": "app_unauthorized"})
                return
            if len(parts) == 3:
                device = self.store.device(device_id)
                self._send_json(200 if device else 404, {"ok": bool(device), "device": device})
                return
            if len(parts) == 4 and parts[3] == "events":
                after = int(query.get("after", ["0"])[0] or 0)
                self._send_json(200, {"ok": True, "events": self.store.events(device_id, after)})
                return
        self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        body = self._json_body()
        if path == "/api/agent/register":
            if not body.get("device_id") or not body.get("device_secret"):
                self._send_json(400, {"ok": False, "error": "missing_device_credentials"})
                return
            try:
                result = self.store.register(str(body.get("device_id") or ""), str(body.get("device_secret") or ""), str(body.get("device_name") or "MacroRelay PC"))
            except PermissionError:
                self._send_json(401, {"ok": False, "error": "device_secret_mismatch"})
                return
            self._send_json(200, {"ok": True, **result})
            return
        if path == "/api/pair":
            if self._pair_rate_limited():
                self._send_json(429, {"ok": False, "error": "pairing_rate_limited"})
                return
            result = self.store.pair(str(body.get("code") or ""))
            if result:
                self._clear_pair_failures()
            else:
                self._record_pair_failure()
            self._send_json(200 if result else 404, {"ok": bool(result), **(result or {"error": "pairing_code_invalid"})})
            return
        if path in {"/api/agent/status", "/api/agent/events"} or path.startswith("/api/agent/commands/"):
            device_id = self._agent()
            if not device_id:
                self._send_json(401, {"ok": False, "error": "agent_unauthorized"})
                return
            if path == "/api/agent/status":
                self.store.update_status(device_id, body)
            elif path == "/api/agent/events":
                self.store.add_event(device_id, str(body.get("type") or "info"), str(body.get("message") or ""), body.get("payload") if isinstance(body.get("payload"), dict) else {})
            else:
                try:
                    command_id = int(path.rsplit("/", 1)[1])
                except ValueError:
                    self._send_json(400, {"ok": False})
                    return
                self.store.command_result(device_id, command_id, body)
            self._send_json(200, {"ok": True})
            return
        if path.startswith("/api/devices/") and path.endswith("/commands"):
            parts = [part for part in path.split("/") if part]
            device_id = parts[2] if len(parts) >= 4 else ""
            if not self._app(device_id):
                self._send_json(401, {"ok": False, "error": "app_unauthorized"})
                return
            action = str(body.get("action") or "")
            if action not in {"status", "list_macros", "run_macro", "stop_macro"}:
                self._send_json(400, {"ok": False, "error": "unsupported_command"})
                return
            command_id = self.store.add_command(device_id, action, body.get("payload") if isinstance(body.get("payload"), dict) else {})
            self._send_json(202, {"ok": True, "command_id": command_id})
            return
        self._send_json(404, {"ok": False, "error": "not_found"})

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not target.is_file():
            target = WEB_ROOT / "index.html"
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type == "application/javascript" else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if target.name in {"index.html", "service-worker.js"} else "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)


def create_server(host: str, port: int, database: Path) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), RelayHandler)
    server.daemon_threads = True
    server.store = RelayStore(database)  # type: ignore[attr-defined]
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="MacroRelay Remote relay server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path, default=ROOT / "relay.db")
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.database.resolve())
    print(f"MacroRelay Remote: http://{args.host}:{args.port}")
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
