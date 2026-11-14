import json
import math
import random
import threading
import time
from dataclasses import asdict, fields

import pytest

from acs import hotkeys
from acs.settings import RANGES, Settings, Store

INT_FIELDS = [f.name for f in fields(Settings) if f.type in ("int", int)]
FLOAT_FIELDS = [f.name for f in fields(Settings) if f.type in ("float", float)]


def weird_value(rng: random.Random, allow_nan: bool = False):
    pool = [None, True, False, 0, -1, 1, 2**63, -(2**63), 10**400, -(10**400), 1e308, -1e308, 1e-308,
            float("inf"), float("-inf"), "", " ", "abc", "50", " 7 ", "1e999", "-1e999", "9" * 500, "x" * 100_000,
            "Ctrl+F8", "Ctrl+Shift+A", "en", "uk", "UK", [], [1, 2], {}, {"a": {"b": [1]}}, (1, 2), b"bytes", object(),
            "\x00", "‮", "\U0001f600", 3.5, -0.0]
    if allow_nan:
        pool.append(float("nan"))
    return rng.choice(pool)


def check_valid(c: Settings, allow_nan_floats: bool = False):
    for f in fields(Settings):
        v = getattr(c, f.name)
        if f.type in ("bool", bool):
            assert type(v) is bool, f.name
        elif f.type in ("int", int):
            assert type(v) is int, f.name
        elif f.type in ("float", float):
            assert type(v) in (int, float), f.name  # clamping to the int bounds yields an int 0/100
            assert allow_nan_floats or not math.isnan(v), f.name
        else:
            assert type(v) is str, f.name
    for k, (lo, hi) in RANGES.items():
        assert lo <= getattr(c, k) <= hi, k
    assert c.language in ("", "en", "uk")
    assert c.match_keyword
    assert hotkeys.parse_combo(c.hk_capture) and hotkeys.parse_combo(c.hk_test)
    assert c.hk_capture != c.hk_test


@pytest.mark.parametrize("data", [None, 5, "x", [], [1], 1.5, b"{}", True])
def test_non_dict_input_gives_base(data):
    assert Settings.from_dict(data) == Settings()
    base = Settings(tg_chat_id="7")
    assert Settings.from_dict(data, base=base) is base


def test_fuzz_from_dict_always_valid():
    rng = random.Random(20240601)
    names = [f.name for f in fields(Settings)]
    for _ in range(3000):
        data = {n: weird_value(rng) for n in names if rng.random() < 0.7}
        if rng.random() < 0.2:
            data["unknown_key"] = weird_value(rng)
        check_valid(Settings.from_dict(data))


def test_from_dict_is_json_roundtrippable_after_fuzz():
    rng = random.Random(5)
    for _ in range(300):
        c = Settings.from_dict({f.name: weird_value(rng) for f in fields(Settings)})
        again = Settings.from_dict(json.loads(json.dumps(asdict(c))))
        assert again == c


@pytest.mark.parametrize("value,expected", [("7", 7), (" 7 ", 7), (7.4, 7), (7.6, 8), ("1e2", 10), (10**400, 3),
                                           (float("inf"), 3), (float("-inf"), 3), ("inf", 3), (-1e308, 1), (1e308, 10),
                                           (None, 3), (True, 3), ("x", 3), ([], 3), ({}, 3)])
def test_int_field_click_count(value, expected):
    assert Settings.from_dict({"click_count": value}).click_count == expected


@pytest.mark.parametrize("field", INT_FIELDS)
def test_every_int_field_clamped_at_extremes(field):
    lo, hi = RANGES[field]
    assert getattr(Settings.from_dict({field: -1e308}), field) == int(lo)
    assert getattr(Settings.from_dict({field: 1e308}), field) == int(hi)
    assert getattr(Settings.from_dict({field: "inf"}), field) == getattr(Settings(), field)  # inf cannot be an int


@pytest.mark.parametrize("field", FLOAT_FIELDS)
def test_every_float_field_clamped_at_extremes(field):
    lo, hi = RANGES[field]
    assert getattr(Settings.from_dict({field: -1e308}), field) == lo
    assert getattr(Settings.from_dict({field: float("inf")}), field) == getattr(Settings(), field)  # non-finite -> default
    assert getattr(Settings.from_dict({field: "12.5"}), field) == 12.5


