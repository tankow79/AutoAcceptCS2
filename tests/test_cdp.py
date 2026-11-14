"""The DevTools client against a fake local websocket server (framing, fragmentation, ping, errors, safety)."""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time

import pytest

from acs import cdp

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _recv_exact(c: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = c.recv(n - len(buf))
        if not chunk:
            raise ConnectionError
        buf += chunk
    return buf


def read_frame(c: socket.socket) -> tuple[int, bytes]:
    b1, b2 = _recv_exact(c, 2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", _recv_exact(c, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _recv_exact(c, 8))[0]
    mask = _recv_exact(c, 4) if b2 & 0x80 else b""
    data = _recv_exact(c, n)
    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return b1 & 0x0F, data


def frame(op: int, payload: bytes, fin: bool = True) -> bytes:
    head = bytes([(0x80 if fin else 0) | op])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload  # server frames are never masked


class FakeSteam:
    """Accepts one websocket client; `handler(conn, request_dict)` answers each JSON message."""

    def __init__(self, handler, bad_accept: bool = False):
        self.handler, self.bad_accept = handler, bad_accept
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.received: list[dict] = []
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/devtools/page/X"

    def _serve(self) -> None:
        try:
            c, _ = self.srv.accept()
            req = b""
            while b"\r\n\r\n" not in req:
                req += c.recv(4096)
            key = next(ln.split(b": ")[1].decode() for ln in req.split(b"\r\n") if ln.lower().startswith(b"sec-websocket-key"))
            acc = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            if self.bad_accept:
                acc = "wrong"
            c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Accept: {acc}\r\n\r\n").encode())
            while True:
                op, data = read_frame(c)
                if op == 8:
                    c.sendall(frame(8, b""))
                    return
                if op == 1:
                    msg = json.loads(data)
                    self.received.append(msg)
                    self.handler(c, msg)
        except (ConnectionError, OSError):
            pass


def reply(c, msg, result):
    c.sendall(frame(1, json.dumps({"id": msg["id"], "result": result}).encode()))


def test_evaluate_returns_the_value():
    s = FakeSteam(lambda c, m: reply(c, m, {"result": {"type": "number", "value": 42}}))
    p = cdp.Page(s.url)
    assert p.evaluate("6*7") == 42
    assert s.received[0]["method"] == "Runtime.evaluate" and s.received[0]["params"]["returnByValue"] is True
    p.close()


def test_events_before_the_reply_are_collected_and_ids_are_matched():
    def h(c, m):
        c.sendall(frame(1, json.dumps({"method": "Some.event", "params": {}}).encode()))
        c.sendall(frame(1, json.dumps({"id": 999, "result": {"result": {"value": "stray"}}}).encode()))  # someone else's id
        reply(c, m, {"result": {"value": "mine"}})

    s = FakeSteam(h)
    p = cdp.Page(s.url)
    assert p.evaluate("x") == "mine"
    assert p.events and p.events[0]["method"] == "Some.event"


def test_ping_frames_are_answered_and_do_not_break_the_reply():
    def h(c, m):
        c.sendall(frame(9, b"hi"))
        reply(c, m, {"result": {"value": 1}})

    s = FakeSteam(h)
    assert cdp.Page(s.url).evaluate("x") == 1


@pytest.mark.parametrize("size", [10, 125, 126, 65535, 65536, 300_000])
def test_replies_of_every_length_class_are_read_correctly(size):
    big = "a" * size
    s = FakeSteam(lambda c, m: reply(c, m, {"result": {"value": big}}))
    assert cdp.Page(s.url).evaluate("x") == big


def test_fragmented_messages_are_reassembled():
    def h(c, m):
        data = json.dumps({"id": m["id"], "result": {"result": {"value": "joined together"}}}).encode()
        third = len(data) // 3
        c.sendall(frame(1, data[:third], fin=False))
        c.sendall(frame(9, b""))  # a control frame may be interleaved
        c.sendall(frame(0, data[third:2 * third], fin=False))
        c.sendall(frame(0, data[2 * third:], fin=True))

    s = FakeSteam(h)
    assert cdp.Page(s.url).evaluate("x") == "joined together"


def test_a_reply_that_arrives_in_tiny_pieces_is_assembled():
    def h(c, m):
        data = frame(1, json.dumps({"id": m["id"], "result": {"result": {"value": "slow"}}}).encode())
        for i in range(len(data)):
            c.sendall(data[i:i + 1])
            time.sleep(0.001)

    s = FakeSteam(h)
    assert cdp.Page(s.url).evaluate("x") == "slow"


def test_javascript_exceptions_become_cdp_errors():
    s = FakeSteam(lambda c, m: reply(c, m, {"exceptionDetails": {"text": "boom"}}))
    with pytest.raises(cdp.CdpError):
        cdp.Page(s.url).evaluate("throw 1")


def test_protocol_errors_become_cdp_errors():
    s = FakeSteam(lambda c, m: c.sendall(frame(1, json.dumps({"id": m["id"], "error": {"message": "nope"}}).encode())))
    with pytest.raises(cdp.CdpError):
        cdp.Page(s.url).evaluate("x")


def test_a_silent_server_times_out_instead_of_hanging():
    s = FakeSteam(lambda c, m: None)
    p = cdp.Page(s.url)
    t0 = time.monotonic()
    with pytest.raises((cdp.CdpError, socket.timeout, TimeoutError)):
        p.evaluate("x", timeout=0.5)
    assert time.monotonic() - t0 < 3


def test_a_closing_server_raises_cdp_error():
    s = FakeSteam(lambda c, m: c.sendall(frame(8, b"")))
    with pytest.raises(cdp.CdpError):
        cdp.Page(s.url).evaluate("x")


def test_garbage_json_is_skipped_not_fatal():
    def h(c, m):
        c.sendall(frame(1, b"not json at all"))
        reply(c, m, {"result": {"value": "ok"}})

    s = FakeSteam(h)
    assert cdp.Page(s.url).evaluate("x") == "ok"


def test_a_handshake_with_a_wrong_accept_key_is_refused():
    s = FakeSteam(lambda c, m: None, bad_accept=True)
    with pytest.raises(cdp.CdpError):
        cdp.Page(s.url)


def test_only_local_websocket_urls_are_accepted():
    for url in ("ws://example.com:8080/x", "ws://10.0.0.5:8080/x", "wss://127.0.0.1/x", "http://127.0.0.1:8080/x",
                "ws://127.0.0.1.evil.com:8080/x"):
        with pytest.raises(cdp.CdpError):
            cdp.WebSocket(url)


def test_list_pages_is_empty_when_the_port_is_closed(monkeypatch):
    monkeypatch.setattr(cdp, "PORT", 1)  # nothing listens there
    assert cdp.list_pages(timeout=0.5) == []


def _fake_conn(monkeypatch, body: bytes, status: int = 200):
    class Resp:
        def __init__(self):
            self.status = status

        def read(self, n=-1):
            return body

    class Conn:
        def __init__(self, host, port, timeout=None):
            assert (host, port) == ("127.0.0.1", 8080)  # direct to localhost: never through a proxy

        def request(self, method, path):
            assert (method, path) == ("GET", "/json")

        def getresponse(self):
            return Resp()

        def close(self):
            pass

    monkeypatch.setattr(cdp.http.client, "HTTPConnection", Conn)


def test_list_pages_ignores_garbage_and_redirects(monkeypatch):
    _fake_conn(monkeypatch, b"<html>not json</html>")
    assert cdp.list_pages() == []
    _fake_conn(monkeypatch, b'{"not": "a list"}')
    assert cdp.list_pages() == []
    _fake_conn(monkeypatch, b"[]", status=302)  # a redirect is never followed
    assert cdp.list_pages() == []
    _fake_conn(monkeypatch, json.dumps([{"title": "SharedJSContext"}, "junk", 5]).encode())
    assert cdp.list_pages() == [{"title": "SharedJSContext"}]
