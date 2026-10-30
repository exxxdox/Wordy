from PySide6.QtCore import QPoint

from wordy.ui import chat_wheel
from wordy.ui.chat_wheel import ChatWheel


def test_fade_in_and_release_never_delay_playback(qapp, monkeypatch):
    monkeypatch.setattr(chat_wheel.QCursor, "pos", lambda: QPoint(500, 500))
    selected = []
    wheel = ChatWheel(selected.append)
    wheel.open_wheel(["Audio"] * 8)
    assert wheel._opacity == 0.0
    wheel._fade.setCurrentTime(60)
    assert 0.0 < wheel._opacity < 1.0
    wheel._fade.setCurrentTime(120)
    assert wheel._opacity == 1.0
    wheel._selected = 2
    wheel._commit()
    assert selected == [2]
    assert wheel.isVisible()
    assert not wheel.is_active()
    assert not wheel._timer.isActive()
    assert wheel._origin is None
    wheel._commit()
    wheel._poll()
    assert selected == [2]
    wheel._fade.setCurrentTime(45)
    assert 0.0 < wheel._opacity < 1.0
    wheel._fade.setCurrentTime(90)
    assert not wheel.isVisible()


def test_reopen_during_fade_out_cannot_hide_new_selection(qapp, monkeypatch):
    monkeypatch.setattr(chat_wheel.QCursor, "pos", lambda: QPoint(500, 500))
    wheel = ChatWheel(lambda _: None)
    wheel.open_wheel(["Old"] * 8)
    wheel._fade.setCurrentTime(120)
    wheel.dismiss()
    wheel._fade.setCurrentTime(45)
    opacity = wheel._opacity
    wheel.open_wheel(["New"] * 8)
    assert wheel.is_active()
    assert wheel._labels == ["New"] * 8
    assert wheel._opacity == opacity
    wheel._fade.setCurrentTime(120)
    assert wheel.is_active()
    assert wheel._opacity == 1.0
    wheel.cancel()


def test_force_cancel_stops_animation_without_reopening(qapp, monkeypatch):
    monkeypatch.setattr(chat_wheel.QCursor, "pos", lambda: QPoint(500, 500))
    wheel = ChatWheel(lambda _: None)
    wheel.open_wheel(["Audio"] * 8)
    wheel._fade.setCurrentTime(60)
    wheel.cancel()
    assert not wheel.isVisible()
    assert wheel._opacity == 0.0
    assert not wheel._timer.isActive()
    assert wheel._fade.state() == wheel._fade.State.Stopped
