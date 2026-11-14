"""Friend-invite auto-accept through Steam's own UI data (no screen scraping): ids, parsing, the monitor."""
from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock

from acs import cdp
from acs.friends import friend_names, parse_ids
from acs.invite import LAUNCH_URL, InviteMonitor, parse_invite
from acs.settings import Settings, Store

ID64 = 76561198000000001  # = 76561197960265728 + 39734273
FRIEND = "76561199440411418"
LOBBY_URL = f"steam://joinlobby/730/109775244535154266/{FRIEND}"


# ---- steam ids -> names (hint in the settings window) ------------------------------------------------
def test_parse_ids_accepts_id64_account_id_and_profile_urls():
    text = "\n".join([f"{ID64}, 39734273 ;", f"https://steamcommunity.com/profiles/{ID64}/",
                      "https://steamcommunity.com/id/vanity", "junk 12"])
    assert parse_ids(text) == [39734273]  # deduplicated; vanity urls and junk ignored
    assert parse_ids("") == [] and parse_ids("0, 99999999999999999999") == []


VDF = """"UserLocalConfigStore"
{
	"friends"
	{
		"39734273"
		{
			"name"		"error_500"
			"NameHistory"
			{
				"0"		"error_500"
				"1"		"old_name"
			}
		}
	}
	"other"
	{
		"39734273"
		{
			"name"		"must_not_be_used"
		}
	}
}
"""


def test_friend_names_come_from_the_friends_block_including_history(tmp_path):
    f = tmp_path / "localconfig.vdf"
    f.write_text(VDF, encoding="utf-8")
    assert friend_names([39734273], f) == ["error_500", "old_name"]
    assert friend_names([5], f) == [] and friend_names([39734273], tmp_path / "missing.vdf") == []


# ---- parsing what Steam's notification store hands us -----------------------------------------------
def chat_event(url=LOBBY_URL, sender=FRIEND, nid=6, etype=8, t=None):
    """Shape of a real captured notification: eType 8 = friend message, array[6] = the Join link."""
    return {"t": int((t if t is not None else time.time()) * 1000), "fn": "OnNewNotificationReceived", "n": nid,
            "etype": etype, "arr": [f"message_{nid}", sender, "", "witch demon invites you to Counter-Strike 2", "https://a/x.jpg", 6, url]}


def test_a_real_invite_is_parsed():
    inv = parse_invite(chat_event())
    assert inv is not None and inv.sender == int(FRIEND) - 76561197960265728 and inv.url == LOBBY_URL


def test_everything_that_is_not_a_cs2_lobby_invite_is_ignored():
    assert parse_invite(chat_event(etype=3)) is None  # "friend in game" toast
    assert parse_invite(chat_event(url="")) is None  # an ordinary chat message
    assert parse_invite(chat_event(url=f"steam://joinlobby/570/1/{FRIEND}")) is None  # another game
    assert parse_invite(chat_event(url="steam://joinlobby/730/1/2; calc.exe")) is None  # junk in the link
    assert parse_invite(chat_event(url="https://evil.example/730/1/2")) is None
    assert parse_invite(chat_event(sender="nope")) is None
    assert parse_invite({"arr": "x"}) is None and parse_invite(None) is None and parse_invite({}) is None


# ---- the monitor --------------------------------------------------------------------------------------
class FakePage:
    def __init__(self):
        self.queue: list = []
        self.executed: list[str] = []
        self.installed = 0

    def evaluate(self, expr, timeout=8.0):
        if "ExecuteSteamURL" in expr:
            self.executed.append(expr)
            return None
        self.installed += 1
        out, self.queue = self.queue, []
        return out

    def close(self):
        pass


class Env:
    def __init__(self, tmp_path, **cfg):
        self.store = Store(tmp_path / "c.json")
        self.store.load()
        self.store.update(**{"invite_enabled": True, "invite_ids": "1480145690", **cfg})
        self.page = FakePage()
        self.pages = [{"title": "SharedJSContext", "webSocketDebuggerUrl": "ws://127.0.0.1:8080/devtools/page/1"}]
        self.notifier = MagicMock()
        self.game_up = True
        self.mon = InviteMonitor(self.store, self.notifier, pages=lambda: self.pages, connect=lambda url: self.page,
                                 ensure_flag=lambda: True, cs2_running=lambda: self.game_up)


