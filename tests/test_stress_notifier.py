import http.client
import json
import threading
import time
import urllib.parse

import pytest

from acs import notifier
from acs.notifier import Notifier, send_once
from acs.settings import Store

TOKEN = "123456:" + "Ab_-" * 8
TOKEN2 = "654321:" + "Zy_-" * 8


class FakeResp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self, n=-1):
        return self._body[:n] if n and n > 0 else self._body


class Net:
    """Scripted replacement for http.client.HTTPSConnection. Each connection pops one behaviour."""

    def __init__(self, script=None, default=("ok",)):
        self.script, self.default = list(script or []), default
        self.connections = 0
        self.requests: list[tuple[str, str, str]] = []
        self.lock = threading.Lock()

    def next(self):
        with self.lock:
            self.connections += 1
            return self.script.pop(0) if self.script else self.default

    def __call__(self, host, timeout=None, **kw):
        return FakeConn(self, self.next())


class FakeConn:
    def __init__(self, net, behaviour):
        self.net, self.b = net, behaviour

    def connect(self):
        if self.b[0] == "connect_fail":
            raise OSError(f"cannot reach https://api.telegram.org/bot{TOKEN}/x")

    def request(self, method, url, body=None, headers=None):
        self.net.requests.append((method, url, body))
        if self.b[0] == "request_fail":
            raise ConnectionResetError(f"reset {TOKEN}")

    def getresponse(self):
        k = self.b[0]
        if k == "read_timeout":
            raise TimeoutError(f"timed out {TOKEN}")
        if k == "status":
            body = self.b[2] if len(self.b) > 2 else {}
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            return FakeResp(self.b[1], raw)
        return FakeResp(200, b'{"ok": true}')

    def close(self):
        pass


class FakeEvent:
    """Event whose wait() never sleeps; records the requested waits."""

    def __init__(self):
        self._set, self.waits = False, []

    def set(self):
        self._set = True

    def is_set(self):
        return self._set

    def wait(self, t=None):
        self.waits.append(t)
        return self._set


@pytest.fixture(autouse=True)
def fresh_gate():
    """The shared send gate (module-global) must not leak 429 flood-waits between tests."""
    notifier._gate_state.update(last=0.0, not_before=0.0)
    yield
    notifier._gate_state.update(last=0.0, not_before=0.0)


@pytest.fixture
def net(monkeypatch):
    n = Net()
    monkeypatch.setattr(http.client, "HTTPSConnection", n)
    return n


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "c.json")
    s.load()
    s.update(tg_enabled=True, tg_token=TOKEN, tg_chat_id="42")
    return s


@pytest.fixture
def ntf(store):
    n = Notifier(store)
    n._stop = FakeEvent()  # deterministic, no real sleeping
    yield n
    n._stop.set()
    n._q.put_nowait(None)


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ---------------------------------------------------------------- send_once

def test_success(net):
    r = send_once(TOKEN, "42", "hi")
    assert r.ok and not r.fatal and r.info == "HTTP 200"
    method, url, body = net.requests[0]
    assert method == "POST" and url == f"/bot{TOKEN}/sendMessage" and "chat_id=42" in body


def test_text_is_truncated_to_the_telegram_limit(net):
    send_once(TOKEN, "42", "x" * 10_000)
    assert len(urllib.parse.parse_qs(net.requests[0][2])["text"][0]) == 4096


@pytest.mark.parametrize("token", ["", "SECRET", "123:abc", "abc:" + "A" * 30, "123456:" + "A" * 19,
                                   "123456:" + "A" * 30 + "\nX", "12345:aaaa aaaa aaaa aaaa aaaa", "тест:" + "A" * 30,
                                   "123456:" + "A" * 30 + "/../x"])
def test_invalid_token_never_opens_connection(net, token):
    r = send_once(token, "42", "hi")
    assert not r.ok and r.fatal
    assert net.connections == 0


def test_empty_chat_never_opens_connection(net):
    r = send_once(TOKEN, "  ", "hi")
    assert not r.ok and r.fatal and net.connections == 0


