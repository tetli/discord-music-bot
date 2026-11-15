import sys
import types

from musicbot.main import DaveLogFilter, davey_is_incompatible
import logging


def install_fake_davey(monkeypatch, *, has_transition_api: bool):
    session = type("DaveSession", (), {})
    if has_transition_api:
        session.prepare_transition = lambda self, transition_id, protocol_version: None
    monkeypatch.setitem(sys.modules, "davey", types.SimpleNamespace(DaveSession=session))


def test_old_davey_is_compatible(monkeypatch):
    install_fake_davey(monkeypatch, has_transition_api=False)
    assert not davey_is_incompatible()


def test_new_davey_without_discordpy_support_is_flagged(monkeypatch):
    install_fake_davey(monkeypatch, has_transition_api=True)
    monkeypatch.setattr("musicbot.main.inspect.getsource", lambda module: "no transition calls here")
    assert davey_is_incompatible()


def test_new_davey_with_discordpy_support_is_fine(monkeypatch):
    install_fake_davey(monkeypatch, has_transition_api=True)
    monkeypatch.setattr("musicbot.main.inspect.getsource", lambda module: "session.prepare_transition(tid, v)")
    assert not davey_is_incompatible()


def test_installed_versions_are_compatible():
    assert not davey_is_incompatible()


def record(level, msg):
    return logging.LogRecord("discord.gateway", level, "", 0, msg, None, None)


def test_dave_log_filter_blocks_frame_dumps():
    f = DaveLogFilter()
    assert not f.filter(record(logging.DEBUG, "Sending voice websocket frame: %s."))
    assert not f.filter(record(logging.DEBUG, "Voice websocket frame received: %s"))
    assert f.filter(record(logging.DEBUG, "MLS welcome processed for transition id %d"))
    assert f.filter(record(logging.INFO, "Shard ID %s has connected to Gateway"))