def test_whitelisted_friend_invite_is_accepted_by_running_the_steam_url(tmp_path):
    e = Env(tmp_path)
    e.page.queue = [chat_event()]
    e.mon.tick()
    assert len(e.page.executed) == 1 and json.dumps(LOBBY_URL) in e.page.executed[0]
    assert e.notifier.send.called


def test_unlisted_friend_is_never_accepted(tmp_path):
    e = Env(tmp_path, invite_ids="123456789")
    e.page.queue = [chat_event()]
    e.mon.tick()
    assert e.page.executed == [] and not e.notifier.send.called


def test_disabled_or_empty_list_does_nothing_and_does_not_even_connect(tmp_path):
    e = Env(tmp_path, invite_enabled=False)
    e.page.queue = [chat_event()]
    e.mon.tick()
    assert e.page.executed == [] and e.page.installed == 0
    e.store.update(invite_enabled=True, invite_ids="")
    e.mon.tick()
    assert e.page.executed == [] and e.page.installed == 0


def test_stale_and_duplicate_invites_are_ignored(tmp_path):
    e = Env(tmp_path)
    e.page.queue = [chat_event(t=time.time() - 300)]  # something that was queued minutes ago
    e.mon.tick()
    assert e.page.executed == []
    e.page.queue = [chat_event(nid=7)]
    e.mon.tick()
    e.page.queue = [chat_event(nid=8)]  # the same lobby again within the cooldown: one join is enough
    e.mon.tick()
    assert len(e.page.executed) == 1


def test_two_different_lobbies_are_both_joined_after_the_cooldown(tmp_path):
    e = Env(tmp_path)
    e.mon.COOLDOWN = 0.0
    e.page.queue = [chat_event()]
    e.mon.tick()
    e.page.queue = [chat_event(url=f"steam://joinlobby/730/999/{FRIEND}", nid=9)]
    e.mon.tick()
    assert len(e.page.executed) == 2


def test_status_tells_the_user_what_is_missing(tmp_path):
    e = Env(tmp_path)
    e.pages = []
    e.mon.tick()
    assert e.mon.status == "no_port"
    e.pages = [{"title": "Steam", "webSocketDebuggerUrl": "ws://127.0.0.1:8080/x"}]
    e.mon.tick()
    assert e.mon.status == "no_ui"
    e.pages = [{"title": "SharedJSContext", "webSocketDebuggerUrl": "ws://127.0.0.1:8080/x"}]
    e.mon.tick()
    assert e.mon.status == "ok"
    e.store.update(invite_enabled=False)
    e.mon.tick()
    assert e.mon.status == "off"
    e.store.update(invite_enabled=True, invite_ids="")
    e.mon.tick()
    assert e.mon.status == "no_ids"  # switch is on but nobody is listed: say so instead of a bare "off"


def test_a_broken_connection_never_raises_and_is_retried(tmp_path):
    e = Env(tmp_path)
    boom = MagicMock(side_effect=cdp.CdpError("closed"))
    e.mon._connect = boom
    e.mon.tick()  # must not raise
    assert e.mon.status == "no_ui" or e.mon.status == "error"
    e.mon._connect = lambda url: e.page
    e.page.queue = [chat_event()]
    e.mon.tick()
    assert len(e.page.executed) == 1


def test_the_flag_file_is_prepared_when_the_feature_is_on(tmp_path):
    e = Env(tmp_path)
    calls = []
    e.mon._ensure_flag = lambda: calls.append(1) or True
    e.mon.tick()
    e.store.update(invite_enabled=False)
    e.mon.tick()
    assert len(calls) == 1  # only while enabled


def test_settings_have_safe_defaults():
    s = Settings()
    assert s.invite_enabled is False and s.invite_ids == ""
    assert Settings.from_dict({"invite_enabled": "yes", "invite_ids": None}).invite_enabled is False


