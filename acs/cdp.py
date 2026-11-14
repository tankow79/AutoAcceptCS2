"""Minimal Chrome DevTools Protocol client (stdlib only) for Steam's embedded browser on localhost.

Steam exposes its UI pages on http://127.0.0.1:8080 when a `.cef-enable-remote-debugging` file exists in the Steam
folder. We only ever talk to 127.0.0.1."""
from __future__ import annotations

import base64
import hashlib
import http.client
from collections import deque
import json
import os
import socket
import struct
import time
from typing import Any, Optional

HOST, PORT = "127.0.0.1", 8080
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class CdpError(Exception):
    pass


def list_pages(timeout: float = 2.0) -> list[dict]:
    """The pages Steam's browser has open ([] when the debug port is off)."""
    conn = http.client.HTTPConnection(HOST, PORT, timeout=timeout)  # direct: no system proxy, no redirects
    try:
        conn.request("GET", "/json")
        r = conn.getresponse()
        if r.status != 200:
            return []
        data = json.loads(r.read(4_000_000).decode("utf-8", "ignore"))
        return [p for p in data if isinstance(p, dict)] if isinstance(data, list) else []
    except (OSError, ValueError, http.client.HTTPException):
        return []
    finally:
        conn.close()


class WebSocket:
    def __init__(self, url: str, timeout: float = 5.0):
        if not url.startswith("ws://"):
            raise CdpError("refusing a non-ws url")
        hostport, _, path = url[len("ws://"):].partition("/")
        host, _, port = hostport.partition(":")
        if host not in (HOST, "localhost") or (port and not port.isdigit()):  # exact match: "127.0.0.1.evil.com" is not local
            raise CdpError("refusing a non-local websocket")
        try:
            self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        except OSError as e:
            raise CdpError(f"cannot connect: {type(e).__name__}") from e
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CdpError("handshake closed")
            head += chunk
            if len(head) > 16384:
                raise CdpError("handshake too long")
        first = head.split(b"\r\n", 1)[0]
        want = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest())  # noqa: S324 - protocol requirement
        if b" 101 " not in first or want not in head:
            raise CdpError("handshake refused")
        self._buf = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CdpError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send(self, text: str) -> None:
        data = text.encode()
        n = len(data)
        head = bytes([0x81])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> Optional[str]:
        """Next text message (None on close). Raises socket.timeout when nothing arrives in time."""
        msg = b""
        while True:
            b1, b2 = self._read(2)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            if n > 64_000_000:
                raise CdpError("frame too large")
            payload = self._read(n)  # server frames are unmasked
            if op == 0x8:
                return None
            if op == 0x9:  # ping
                self.sock.sendall(bytes([0x8A, 0x80 | len(payload[:125])]) + b"\0\0\0\0" + payload[:125])
                continue
            if op in (0x1, 0x0):
                msg += payload
                if b1 & 0x80:
                    return msg.decode("utf-8", "ignore")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Page:
    """One Steam browser page: evaluate JavaScript in it and collect events."""

    def __init__(self, ws_url: str, timeout: float = 5.0):
        self.ws = WebSocket(ws_url, timeout)
        self._id = 0
        self.events: deque = deque(maxlen=200)  # bounded: nothing reads it today

    def call(self, method: str, params: Optional[dict] = None, timeout: float = 8.0) -> dict:
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        end = time.monotonic() + timeout
        self.ws.sock.settimeout(timeout)
        while time.monotonic() < end:
            raw = self.ws.recv()
            if raw is None:
                raise CdpError("closed")
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CdpError(str(msg["error"])[:200])
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)
        raise CdpError("timeout")

    def evaluate(self, expression: str, timeout: float = 8.0) -> Any:
        """Run JS and return its JSON-serialisable value."""
        res = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True},
                        timeout)
        if "exceptionDetails" in res:
            raise CdpError(str(res["exceptionDetails"].get("text", "js error"))[:200])
        return res.get("result", {}).get("value")

    def close(self) -> None:
        self.ws.close()