def test_bool_int_confusion():
    c = Settings.from_dict({"enabled": 0, "beep": 1, "click_count": True, "click_x_pct": False, "focus_cs2": "false"})
    assert c.enabled is True and c.beep is False  # ints are not bools -> defaults
    assert c.click_count == 3 and c.click_x_pct == 50.0  # bools are not numbers -> defaults


def test_nan_float_does_not_survive():
    c = Settings.from_dict({"click_x_pct": float("nan"), "click_y_pct": "nan"})
    assert not math.isnan(c.click_x_pct) and not math.isnan(c.click_y_pct)



def test_nan_int_field_falls_back():
    assert Settings.from_dict({"click_count": float("nan")}).click_count == 3


def test_none_for_string_field_is_not_the_text_None():
    c = Settings.from_dict({"tg_token": None, "tg_chat_id": None, "log_path": {"a": 1}})
    assert c.tg_token == "" and c.tg_chat_id == "" and c.log_path == ""



def test_huge_strings_survive():
    c = Settings.from_dict({"tg_token": "t" * 1_000_000, "match_keyword": " k " * 10})
    assert len(c.tg_token) == 1_000_000 and c.match_keyword == "k  k  k  k  k  k  k  k  k  k"


def test_hotkey_conflict_and_garbage_reset():
    c = Settings.from_dict({"hk_capture": "Ctrl+F5", "hk_test": "Ctrl+F5"})
    assert (c.hk_capture, c.hk_test) == ("Ctrl+F8", "Ctrl+F9")
    c = Settings.from_dict({"hk_capture": "nonsense", "hk_test": "Shift+Ctrl+a"})
    assert (c.hk_capture, c.hk_test) == ("Ctrl+F8", "Ctrl+F9")
    c = Settings.from_dict({"hk_capture": "Shift+Ctrl+A"})
    assert c.hk_capture == "Ctrl+Shift+A"


def test_language_validation():
    assert Settings.from_dict({"language": "de"}).language == ""
    assert Settings.from_dict({"language": " uk "}).language == "uk"
    assert Settings.from_dict({"language": 5}).language == ""


def test_base_is_used_for_bad_values():
    base = Settings(click_x_pct=12.0, tg_chat_id="9")
    c = Settings.from_dict({"click_x_pct": "bad", "tg_chat_id": 77}, base=base)
    assert c.click_x_pct == 12.0 and c.tg_chat_id == "77"


# ---------------------------------------------------------------- Store

@pytest.mark.parametrize("content", [
    b"", b"   \n", b"{", b'{"enabled": tr', b'{"a":1}}', b"\x00\x01\x02\xff\xfe", b"\xef\xbb\xbf{", b"null", b"[]",
    b'"str"', b"12", b'{"click_count": [1,2], "enabled": null}', ("{" + '"x":' * 5000).encode(),
    b"\xff" * 100_000, b"NaN", b'{"click_x_pct": NaN}', b'{"click_x_pct": Infinity}',
    b"[" * 100_000,  # RecursionError in json
])
def test_load_garbage_never_raises(tmp_path, content):
    p = tmp_path / "config.json"
    p.write_bytes(content)
    s = Store(p)
    cfg = s.load()
    assert isinstance(cfg, Settings)
    check_valid(s.get(), allow_nan_floats=content == b'{"click_x_pct": NaN}')
    # the store is still usable afterwards
    s.update(tg_chat_id="1")
    assert json.loads(p.read_text(encoding="utf-8"))["tg_chat_id"] == "1"


def test_load_garbage_is_moved_aside_and_defaults_used(tmp_path):
    p = tmp_path / "config.json"
    p.write_bytes(b"\xff\xfe garbage")
    s = Store(p)
    assert s.load() == Settings()
    assert (tmp_path / "config.bad").read_bytes() == b"\xff\xfe garbage"
    assert not p.exists()


