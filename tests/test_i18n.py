import re

from acs import i18n


def test_both_languages_have_same_keys_and_placeholders():
    assert set(i18n.EN) == set(i18n.UK)
    ph = re.compile(r"{(\w+)}")
    for k in i18n.EN:
        assert set(ph.findall(i18n.EN[k])) == set(ph.findall(i18n.UK[k])), k


def test_tr_fallback_and_format():
    i18n.set_language("uk")
    try:
        assert i18n.tr("btn.save") == "Зберегти"
        assert i18n.tr("msg.ping", ping=5) == "📍 Пінг: 5 мс"
        assert i18n.tr("missing.key") == "missing.key"
    finally:
        i18n.set_language("en")
    assert i18n.tr("msg.ping", ping=5) == "📍 Ping: 5 ms"