def test_token_with_surrounding_whitespace_is_accepted(net):
    assert send_once(f"  {TOKEN}\n", " 42 ", "hi").ok


def test_429_reports_retry_after(net):
    net.script = [("status", 429, {"parameters": {"retry_after": 7}}),
                  ("status", 429, {"parameters": {"retry_after": 99999}}),
                  ("status", 429, {"parameters": {"retry_after": 0}}),
                  ("status", 429, {})]
    rs = [send_once(TOKEN, "42", "hi") for _ in range(4)]
    assert all(r.safe_retry and not r.ok and not r.fatal for r in rs)
    assert [r.retry_after for r in rs] == [7.0, 99999.0, 5.0, 5.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_fatal(net, status):
    net.script = [("status", status, {"ok": False, "description": "bad"})]
    r = send_once(TOKEN, "42", "hi")
    assert r.fatal and not r.safe_retry and not r.ok


@pytest.mark.parametrize("status", [500, 502, 503, 599])
def test_5xx_is_safe_retry(net, status):
    net.script = [("status", status, b"<html>oops</html>")]
    r = send_once(TOKEN, "42", "hi")
    assert r.safe_retry and not r.fatal and not r.ok


def test_other_status_not_retried_not_fatal(net):
    net.script = [("status", 418, {})]
    r = send_once(TOKEN, "42", "hi")
    assert not r.ok and not r.safe_retry and not r.fatal


def test_ok_false_in_2xx_body_is_failure(net):
    net.script = [("status", 200, {"ok": False})]
    assert not send_once(TOKEN, "42", "hi").ok


def test_garbage_body_does_not_raise(net):
    net.script = [("status", 200, b"\xff\xfe not json"), ("status", 500, b""), ("status", 400, b"{")]
    assert [send_once(TOKEN, "42", "hi").ok for _ in range(3)] == [True, False, False]


def test_connect_failure_is_safe_retry(net):
    net.script = [("connect_fail",)]
    r = send_once(TOKEN, "42", "hi")
    assert not r.ok and r.safe_retry and net.requests == []
    assert TOKEN not in r.info


@pytest.mark.parametrize("kind", ["read_timeout", "request_fail"])
def test_failure_after_request_is_not_retryable_and_hides_token(net, kind):
    net.script = [(kind,)]
    r = send_once(TOKEN, "42", "hi")
    assert not r.ok and not r.safe_retry and not r.fatal
    assert TOKEN not in r.info and "delivery unknown" in r.info


def test_error_description_never_contains_token(net):
    net.script = [("status", 400, {"description": f"bad request {TOKEN}"})]
    r = send_once(TOKEN, "42", "hi")
    assert TOKEN not in r.info and "***" in r.info


def test_error_description_truncation_does_not_leak_token_prefix(net):
    net.script = [("status", 400, {"description": "x" * 70 + TOKEN})]
    r = send_once(TOKEN, "42", "hi")
    assert TOKEN[:10] not in r.info



@pytest.mark.parametrize("body", [b"[]", b"[1,2]", b'"str"', b"123", b"null"])
def test_non_dict_json_body_does_not_raise(net, body):
    net.script = [("status", 500, body)]
    send_once(TOKEN, "42", "hi")



def test_post_message_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(TOKEN)

    monkeypatch.setattr(http.client, "HTTPSConnection", boom)
    ok, info = notifier.post_message(TOKEN, "42", "hi")
    assert not ok and TOKEN not in info


# ---------------------------------------------------------------- Notifier worker logic

def test_deliver_success(net, ntf):
    ntf._deliver("hello")
    assert net.connections == 1


def test_deliver_429_waits_retry_after_and_retries(net, ntf):
    net.script = [("status", 429, {"parameters": {"retry_after": 3}}), ("ok",)]
    ntf._deliver("hello")
    assert net.connections == 2
    assert 3.0 in ntf._stop.waits


def test_long_flood_wait_drops_messages_instead_of_blocking(net, ntf):
    net.script = [("status", 429, {"parameters": {"retry_after": 3600}})]
    t0 = time.monotonic()
    ntf._deliver("one")
    for i in range(5):
        ntf._deliver(f"later {i}")
    assert net.connections == 1 and time.monotonic() - t0 < 1.0  # nothing was slept or sent


def test_post_message_shares_the_gate_with_worker(net, ntf):
    net.script = [("status", 429, {"parameters": {"retry_after": 3600}})]
    ntf._deliver("one")
    ok, info = notifier.post_message(TOKEN, "42", "test")
    assert not ok and "rate limited" in info and net.connections == 1


def test_deliver_5xx_retries_up_to_max_attempts(net, ntf):
    net.default = ("status", 503, {})
    ntf._deliver("hello")
    assert net.connections == notifier.MAX_ATTEMPTS


def test_deliver_5xx_then_success(net, ntf):
    net.script = [("status", 502, {}), ("ok",)]
    ntf._deliver("hello")
    assert net.connections == 2


def test_deliver_connect_failure_retried(net, ntf):
    net.script = [("connect_fail",), ("connect_fail",), ("ok",)]
    ntf._deliver("hello")
    assert net.connections == 3 and len(net.requests) == 1


def test_deliver_connect_failure_gives_up(net, ntf):
    net.default = ("connect_fail",)
    ntf._deliver("hello")
    assert net.connections == notifier.MAX_ATTEMPTS and net.requests == []


@pytest.mark.parametrize("kind", ["read_timeout", "request_fail"])
def test_deliver_failure_after_send_is_not_retried(net, ntf, kind):
    net.default = (kind,)
    ntf._deliver("hello")
    assert net.connections == 1


@pytest.mark.parametrize("status", [400, 401, 403])
def test_fatal_blocks_until_token_or_chat_changes(net, ntf, store, status):
    net.script = [("status", status, {"description": "nope"})]
    ntf._deliver("one")
    assert net.connections == 1
    for i in range(10):
        ntf._deliver(f"later {i}")
    assert net.connections == 1  # dropped without touching the network
    store.update(tg_token=TOKEN2)
    ntf._deliver("after token change")
    assert net.connections == 2
    net.script = [("status", status, {})]
    ntf._deliver("fails again")
    assert net.connections == 3
    ntf._deliver("blocked again")
    assert net.connections == 3
    store.update(tg_chat_id="43")
    ntf._deliver("after chat change")
    assert net.connections == 4


def test_invalid_token_blocks_and_opens_no_connection(net, ntf, store):
    store.update(tg_token="SECRET")
    for i in range(5):
        ntf._deliver(f"m{i}")
    assert net.connections == 0


def test_identical_messages_deduped(net, ntf):
    for _ in range(10):
        ntf._deliver("same")
    assert net.connections == 1
    ntf._deliver("different")
    assert net.connections == 2


def test_dedupe_window_expires(net, ntf, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(notifier.time, "monotonic", lambda: clock[0])
    ntf._deliver("same")
    clock[0] += notifier.DEDUPE_WINDOW + 1
    ntf._deliver("same")
    assert net.connections == 2


def test_messages_are_spaced_by_min_interval(net, ntf, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(notifier.time, "monotonic", lambda: clock[0])
    ntf._deliver("a")
    ntf._deliver("b")
    ntf._deliver("c")
    assert net.connections == 3
    # clock is frozen, so a and b/c must each have asked to wait the full interval
    gaps = [w for w in ntf._stop.waits if w and w > 0]
    assert len(gaps) == 2 and all(abs(g - notifier.MIN_INTERVAL) < 1e-6 for g in gaps)


def test_stop_during_wait_aborts_delivery(net, ntf):
    notifier._gate_state["last"] = time.monotonic()
    ntf._stop.set()
    ntf._deliver("x")
    assert net.connections == 0


# ---------------------------------------------------------------- send() / worker thread

def test_send_respects_flags(net, store):
    n = Notifier(store)
    n._stop = FakeEvent()
    try:
        store.update(tg_on_ping=False)
        n.send("p", "ping")
        assert n._q.empty()
        store.update(tg_enabled=False)
        n.send("m", "match")
        assert n._q.empty()
    finally:
        n._stop.set()
        n._q.put_nowait(None)


def test_end_to_end_through_worker(net, ntf):
    ntf.send("one", "match")
    ntf.send("two", "match")
    ntf.send("one", "match")  # duplicate within window
    assert wait_for(lambda: net.connections == 2)
    time.sleep(0.1)
    assert net.connections == 2


def test_queue_overflow_drops_without_blocking(net, store, monkeypatch, caplog):
    gate, started = threading.Event(), threading.Event()
    calls = []

    def slow_send_once(*a, **k):
        calls.append(a[2])
        started.set()
        gate.wait(5)
        return notifier.SendResult(True, "ok")

    monkeypatch.setattr(notifier, "send_once", slow_send_once)
    n = Notifier(store)
    n._stop = FakeEvent()
    try:
        n.send("first", "match")
        assert started.wait(3)  # worker is now stuck inside the first delivery
        t0 = time.monotonic()
        with caplog.at_level("WARNING", logger="acs.notify"):
            for i in range(200):
                n.send(f"msg {i}", "match")
        assert time.monotonic() - t0 < 1.0
        assert n._q.qsize() == notifier.QUEUE_SIZE
        assert "queue full" in caplog.text
    finally:
        gate.set()
        n._stop.set()
        try:
            n._q.put_nowait(None)
        except Exception:
            pass
    assert wait_for(lambda: not n._thread.is_alive())


def test_token_never_in_logs_or_infos(net, ntf, caplog):
    net.script = [("connect_fail",), ("read_timeout",), ("status", 400, {"description": f"x {TOKEN}"}),
                  ("request_fail",), ("status", 500, {})]
    infos = []
    with caplog.at_level("DEBUG"):
        for _ in range(5):
            infos.append(send_once(TOKEN, "42", "m").info)
        net.script = [("connect_fail",), ("status", 429, {}), ("status", 401, {"description": TOKEN})]
        ntf._deliver("log me")
        ntf._blocked_sig = None
        ntf._deliver("log me 2")
    assert all(TOKEN not in i for i in infos)
    assert TOKEN not in caplog.text and TOKEN2 not in caplog.text


def test_stop_terminates_worker_thread(store):
    n = Notifier(store)
    assert n._thread.is_alive()
    n.stop()
    n._thread.join(3)
    assert not n._thread.is_alive()


def test_stop_when_queue_is_full_still_terminates(net, store, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(notifier, "send_once", lambda *a, **k: (gate.wait(5), notifier.SendResult(True, "ok"))[1])
    n = Notifier(store)
    for i in range(notifier.QUEUE_SIZE + 5):
        n.send(f"m{i}", "match")
    n.stop()  # queue full: the None sentinel cannot be queued, the stop flag must still end the worker
    gate.set()
    n._thread.join(5)
    assert not n._thread.is_alive()


def test_worker_survives_exceptions_in_deliver(net, store, monkeypatch):
    n = Notifier(store)
    n._stop = FakeEvent()
    real = n._deliver
    seen = []

    def deliver(text):
        seen.append(text)
        if text == "bad":
            raise RuntimeError("x")
        real(text)

    n._deliver = deliver
    try:
        n.send("bad", "match")
        n.send("good", "match")
        assert wait_for(lambda: net.connections == 1 and seen == ["bad", "good"])
    finally:
        n._stop.set()
        n._q.put_nowait(None)


def test_text_is_cut_by_utf16_units_and_a_too_long_400_is_not_fatal():
    from acs import notifier
    text = "😀" * 3000  # 6000 UTF-16 units
    assert len(notifier._fit(text).encode("utf-16-le")) // 2 <= 4096
    assert notifier._fit("short") == "short"