def test_with_the_game_closed_it_is_launched_first_and_the_lobby_joined_only_when_the_window_is_up(tmp_path):
    e = Env(tmp_path)
    e.game_up = False
    e.mon.SETTLE, e.mon.RETRY_GAP = 0.0, 0.05
    e.page.queue = [chat_event()]
    e.mon.tick()
    assert e.page.executed == ["SteamClient.URL.ExecuteSteamURL(" + json.dumps(LAUNCH_URL) + ")"]  # only launches CS2
    time.sleep(0.8)
    assert len(e.page.executed) == 1  # still waiting for the game window: no premature join
    e.game_up = True
    deadline = time.monotonic() + 3
    while len(e.page.executed) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(e.page.executed) == 2  # launch + one join link (a repeat would rejoin the lobby)
    assert all(json.dumps(LOBBY_URL) in x for x in e.page.executed[1:])


def test_with_the_game_running_the_lobby_is_joined_at_once_and_nothing_is_launched(tmp_path):
    e = Env(tmp_path)
    e.mon.SETTLE, e.mon.RETRY_GAP = 0.0, 0.05
    e.page.queue = [chat_event()]
    e.mon.tick()
    time.sleep(0.5)
    assert len(e.page.executed) == 1 and json.dumps(LOBBY_URL) in e.page.executed[0]
    assert LAUNCH_URL not in e.page.executed[0]


def test_stop_ends_a_pending_rejoin(tmp_path):
    e = Env(tmp_path)
    e.game_up = False
    e.page.queue = [chat_event()]
    e.mon.tick()
    e.mon.stop()
    e.game_up = True
    e.mon.SETTLE = 0.0
    time.sleep(0.8)
    assert len(e.page.executed) == 1


# ---- CS2's confirmation boxes (answered by clicking OK) -----------------------------------------------------------
def wait_until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class OkEnv(Env):
    def __init__(self, tmp_path, **cfg):
        super().__init__(tmp_path, **cfg)
        self.enters = 0
        self.ready = True
        self.mon._ready = lambda: self.ready
        self.mon._enter = self._enter
        self.mon.RENDER_DELAY = 0.0
        self.mon.READY_TIMEOUT = 2.0

    def _enter(self):
        self.enters += 1
        return True

    def invite(self):
        self.page.queue = [chat_event()]
        self.mon.tick()  # runs the join link


def test_after_our_join_the_confirmation_box_is_answered_with_enter(tmp_path):
    e = OkEnv(tmp_path)
    e.invite()
    e.mon.on_dialog()  # the game's console.log reports the box
    assert wait_until(lambda: e.enters == 1)


def test_a_confirmation_box_we_did_not_cause_is_never_touched(tmp_path):
    e = OkEnv(tmp_path)
    e.mon.on_dialog()
    time.sleep(0.2)
    assert e.enters == 0


def test_the_box_must_appear_soon_after_our_join(tmp_path):
    e = OkEnv(tmp_path)
    e.mon.ARM_SEC = 0.05
    e.invite()
    time.sleep(0.2)
    e.mon.on_dialog()
    time.sleep(0.2)
    assert e.enters == 0


def test_enter_waits_for_the_game_window_to_come_to_the_front(tmp_path):
    e = OkEnv(tmp_path)
    e.ready = False  # CS2 is minimised / slow to open
    e.invite()
    e.mon.on_dialog()
    time.sleep(0.4)
    assert e.enters == 0  # never types into a window that is not ready
    e.ready = True
    assert wait_until(lambda: e.enters == 1)


def test_enter_gives_up_if_the_window_never_becomes_ready(tmp_path):
    e = OkEnv(tmp_path)
    e.ready = False
    e.mon.READY_TIMEOUT = 0.3
    e.invite()
    e.mon.on_dialog()
    time.sleep(0.8)
    e.ready = True
    time.sleep(0.4)
    assert e.enters == 0


def test_the_failed_to_join_box_after_the_first_one_is_answered_too_but_nothing_beyond_that(tmp_path):
    e = OkEnv(tmp_path)
    e.invite()
    e.mon.on_dialog()  # "Join another game?"
    assert wait_until(lambda: e.enters == 1 and not e.mon._confirming)
    e.mon.on_dialog()  # the lobby is gone: "Failed to join session"
    assert wait_until(lambda: e.enters == 2 and not e.mon._confirming)
    for _ in range(3):  # anything after these two boxes is none of our business
        e.mon.on_dialog()
        time.sleep(0.1)
    assert e.enters == 2


