from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.core.domain import DataQuality
from app.market_data.validation import SeriesValidator, check_candle, check_series, find_gaps
from app.providers.binance.parsing import (
    epoch_to_utc,
    parse_book_ticker,
    parse_instrument_rules,
    parse_kline_row,
    parse_ws_kline,
)
from tests.factories import T0, candle, series, ws_kline

MIN = timedelta(minutes=1)


# ------------------------------------------------------------------ parsing


def test_epoch_ms_and_micros() -> None:
    assert epoch_to_utc(1785542400000) == datetime(2026, 8, 1, tzinfo=UTC)
    assert epoch_to_utc(1785542400000000) == datetime(2026, 8, 1, tzinfo=UTC)
    assert epoch_to_utc("1785542459999999") == datetime(2026, 8, 1, 0, 0, 59, 999999, tzinfo=UTC)


def test_parse_rest_and_archive_rows() -> None:
    rest = [
        1785542400000,
        "62887.88",
        "62900.00",
        "62887.87",
        "62893.06",
        "7.71179000",
        1785542459999,
        "485038.09",
        873,
        "1.18",
        "74456.29",
        "0",
    ]
    archive = [
        "1785542400000000",
        "62887.88000000",
        "62900.00000000",
        "62887.87000000",
        "62893.06000000",
        "7.71179000",
        "1785542459999999",
        "485038.09592460",
        "873",
        "1.18384000",
        "74456.29821520",
        "0",
    ]
    a = parse_kline_row("BTCUSDT", "1m", rest, source="r")
    b = parse_kline_row("BTCUSDT", "1m", archive, source="a")
    assert a.open_time == b.open_time == datetime(2026, 8, 1, tzinfo=UTC)
    assert a.close == Decimal("62893.06") and b.close == Decimal("62893.06000000")
    assert a.is_closed and b.is_closed
    assert check_candle(a).accepted and check_candle(b).accepted


def test_rest_row_still_open_is_not_closed() -> None:
    now = datetime(2026, 8, 1, 0, 0, 30, tzinfo=UTC)
    row = [1785542400000, "1", "1", "1", "1", "1", 1785542459999, "1", 1, "0", "0", "0"]
    c = parse_kline_row("BTCUSDT", "1m", row, source="r", now=now)
    assert not c.is_closed
    assert check_candle(c).quality is DataQuality.OPEN


def test_parse_ws_kline_respects_closed_flag() -> None:
    c = candle(T0)
    assert parse_ws_kline(ws_kline(c)["data"]).is_closed
    assert not parse_ws_kline(ws_kline(c, closed=False)["data"]).is_closed


def test_parse_book_ticker() -> None:
    q = parse_book_ticker({"u": 1, "s": "BTCUSDT", "b": "100.0", "B": "1", "a": "100.5", "A": "2"})
    assert q.spread == Decimal("0.5") and q.mid == Decimal("100.25")


def test_parse_instrument_rules_from_live_format() -> None:
    info = {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "filters": [
            {
                "filterType": "PRICE_FILTER",
                "minPrice": "0.01",
                "maxPrice": "1000000",
                "tickSize": "0.01000000",
            },
            {
                "filterType": "LOT_SIZE",
                "minQty": "0.00001000",
                "maxQty": "9000",
                "stepSize": "0.00001000",
            },
            {
                "filterType": "NOTIONAL",
                "minNotional": "5.00000000",
                "applyMinToMarket": True,
                "maxNotional": "9000000",
                "applyMaxToMarket": False,
                "avgPriceMins": 5,
            },
        ],
    }
    r = parse_instrument_rules(info)
    assert r.tick_size == Decimal("0.01") and r.step_size == Decimal("0.00001")
    assert r.min_notional == Decimal("5")


# ------------------------------------------------------------------ vela individual