def test_load_truncated_valid_file(tmp_path):
    p = tmp_path / "config.json"
    s = Store(p)
    s.update(tg_chat_id="123", click_count=7)
    full = p.read_bytes()
    for cut in range(0, len(full), max(1, len(full) // 40)):
        p.write_bytes(full[:cut])
        c = Store(p).load()
        check_valid(c)


def test_load_missing_file_gives_defaults_without_creating_it(tmp_path):
    s = Store(tmp_path / "nope.json")
    assert s.load() == Settings()
    assert not (tmp_path / "nope.json").exists()


def test_update_unknown_and_invalid_keys_are_sanitised(tmp_path):
    s = Store(tmp_path / "c.json")
    s.load()
    c = s.update(bogus=1, click_count="999", enabled="no")
    assert c.click_count == 10 and c.enabled is True
    assert "bogus" not in json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))


def test_listener_exception_does_not_break_update(tmp_path):
    s = Store(tmp_path / "c.json")
    s.load()
    calls = []
    s.on_change(lambda c: (_ for _ in ()).throw(RuntimeError("x")))
    s.on_change(calls.append)
    s.update(beep=True)
    assert len(calls) == 1 and s.get().beep is True


def test_save_failure_is_swallowed(tmp_path):
    s = Store(tmp_path / "missing_dir" / "c.json")
    c = s.update(beep=True)  # directory does not exist -> OSError logged, memory state kept
    assert c.beep is True and s.get().beep is True


def test_atomic_save_leaves_no_tmp(tmp_path):
    p = tmp_path / "c.json"
    s = Store(p)
    for i in range(100):
        s.update(click_count=(i % 10) + 1, tg_chat_id="é" * i)
    assert sorted(x.name for x in tmp_path.iterdir()) == ["c.json"]
    assert json.loads(p.read_text(encoding="utf-8"))["tg_chat_id"] == "é" * 99


def test_concurrent_updates_from_8_threads_yield_valid_json(tmp_path):
    p = tmp_path / "c.json"
    s = Store(p)
    s.load()
    errors = []

    def work(tid):
        try:
            for i in range(200):
                s.update(click_count=(i % 10) + 1, tg_chat_id=f"{tid}-{i}", beep=bool(i % 2))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=work, args=(t,)) for t in range(8)]
    t0 = time.monotonic()
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert not errors and all(not t.is_alive() for t in ts)
    assert time.monotonic() - t0 < 20
    data = json.loads(p.read_text(encoding="utf-8"))
    assert Settings.from_dict(data) == s.get()
    assert not list(tmp_path.glob("*.tmp"))
    assert Store(p).load() == s.get()


def test_concurrent_readers_never_see_partial_state(tmp_path):
    p = tmp_path / "c.json"
    s = Store(p)
    s.load()
    stop = threading.Event()
    bad = []

    def reader():
        while not stop.is_set():
            time.sleep(0.0005)
            c = s.get()
            if c.tg_token != c.tg_chat_id:  # both are always updated together
                bad.append(c)

    def writer(tid):
        for i in range(100):
            v = f"{tid}:{i}"
            s.update(tg_token=v, tg_chat_id=v)

    rs = [threading.Thread(target=reader) for _ in range(3)]
    ws = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    for t in rs + ws:
        t.start()
    for t in ws:
        t.join(30)
    stop.set()
    for t in rs:
        t.join(5)
    assert not bad
    assert not list(tmp_path.glob("*.tmp"))


def test_file_never_partially_written_while_polled(tmp_path):
    """A parallel reader polling the file must only ever see complete JSON (or a transient access error)."""
    p = tmp_path / "c.json"
    s = Store(p)
    s.update(tg_chat_id="seed")
    stop, bad, ok = threading.Event(), [], [0]

    def reader():
        while not stop.is_set():
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            try:
                json.loads(raw.decode("utf-8"))
                ok[0] += 1
            except ValueError:
                bad.append(raw[:50])

    t = threading.Thread(target=reader)
    t.start()
    for i in range(150):
        s.update(tg_chat_id="x" * (1 + i * 20))
    stop.set()
    t.join(5)
    assert not bad and ok[0] > 0