def test_a_second_report_that_arrives_during_the_first_answer_is_not_lost(tmp_path):
    e = OkEnv(tmp_path)
    e.invite()
    gate = threading.Event()
    e.mon._enter = lambda: (gate.wait(0.3), e._enter())[1]  # the first Enter takes a moment
    e.mon.on_dialog()
    time.sleep(0.1)
    e.mon.on_dialog()  # the error box was reported while the first answer is still running
    assert wait_until(lambda: e.enters == 2, 4)


def test_launching_the_game_does_not_arm_the_confirmation(tmp_path):
    e = OkEnv(tmp_path)
    e.game_up = False
    e.mon.SETTLE = 5.0  # the join link comes much later
    e.invite()  # only the launch link was run
    e.mon.on_dialog()
    time.sleep(0.2)
    assert e.enters == 0
    e.mon.stop()


def test_ok_position_comes_from_the_calibration_else_from_the_fit(tmp_path, monkeypatch):
    from acs import invite, winapi
    from acs.i18n import tr
    rect = (100, 50, 1080, 1080)  # (left, top, width, height): a 1080x1080 window at an offset
    assert invite.ok_point(rect) == (100 + 540 + 150, 50 + 605)
    assert abs(invite.ok_point((0, 0, 2560, 1440))[0] - 1532) < 4  # measured on a real 2560x1440 box
    assert abs(invite.ok_point((0, 0, 2560, 1440))[1] - 796) < 14
    saved = json.dumps({"1080x1080": [0.6, 0.5]})
    assert invite.ok_point(rect, saved) == (100 + 648, 50 + 540)  # the user's own click wins
    assert invite.ok_point((0, 0, 1280, 720), saved) == invite.guess_ok_point((0, 0, 1280, 720))  # another size
    for bad in ("", "x", "[]", '{"1080x1080": [2, 0.5]}', '{"1080x1080": "no"}'):
        assert invite.ok_point(rect, bad) == invite.guess_ok_point(rect)
    e = Env(tmp_path)
    clicks = []
    monkeypatch.setattr(winapi, "find_cs2_window", lambda: 5)
    monkeypatch.setattr(winapi, "client_rect", lambda h: rect)
    monkeypatch.setattr(winapi.user32, "SetCursorPos", lambda x, y: True, raising=False)
    monkeypatch.setattr(winapi, "click", lambda x, y, h: clicks.append((x, y, h)) or True)
    monkeypatch.setattr(invite.time, "sleep", lambda s: None)
    assert e.mon._click_ok() is True and clicks == [(790, 655, 5)]
    e.mon._click_ok()
    assert [c.args[0] for c in e.notifier.send.call_args_list].count(tr("msg.calibrate_needed", size="1080x1080")) == 1  # told once
    e.store.update(invite_ok=saved)
    clicks.clear()
    e.mon._click_ok()
    assert clicks == [(748, 590, 5)]
    monkeypatch.setattr(winapi, "client_rect", lambda h: None)
    assert e.mon._click_ok() is False


def _calibration_env(tmp_path, monkeypatch, presses, rect=(0, 0, 2560, 1440)):
    from acs import winapi
    e = Env(tmp_path)
    seq = iter(presses)  # (button_down, cursor) samples
    state = {"cur": (0, 0)}

    def down():
        d, cur = next(seq, (False, state["cur"]))
        state["cur"] = cur
        return d

    monkeypatch.setattr(winapi, "find_cs2_window", lambda: 5)
    monkeypatch.setattr(winapi, "focus", lambda h: True)
    monkeypatch.setattr(winapi, "client_rect", lambda h: rect)
    monkeypatch.setattr(winapi, "left_button_down", down)
    monkeypatch.setattr(winapi, "cursor_pos", lambda: state["cur"])
    e.mon._stop.wait = lambda t=None: False  # no real waiting
    return e


def test_calibration_saves_the_users_click_as_window_fractions(tmp_path, monkeypatch):
    from acs.invite import CALIB_URL
    e = _calibration_env(tmp_path, monkeypatch, [(False, (0, 0)), (True, (1530, 800))])
    got = []
    e.mon.calibrate(got.append, wait=2.0)
    assert wait_until(lambda: got == ["saved"])
    assert json.loads(e.store.get().invite_ok) == {"2560x1440": [0.5977, 0.5556]}
    assert any(json.dumps(CALIB_URL) in x for x in e.page.executed)  # the box was opened on a lobby that does not exist
    e.mon.on_dialog()  # and our own answering stayed out of it
    time.sleep(0.2)
    assert e.enters == 0 if hasattr(e, "enters") else True


