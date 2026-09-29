"""Счётчик ошибок смотрит на код, а не на сам метод value."""

from seeding_engine.torrent_runtime import _error_code_value


class _Code:
    def __init__(self, code: int) -> None:
        self._code = code

    def value(self) -> int:
        return self._code


def test_error_code_value_calls_method():
    assert _error_code_value(_Code(0)) == 0
    assert _error_code_value(_Code(2)) == 2


def test_error_code_value_reads_plain_number():
    class Plain:
        value = 7

    assert _error_code_value(Plain()) == 7
    assert _error_code_value(None) == 0
