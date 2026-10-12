import json
import logging
from decimal import Decimal

import pytest

from app.core.logging import JsonFormatter, redact
from app.core.money import D, bps, floor_to_step, round_to_tick


def test_D_avoids_binary_float_error() -> None:
    assert D(0.1) == Decimal("0.1")
    assert D("82444.01") == Decimal("82444.01")
    with pytest.raises(ValueError):
        D(float("nan"))


def test_floor_to_step_never_rounds_up() -> None:
    assert floor_to_step(Decimal("0.0030799"), Decimal("0.00001")) == Decimal("0.00307")
    assert floor_to_step(Decimal("1.99999"), Decimal("0.0001")) == Decimal("1.9999")


def test_round_to_tick_directions() -> None:
    tick = Decimal("0.01")
    assert round_to_tick(Decimal("100.016"), tick, direction="down") == Decimal("100.01")
    assert round_to_tick(Decimal("100.011"), tick, direction="up") == Decimal("100.02")
    assert round_to_tick(Decimal("100.015"), tick) == Decimal("100.02")


def test_bps() -> None:
    assert bps(Decimal(2)) == Decimal("0.0002")


def test_redact_nested_and_inline() -> None:
    data = {"api_key": "AAA", "nested": {"password": "x", "ok": 1}, "url": "a?signature=zzz&x=1"}
    out = redact(data)
    assert out["api_key"] == "***"
    assert out["nested"] == {"password": "***", "ok": 1}
    assert "zzz" not in out["url"]
    assert redact("postgresql://u:secretpw@db/x") == "postgresql://u:***@db/x"


def test_json_formatter_redacts_extra() -> None:
    rec = logging.LogRecord("t", logging.INFO, "", 0, "hola token=abc", None, None)
    rec.api_secret = "s3cr3t"
    line = json.loads(JsonFormatter().format(rec))
    assert line["msg"] == "hola token=***"
    assert line["api_secret"] == "***"
