"""Пустой httpx.RequestError не должен превращаться в «upstream error: » без текста."""

from seeding_upload_relay.main import format_upstream_error


class _Empty(Exception):
    def __str__(self) -> str:
        return ""


def test_empty_exception_has_fallback_text():
    text = format_upstream_error(_Empty())
    assert "upstream error" not in text
    assert "connection dropped" in text
    assert "_Empty" in text


def test_message_and_cause_are_kept():
    try:
        raise RuntimeError("reset by peer") from OSError("EBADF")
    except RuntimeError as exc:
        text = format_upstream_error(exc)
    assert "RuntimeError" in text
    assert "reset by peer" in text
    assert "EBADF" in text
