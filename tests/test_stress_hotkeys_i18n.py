import ast
import itertools
import re
from pathlib import Path

import pytest

from acs import hotkeys, i18n

VK = {"Ctrl": 0x11, "Alt": 0x12, "Shift": 0x10, "Win": 0x5B}
SRC = Path(__file__).resolve().parent.parent / "acs"


@pytest.fixture(autouse=True)
def reset_lang():
    yield
    i18n.set_language("en")


# ---------------------------------------------------------------- hotkeys

def all_mod_subsets():
    for r in range(5):
        for c in itertools.combinations(hotkeys._MOD_ORDER, r):
            yield frozenset(c)


def test_vk_codes_are_unique():
    assert len(set(hotkeys.KEYS.values())) == len(hotkeys.KEYS)


@pytest.mark.parametrize("key", list(hotkeys.KEYS))
def test_roundtrip_every_key_with_every_modifier_subset(key):
    for mods in all_mod_subsets():
        text = hotkeys.format_combo(mods, key)
        parsed = hotkeys.parse_combo(text)
        assert parsed == (mods, hotkeys.KEYS[key]), text
        assert hotkeys.normalize(text, "DEFAULT") == text


def test_normalize_reorders_modifiers_and_strips_spaces():
    assert hotkeys.normalize("Shift+Win+Alt+Ctrl+F8", "d") == "Ctrl+Alt+Shift+Win+F8"
    assert hotkeys.normalize(" Ctrl + F8 ", "d") == "Ctrl+F8"
    assert hotkeys.normalize("F8+Ctrl", "d") == "Ctrl+F8"


@pytest.mark.parametrize("text", ["", " ", "+", "++", "Ctrl", "Ctrl+", "Ctrl+Alt", "F8+F9", "A+B", "Ctrl+Ctrl+F8",
                                  "ctrl+f8", "CTRL+F8", "Ctrl+F25", "Ctrl+F0", "Ctrl+Esc", "Ctrl+a", "Meta+F8",
                                  "Ctrl+Ctrl", "Num10", "None", "Ctrl+Ё", "Ctrl+\x00", "F8 F9", "x" * 10000,
                                  None, [], b"F8"])
def test_invalid_combos_rejected_and_defaulted(text):
    assert hotkeys.parse_combo(text) is None
    assert hotkeys.normalize(text, "Ctrl+F8") == "Ctrl+F8"


def test_parse_combo_fuzz_never_raises():
    import random
    rng = random.Random(3)
    toks = ["Ctrl", "Alt", "Shift", "Win", "F1", "A", "+", " ", "", "Num5", "Space", "x", "++", "\n"]
    for _ in range(5000):
        text = "".join(rng.choice(toks) for _ in range(rng.randint(0, 8)))
        r = hotkeys.parse_combo(text)
        if r is not None:
            assert hotkeys.parse_combo(hotkeys.normalize(text, "Ctrl+F8")) == r


class Keys:
    def __init__(self, *down):
        self.down_set = set(down)

    def __call__(self, vk):
        return vk in self.down_set


def poller(bindings, keys):
    return hotkeys.HotkeyPoller(bindings, down=keys)


def test_poller_fires_once_per_press():
    k = Keys()
    p = poller({"cap": "Ctrl+F8"}, k)
    assert p.poll() == []
    k.down_set = {VK["Ctrl"]}
    assert p.poll() == []
    k.down_set = {VK["Ctrl"], hotkeys.KEYS["F8"]}
    assert p.poll() == ["cap"]
    for _ in range(50):
        assert p.poll() == []  # held: edge triggered
    k.down_set = {VK["Ctrl"]}
    assert p.poll() == []
    k.down_set = {VK["Ctrl"], hotkeys.KEYS["F8"]}
    assert p.poll() == ["cap"]


def test_poller_requires_exact_modifiers():
    k = Keys(hotkeys.KEYS["F8"])
    p = poller({"cap": "Ctrl+F8", "bare": "F8"}, k)
    assert p.poll() == ["bare"]  # F8 alone does not trigger Ctrl+F8
    k.down_set = {VK["Ctrl"], VK["Shift"], hotkeys.KEYS["F8"]}
    assert p.poll() == []  # extra modifier: neither binding
    k.down_set = {VK["Ctrl"], hotkeys.KEYS["F8"]}
    assert p.poll() == ["cap"]


def test_poller_modifier_added_then_removed_retriggers():
    k = Keys(VK["Ctrl"], hotkeys.KEYS["F8"])
    p = poller({"cap": "Ctrl+F8"}, k)
    assert p.poll() == ["cap"]
    k.down_set.add(VK["Shift"])
    assert p.poll() == []
    k.down_set.discard(VK["Shift"])
    assert p.poll() == ["cap"]  # documented edge semantics: becoming active again is a new press


def test_poller_win_key_either_side_and_two_bindings():
    k = Keys(0x5C, hotkeys.KEYS["A"], hotkeys.KEYS["B"])
    p = poller({"one": "Win+A", "two": "Win+B", "other": "Win+C"}, k)
    assert sorted(p.poll()) == ["one", "two"]
    assert p.poll() == []


def test_poller_same_combo_fires_both_bindings():
    k = Keys(hotkeys.KEYS["F5"])
    assert sorted(poller({"a": "F5", "b": "F5"}, k).poll()) == ["a", "b"]


def test_poller_ignores_invalid_bindings():
    k = Keys(hotkeys.KEYS["F5"])
    p = poller({"bad": "nonsense", "empty": "", "ok": "F5"}, k)
    assert list(p.bindings) == ["ok"] and p.poll() == ["ok"]
    assert poller({}, k).poll() == []