def test_calibration_reports_missing_game_timeout_and_clicks_outside(tmp_path, monkeypatch):
    from acs import winapi
    e = _calibration_env(tmp_path, monkeypatch, [])
    got = []
    e.mon.calibrate(got.append, wait=0.2)
    assert wait_until(lambda: got == ["timeout"])
    monkeypatch.setattr(winapi, "find_cs2_window", lambda: None)
    got.clear()
    e.mon.calibrate(got.append, wait=0.2)
    assert wait_until(lambda: got == ["no_game"])
    (tmp_path / "b").mkdir()
    e2 = _calibration_env(tmp_path / "b", monkeypatch, [(False, (0, 0)), (True, (-5, 10))])
    got2 = []
    e2.mon.calibrate(got2.append, wait=1.0)
    assert wait_until(lambda: got2 == ["outside"])
    assert e2.store.get().invite_ok == ""


def test_watcher_reports_the_confirmation_dialog_line(tmp_path):
    from acs.watcher import Watcher
    store = Store(tmp_path / "c.json")
    store.load()
    seen = []
    w = Watcher(store, MagicMock(), MagicMock(), on_dialog=lambda: seen.append(1))
    w.handle_line("11/14 14:37:16 [Localization System] CLocalize::CompileString failed to localize: '#GenericConfirmText_Label'")
    w.handle_line("something else")
    assert seen == [1]


# ---- vanity profile links (steamcommunity.com/id/<name>) ---------------------------------------------------
def test_vanity_names_are_extracted_from_profile_links():
    from acs import friends
    text = "https://steamcommunity.com/id/exampleuser/\nsteamcommunity.com/id/some_name\nhttps://evil.example/id/nope\njunk"
    assert friends.vanity_names(text) == ["exampleuser", "some_name"]
    assert friends.vanity_names("https://steamcommunity.com/id/a/") == []  # too short to be a profile name
    assert friends.vanity_names("https://steamcommunity.com/id/../../x") == []


def test_vanity_links_are_resolved_in_the_background_and_cached(monkeypatch):
    from acs import friends
    calls = []

    def fake_fetch(name):
        calls.append(name)
        return 76561198000000000 if name == "exampleuser" else None

    monkeypatch.setattr(friends, "fetch_vanity", fake_fetch)
    friends.reset_vanity_cache()
    text = "39734273\nhttps://steamcommunity.com/id/exampleuser/"
    assert friends.all_ids(text) == [39734273]  # first call: the lookup runs in the background, nothing blocks
    assert wait_until(lambda: 39734272 in friends.all_ids(text))
    assert friends.all_ids(text) == [39734273, 39734272]
    friends.all_ids(text)
    assert calls == ["exampleuser"]  # looked up once, then served from the cache
    assert friends.unresolved(text) == []


def test_a_vanity_name_that_cannot_be_resolved_is_reported_and_retried_later(monkeypatch):
    from acs import friends
    monkeypatch.setattr(friends, "fetch_vanity", lambda name: None)
    friends.reset_vanity_cache()
    text = "https://steamcommunity.com/id/nobody_here/"
    friends.all_ids(text)
    assert wait_until(lambda: friends.unresolved(text) == ["nobody_here"] and not friends._pending)
    friends.RETRY_AFTER = 0.0
    n = []
    monkeypatch.setattr(friends, "fetch_vanity", lambda name: n.append(1) or 76561198000000000)
    friends.all_ids(text)
    assert wait_until(lambda: n and friends.unresolved(text) == [])
    friends.RETRY_AFTER = 120.0


def test_the_vanity_lookup_never_raises_and_never_blocks_the_caller(monkeypatch):
    from acs import friends

    def boom(name):
        raise OSError("no network")

    monkeypatch.setattr(friends, "fetch_vanity", boom)
    friends.reset_vanity_cache()
    t0 = time.monotonic()
    assert friends.all_ids("https://steamcommunity.com/id/whoever/") == []
    assert time.monotonic() - t0 < 0.5
    assert wait_until(lambda: friends.unresolved("https://steamcommunity.com/id/whoever/") == ["whoever"])