def test_check_candle_detects_problems() -> None:
    assert check_candle(candle(T0 + timedelta(seconds=30))).quality is DataQuality.INVALID
    assert check_candle(candle(T0, o="100", h="99", l="98", c="100")).quality is DataQuality.INVALID
    assert check_candle(candle(T0, o="0", h="1", l="0", c="1")).quality is DataQuality.INVALID
    assert check_candle(candle(T0, v="-1")).quality is DataQuality.INVALID
    naive = candle(T0).__class__(**{**_fields(candle(T0)), "open_time": T0.replace(tzinfo=None)})
    assert check_candle(naive).quality is DataQuality.INVALID
    assert check_candle(candle(T0, closed=False)).quality is DataQuality.OPEN


def _fields(c: object) -> dict[str, object]:
    return {k: getattr(c, k) for k in c.__slots__}  # type: ignore[attr-defined]


# ------------------------------------------------------------------ series en vivo


def test_series_validator_duplicates_order_and_gaps() -> None:
    v = SeriesValidator("BTCUSDT", "1m")
    c0, c1, c2, c3 = series(T0, 4)
    assert v.feed(c0).accepted
    same = v.feed(c0)
    assert same.quality is DataQuality.DUPLICATE and not same.conflict
    conflicting = v.feed(candle(T0, o="999", c="999"))
    assert conflicting.quality is DataQuality.DUPLICATE and conflicting.conflict
    assert v.feed(c1).accepted
    assert v.feed(c0).quality is DataQuality.OUT_OF_ORDER
    # c2 se pierde: llega c3 → hueco de una vela
    res = v.feed(c3)
    assert res.accepted and res.gap is not None
    assert res.gap.start == c2.open_time and res.gap.end == c2.open_time
    assert res.gap.missing_count() == 1


def test_open_candle_does_not_advance_series() -> None:
    v = SeriesValidator("BTCUSDT", "1m")
    c0, c1 = series(T0, 2)
    v.feed(c0)
    assert v.feed(candle(c1.open_time, closed=False)).quality is DataQuality.OPEN
    assert v.last == c0


def test_readiness_blocks_on_gap_stale_and_invalid() -> None:
    v = SeriesValidator("BTCUSDT", "1m")
    stale = timedelta(seconds=150)
    assert v.readiness(T0, stale) == (False, "sin datos")
    c = series(T0, 5)
    v.feed(c[0])
    now = c[0].close_time + timedelta(seconds=1)
    assert v.readiness(now, stale)[0]
    assert not v.readiness(now + timedelta(minutes=10), stale)[0]
    v.feed(c[2])  # hueco
    ok, reason = v.readiness(c[2].close_time, stale)
    assert not ok and "hueco" in reason
    v.resolve_gaps_until(c[1].open_time)
    assert v.readiness(c[2].close_time, stale)[0]
    v.feed(candle(c[3].open_time, o="5", h="1", l="1", c="5"))  # inválida
    assert not v.readiness(c[2].close_time, stale)[0]


def test_wrong_symbol_rejected() -> None:
    v = SeriesValidator("BTCUSDT", "1m")
    assert v.feed(candle(T0, symbol="ETHUSDT")).quality is DataQuality.INVALID


# ------------------------------------------------------------------ auditoría de lotes


def test_check_series_report() -> None:
    s = series(T0, 10)
    batch = [*s[:3], s[2], *s[5:], s[4], candle(T0 + 20 * MIN, closed=False)]
    rep = check_series(batch, start=T0, end=T0 + 11 * MIN)
    assert rep.duplicates == 1
    assert rep.out_of_order == 1
    assert rep.open_candles == 1
    # Falta s[3] (minuto 3) y los minutos 10-11 al final del rango
    assert [(g.start, g.end) for g in rep.gaps] == [
        (T0 + 3 * MIN, T0 + 3 * MIN),
        (T0 + 10 * MIN, T0 + 11 * MIN),
    ]
    assert not rep.clean


def test_find_gaps_edges() -> None:
    assert find_gaps([], "X", "1m", start=T0, end=T0 + 2 * MIN)[0].missing_count() == 3
    assert find_gaps([T0, T0 + MIN], "X", "1m", start=T0, end=T0 + MIN) == []
    g = find_gaps([T0 + 2 * MIN], "X", "1m", start=T0, end=T0 + 2 * MIN)
    assert len(g) == 1 and g[0].missing_count() == 2