def test_poller_thousand_polls_with_random_keys_never_raises():
    import random
    rng = random.Random(9)
    allv = list(hotkeys.KEYS.values()) + list(VK.values()) + [0x5C]
    k = Keys()
    p = poller({"a": "Ctrl+F8", "b": "Ctrl+F9", "c": "Alt+Shift+Z"}, k)
    for _ in range(1000):
        k.down_set = set(rng.sample(allv, rng.randint(0, 5)))
        for name in p.poll():
            assert name in p.bindings


def test_record_step_variants():
    assert hotkeys.record_step(Keys()) is None
    assert hotkeys.record_step(Keys(VK["Ctrl"])) is None  # modifier alone is not a combo
    assert hotkeys.record_step(Keys(VK["Ctrl"], VK["Shift"], hotkeys.KEYS["F8"])) == "Ctrl+Shift+F8"
    assert hotkeys.record_step(Keys(0x5C, hotkeys.KEYS["Q"])) == "Win+Q"
    assert hotkeys.record_step(Keys(hotkeys.KEYS["Num3"])) == "Num3"
    assert hotkeys.record_step(Keys(0x1B)) == ""  # Esc cancels
    assert hotkeys.record_step(Keys(0x1B, VK["Ctrl"], hotkeys.KEYS["F8"])) == ""  # Esc wins


@pytest.mark.parametrize("key", list(hotkeys.KEYS))
def test_record_step_result_is_parseable_for_every_key(key):
    res = hotkeys.record_step(Keys(hotkeys.KEYS[key], VK["Alt"]))
    assert res == f"Alt+{key}" and hotkeys.parse_combo(res)


# ---------------------------------------------------------------- i18n

PH = re.compile(r"{(\w*)}")


def test_same_keys_in_both_languages():
    assert set(i18n.EN) == set(i18n.UK)


def test_placeholders_identical_and_translations_nonempty():
    for k in i18n.EN:
        assert set(PH.findall(i18n.EN[k])) == set(PH.findall(i18n.UK[k])), k
        assert i18n.EN[k].strip() and i18n.UK[k].strip(), k
        assert "" not in PH.findall(i18n.EN[k]), f"positional placeholder in {k}"


def test_no_unbalanced_braces_in_any_text():
    for table in (i18n.EN, i18n.UK):
        for v in table.values():
            v.format(**{n: 1 for n in PH.findall(v)})  # must not raise ValueError


@pytest.mark.parametrize("lang", ["en", "uk"])
def test_tr_never_raises_for_any_key_and_kwargs(lang):
    i18n.set_language(lang)
    weird = [None, "x", 5, 1.5, object(), [], {}]
    for k in i18n.EN:
        names = PH.findall(i18n.EN[k])
        assert isinstance(i18n.tr(k), str)  # missing placeholders
        assert isinstance(i18n.tr(k, unrelated=1), str)
        for w in weird:
            assert isinstance(i18n.tr(k, **{n: w for n in names}), str)
        if names:
            full = i18n.tr(k, **{n: "VAL" for n in names})
            assert "VAL" in full and "{" not in full


def test_tr_unknown_and_odd_keys():
    for k in ("", "nope", "a.b.c", "{x}", "btn.save ", "BTN.SAVE"):
        assert i18n.tr(k) == k
    assert i18n.tr("nope", x=1) == "nope"


def test_language_switching_many_times():
    for i in range(500):
        got = i18n.set_language("uk" if i % 2 else "en")
        assert got == i18n.language() == ("uk" if i % 2 else "en")
    assert i18n.tr("btn.save") != i18n.EN["btn.save"] or i18n.EN["btn.save"] == i18n.UK["btn.save"]
    assert i18n.resolve("de") in ("en", "uk") and i18n.resolve("") in ("en", "uk")


def test_uk_actually_differs_from_en():
    same = [k for k in i18n.EN if i18n.EN[k] == i18n.UK[k]]
    assert len(same) < len(i18n.EN) * 0.2, same


def _python_sources():
    return sorted(SRC.glob("*.py"))


def test_every_literal_tr_key_in_source_exists():
    pat = re.compile(r"""\btr\(\s*f?(["'])([^"']+)\1""")
    missing, used = [], 0
    for f in _python_sources():
        if f.name == "i18n.py":
            continue
        for m in pat.finditer(f.read_text(encoding="utf-8")):
            key = m.group(2)
            if "{" in key or key.endswith("."):  # dynamic key (prefix + variable), covered below
                continue
            used += 1
            if key not in i18n.EN:
                missing.append((f.name, key))
    assert used > 10
    assert not missing, missing


def test_every_i18n_key_literal_passed_to_ui_helpers_exists():
    """ui.py passes keys through _label/_button/_row/_page/_section/_bind helpers instead of calling tr() directly."""
    prefixes = {k.split(".")[0] for k in i18n.EN}
    missing = []
    for f in _python_sources():
        if f.name == "i18n.py":
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                v = node.value
                if re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", v) and v.split(".")[0] in prefixes and v not in i18n.EN:
                    missing.append((f.name, node.lineno, v))
    assert not missing, missing


def test_dynamic_keys_exist():
    for state in ("on", "off", "nolog"):
        for k in (f"state.{state}", f"hero.{state}", f"hero.{state}_sub"):
            assert k in i18n.EN, k
    for k in ("st.cs2_on", "st.cs2_off", "cal.stop", "cal.start"):
        assert k in i18n.EN


def test_nav_keys_exist():
    from acs import ui  # noqa: F401  (import only; Qt is not instantiated)
    for key, _ic in ui.NAV:
        assert key in i18n.EN