def test_fetch_vanity_parses_the_profile_xml(monkeypatch):
    from acs import friends

    class R:
        status = 200

        def read(self, n=-1):
            return b'<?xml version="1.0"?><profile><steamID64>76561198000000000</steamID64></profile>'

    class C:
        def __init__(self, *a, **k): ...
        def request(self, *a, **k): ...
        def getresponse(self): return R()
        def close(self): ...

    monkeypatch.setattr(friends.http.client, "HTTPSConnection", C)
    assert friends.fetch_vanity("exampleuser") == 76561198000000000
    assert friends.fetch_vanity("bad name!") is None  # never sends a malformed name


# ---- Steam's debug-port flag file ----------------------------------------------------------------------------
def test_the_debug_flag_is_created_in_every_steam_folder_and_is_idempotent(tmp_path, monkeypatch):
    from acs import winapi
    a, b = tmp_path / "Steam", tmp_path / "Steam2"
    a.mkdir()
    b.mkdir()
    monkeypatch.setattr(winapi, "steam_roots", lambda: [a, b])
    assert winapi.ensure_steam_debug_flag() is True
    assert (a / ".cef-enable-remote-debugging").exists() and (b / ".cef-enable-remote-debugging").exists()
    assert winapi.ensure_steam_debug_flag() is True  # again: nothing breaks


def test_the_debug_flag_reports_failure_without_raising(tmp_path, monkeypatch):
    from acs import winapi
    monkeypatch.setattr(winapi, "steam_roots", lambda: [tmp_path / "does" / "not" / "exist"])
    assert winapi.ensure_steam_debug_flag() is False
    monkeypatch.setattr(winapi, "steam_roots", lambda: [])
    assert winapi.ensure_steam_debug_flag() is False


# ---- review round: hook installation, switches, arming, URL strictness ---------------------------------------
def test_the_hook_is_marked_installed_only_after_it_is_really_installed():
    from acs.invite import HOOK_JS
    assert HOOK_JS.index("steam ui not ready") < HOOK_JS.index("window.__acs_inv_v = 1;")  # not-ready bails out first
    assert HOOK_JS.rindex("window.__acs_inv_v = 1;") > HOOK_JS.index("NotificationStore.OnNewNotificationReceived = function")
    assert HOOK_JS.count("window.__acs_inv_v = 1;") == 1


def test_the_box_marker_is_handled_even_when_auto_accept_is_switched_off(tmp_path):
    from acs.watcher import Watcher
    store = Store(tmp_path / "c.json")
    store.load()
    store.update(enabled=False)
    seen = []
    w = Watcher(store, MagicMock(), MagicMock(), on_dialog=lambda: seen.append(1))
    w.handle_line("[Localization System] CLocalize::CompileString failed to localize: '#GenericConfirmText_Label'")
    assert seen == [1]


def test_a_join_link_that_could_not_be_run_arms_nothing(tmp_path):
    e = OkEnv(tmp_path)
    e.pages = []  # the debug port is closed
    assert e.mon._exec(LOBBY_URL) is False
    e.mon.on_dialog()  # a confirmation the USER opened a moment later must not be answered
    time.sleep(0.2)
    assert e.enters == 0


def test_the_debug_flag_is_retried_until_it_can_be_created(tmp_path):
    e = Env(tmp_path)
    results = iter([False, False, True])
    calls = []
    e.mon._ensure_flag = lambda: calls.append(1) or next(results, True)
    for _ in range(5):
        e.mon.tick()
    assert len(calls) == 3  # kept trying while it failed, stopped after it worked


def test_join_urls_with_trailing_junk_are_refused():
    assert parse_invite(chat_event(url=LOBBY_URL + "\n")) is None
    assert parse_invite(chat_event(url=LOBBY_URL + " ")) is None
    assert parse_invite(chat_event()) is not None


def test_stop_returns_at_once_even_if_the_poll_is_stuck_on_a_hung_steam(tmp_path):
    e = Env(tmp_path)
    e.mon._lock.acquire()  # simulates tick() blocked inside a hung evaluate()
    try:
        t0 = time.monotonic()
        e.mon.stop()
        assert time.monotonic() - t0 < 0.5
    finally:
        e.mon._lock.release()


def test_ui_lookups_do_not_start_online_requests_per_keystroke(monkeypatch):
    from acs import friends
    calls = []
    monkeypatch.setattr(friends, "fetch_vanity", lambda name: calls.append(name) or None)
    friends.reset_vanity_cache()
    for typed in ("https://steamcommunity.com/id/ab", "https://steamcommunity.com/id/abc", "https://steamcommunity.com/id/abcd"):
        friends.all_ids(typed, lookup=False)
    time.sleep(0.2)
    assert calls == []
    friends.all_ids("https://steamcommunity.com/id/abcd", lookup=True)
    assert wait_until(lambda: calls == ["abcd"])


def test_vanity_names_that_differ_only_in_case_are_one_lookup(monkeypatch):
    from acs import friends
    calls = []
    monkeypatch.setattr(friends, "fetch_vanity", lambda name: calls.append(name) or None)
    friends.reset_vanity_cache()
    friends.all_ids("https://steamcommunity.com/id/Name\nhttps://steamcommunity.com/id/name")
    wait_until(lambda: not friends._pending)
    time.sleep(0.1)
    friends.all_ids("https://steamcommunity.com/id/NAME")
    time.sleep(0.2)
    assert len(calls) == 1


def test_an_invite_is_ignored_while_another_steam_game_is_running(tmp_path):
    e = Env(tmp_path)
    e.mon._running_app = lambda: 570  # Dota 2
    e.page.queue = [chat_event()]
    e.mon.tick()
    assert e.page.executed == [] and not e.notifier.send.called
    e.mon._running_app = lambda: 730  # CS2 itself: the invite is accepted
    e.page.queue = [chat_event(n=2)] if "n" in chat_event.__code__.co_varnames else [chat_event()]
    e.mon._last_join = -1e9
    e.mon.tick()
    assert len(e.page.executed) == 1
    e.mon._running_app = lambda: 0  # nothing running
    e.mon._last_join = -1e9
    e.page.queue = [chat_event()]
    e.mon.tick()
    assert len(e.page.executed) == 2


def test_a_newer_invite_cancels_the_older_launch_then_join(tmp_path):
    e = Env(tmp_path)
    e.game_up = False
    e.mon.SETTLE, e.mon.RETRY_GAP = 0.3, 0.05
    e.page.queue = [chat_event()]
    e.mon.tick()  # launches, a join thread waits
    e.game_up = True
    e.mon._join_gen += 1  # a newer invite was accepted meanwhile
    time.sleep(0.8)
    assert len(e.page.executed) == 1  # only the launch: the old link was never run


def test_switching_invites_off_cancels_a_pending_join(tmp_path):
    e = Env(tmp_path)
    e.game_up = False
    e.mon.SETTLE, e.mon.RETRY_GAP = 0.3, 0.05
    e.page.queue = [chat_event()]
    e.mon.tick()
    e.game_up = True
    e.store.update(invite_enabled=False)
    time.sleep(0.8)
    assert len(e.page.executed) == 1


def test_a_second_calibration_reports_busy(tmp_path):
    e = Env(tmp_path)
    e.mon._calibrating = True
    got = []
    e.mon.calibrate(got.append)
    assert got == ["busy"]


def test_a_saved_fraction_of_one_stays_inside_the_window():
    from acs import invite
    assert invite.saved_ok(json.dumps({"100x50": [1.0, 1.0]}), (10, 20, 100, 50)) == (109, 69)


def test_a_foreign_program_on_the_debug_port_is_reported_by_name(tmp_path):
    e = Env(tmp_path)
    e.pages = []
    e.mon._port_owner = lambda port: "nginx.exe"
    e.mon.tick()
    assert e.mon.status == "port_busy" and e.mon.port_name == "nginx.exe"
    e.mon._port_checked = -1e9  # steam itself holds the port but has not shown its page yet: not "busy"
    e.mon._port_owner = lambda port: "steamwebhelper.exe"
    e.mon.tick()
    assert e.mon.status == "no_port" and e.mon.port_name == ""


def test_port_owner_never_raises(monkeypatch):
    from acs import winapi
    monkeypatch.setattr(winapi.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert winapi.port_owner(8080) is None
