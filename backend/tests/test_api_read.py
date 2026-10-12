from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.core.domain import utcnow
from app.persistence import db
from app.persistence import models as m
from tests.api_helpers import (
    OLD,
    READ_ENDPOINTS,
    client_as,
    seed_candles,
    seed_closed_and_open_trade,
)


@pytest.mark.parametrize("path", READ_ENDPOINTS)
async def test_every_read_endpoint_requires_authentication(
    client: httpx.AsyncClient, path: str
) -> None:
    r = await client.get(path)
    assert r.status_code == 401


async def test_health_stays_public(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health")).status_code == 200


# ------------------------------------------------------------------ sin datos: nada inventado


async def test_overview_without_any_data_shows_unavailable_not_zeros(
    admin_client: httpx.AsyncClient,
) -> None:
    r = await admin_client.get("/api/dashboard/overview")
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] == "PAPER" and "simulada" in d["data_origin"]
    assert d["engine"]["status"] == "never_started" and d["engine"]["last_beat_at"] is None
    assert d["account"] is None and d["drawdown"] is None  # ni 0 ni 500 inventados
    assert d["open_positions"] is None
    assert d["connections"] == [] and d["last_data_at"] is None
    assert d["risk"]["state"] is None and d["risk"]["usage"] is None
    assert d["risk"]["limits"]["leverage"] == 1 and d["risk"]["limits"]["long_only_spot"] is True


async def test_markets_without_candles_say_no_data(admin_client: httpx.AsyncClient) -> None:
    d = (await admin_client.get("/api/markets")).json()
    assert [x["symbol"] for x in d["markets"]] == ["BTCUSDT", "ETHUSDT"]
    for mk in d["markets"]:
        assert mk["price"] is None and mk["data_status"] == "sin_datos"
        assert mk["change_24h_pct"] is None and mk["volume_24h"] is None
        assert mk["volatility_1h_pct"] is None


# ------------------------------------------------------------------ con datos


async def test_overview_with_trades_matches_the_ledger(admin_client: httpx.AsyncClient) -> None:
    h = await seed_closed_and_open_trade()
    await seed_candles()
    await h.manager.adapter.balances()
    d = (await admin_client.get("/api/dashboard/overview")).json()
    acct = d["account"]
    assert Decimal(acct["initial_capital"]) == 500
    assert d["closed_trades"] == 1 and d["open_positions"] == 1
    assert len(d["recent_trades"]) == 1 and Decimal(d["recent_trades"][0]["net_pnl"]) < 0
    # equity = caja + valor de mercado de lo abierto (ETH sin vela: valorada a su entrada)
    snap = await h.snapshot()
    assert Decimal(acct["realized_pnl"]) == snap.realized_pnl < 0
    assert Decimal(acct["cash"]) == snap.cash
    assert (
        d["drawdown"]["limit"] == "25.000000000000000000" or Decimal(d["drawdown"]["limit"]) == 25
    )
    assert acct["marks_stale"] is False


async def test_stale_marks_are_flagged(admin_client: httpx.AsyncClient) -> None:
    await seed_closed_and_open_trade()
    await seed_candles(end=OLD)
    d = (await admin_client.get("/api/dashboard/overview")).json()
    assert d["account"]["marks_stale"] is True
    mk = (await admin_client.get("/api/markets?symbol=BTCUSDT")).json()["markets"][0]
    assert mk["data_status"] == "obsoleto"


async def test_markets_with_fresh_candles_and_filters(admin_client: httpx.AsyncClient) -> None:
    await seed_candles()
    async with db.session_scope() as s:
        s.add(
            m.Strategy(id="momentum", name="M", enabled=True, symbols=["BTCUSDT"], interval="1m",
                       params={}, version="1")
        )  # fmt: skip
    d = (await admin_client.get("/api/markets?symbol=btcusdt")).json()
    assert len(d["markets"]) == 1
    mk = d["markets"][0]
    assert mk["data_status"] == "al_dia" and Decimal(mk["price"]) == 80004
    assert mk["strategies"] == ["momentum"]
    assert mk["change_24h_pct"] is None  # menos de medio día de velas: no se extrapola
    none = (await admin_client.get("/api/markets?strategy=otra")).json()
    assert none["markets"] == []


async def test_candles_endpoint(admin_client: httpx.AsyncClient) -> None:
    await seed_candles(30)
    d = (await admin_client.get("/api/markets/BTCUSDT/candles?limit=20")).json()
    assert len(d["candles"]) == 20
    ts = [c["t"] for c in d["candles"]]
    assert ts == sorted(ts) and d["interval"] == "1m"
    assert (await admin_client.get("/api/markets/DOGEUSDT/candles")).status_code == 404
    assert (await admin_client.get("/api/markets/BTCUSDT/candles?limit=5")).status_code == 422
    assert (await admin_client.get("/api/markets/BTCUSDT/candles?interval=1h")).status_code == 422


async def test_trading_lists_filters_and_pagination(admin_client: httpx.AsyncClient) -> None:
    await seed_closed_and_open_trade()
    orders = (await admin_client.get("/api/orders?limit=2&offset=0")).json()
    assert orders["total"] == 3 and len(orders["items"]) == 2 and orders["limit"] == 2
    page2 = (await admin_client.get("/api/orders?limit=2&offset=2")).json()
    assert len(page2["items"]) == 1
    ids = {o["id"] for o in orders["items"]} | {o["id"] for o in page2["items"]}
    assert len(ids) == 3  # sin solapes entre páginas
    sells = (await admin_client.get("/api/orders?status=FILLED&symbol=btcusdt")).json()
    assert sells["total"] == 2
    assert (await admin_client.get("/api/orders?pending=true")).json()["total"] == 0

    pos_open = (await admin_client.get("/api/positions")).json()
    assert pos_open["total"] == 1 and pos_open["items"][0]["symbol"] == "ETHUSDT"
    assert (await admin_client.get("/api/positions?status=ALL")).json()["total"] == 2

    trades = (await admin_client.get("/api/trades")).json()
    t = trades["items"][0]
    assert trades["total"] == 1 and Decimal(t["net_pnl"]) < 0 and t["exit_reason"] == "stop"
    assert Decimal(t["gross_pnl"]) - Decimal(t["fees"]) == Decimal(t["net_pnl"])
    assert (await admin_client.get("/api/trades?outcome=win")).json()["total"] == 0
    assert (await admin_client.get("/api/trades?outcome=loss")).json()["total"] == 1
    future = (utcnow() + timedelta(days=1)).isoformat()
    assert (await admin_client.get("/api/trades", params={"from": future})).json()["total"] == 0

    sig = (await admin_client.get("/api/signals?strategy=test-strategy")).json()
    assert sig["total"] == 2 and all(i["risk_approved"] is True for i in sig["items"])


@pytest.mark.parametrize(
    "query",
    [
        "/api/orders?limit=1000",
        "/api/orders?limit=0",
        "/api/orders?offset=-1",
        "/api/orders?symbol=BTC';DROP TABLE orders;--",
        "/api/trades?outcome=maybe",
        "/api/signals?status=bad status",
        "/api/positions?status=ZZZ",
        "/api/orders?from=not-a-date",
    ],
)
async def test_invalid_query_parameters_are_rejected(
    admin_client: httpx.AsyncClient, query: str
) -> None:
    assert (await admin_client.get(query)).status_code == 422


async def test_order_detail_has_full_traceability_chain(admin_client: httpx.AsyncClient) -> None:
    await seed_closed_and_open_trade()
    first = (await admin_client.get("/api/orders?symbol=BTCUSDT&status=FILLED")).json()["items"]
    entry = next(o for o in first if o["purpose"] == "ENTRY")
    d = (await admin_client.get(f"/api/orders/{entry['id']}")).json()
    assert [e["to"] for e in d["events"]] == ["CREATED", "VALIDATED", "SUBMITTED", "FILLED"]
    assert d["signal"]["id"] == entry["signal_id"] and d["signal"]["risk_approved"] is True
    assert len(d["fills"]) == 1
    assert (await admin_client.get("/api/orders/not-a-uuid")).status_code == 422
    import uuid

    assert (await admin_client.get(f"/api/orders/{uuid.uuid4()}")).status_code == 404


async def test_performance_is_net_excludes_open_and_warns_on_small_sample(
    admin_client: httpx.AsyncClient,
) -> None:
    await seed_closed_and_open_trade()
    d = (await admin_client.get("/api/performance")).json()
    assert d["overall"]["trades"] == 1 and d["open_positions_excluded"] == 1
    assert d["overall"]["win_rate"] == 0.0
    assert Decimal(d["overall"]["profit_factor"]) == 0  # solo pérdidas ⇒ PF 0, no None
    assert Decimal(d["overall"]["net_pnl"]) < 0
    assert "1 operación cerrada" in d["sample_warning"]
    assert set(d["by_strategy"]) == {"test-strategy"}


async def test_performance_without_trades_has_no_made_up_ratios(
    admin_client: httpx.AsyncClient,
) -> None:
    d = (await admin_client.get("/api/performance")).json()
    o = d["overall"]
    assert o["trades"] == 0 and o["win_rate"] is None and o["expectancy_net"] is None
    assert o["profit_factor"] is None and d["max_drawdown_observed"] is None


async def test_risk_endpoint_is_read_only_and_reports_usage(
    admin_client: httpx.AsyncClient,
) -> None:
    await seed_closed_and_open_trade()
    d = (await admin_client.get("/api/risk")).json()
    assert d["editable"] is False
    assert d["state"]["entries_allowed"] is True and d["state"]["entries_today"] == 2
    assert Decimal(d["usage"]["daily_loss"]) > 0
    assert Decimal(d["limits"]["risk_per_trade"]) == Decimal("1.25")
    assert d["recent_decisions"]
    for verb in ("put", "patch", "post", "delete"):
        r = await admin_client.request(verb.upper(), "/api/risk", json={"max_drawdown_pct": 99})
        assert r.status_code in (404, 405)


async def test_strategies_catalog_and_detail(admin_client: httpx.AsyncClient) -> None:
    async with db.session_scope() as s:
        s.add(
            m.Strategy(id="momentum", name="Momentum", enabled=False, symbols=["BTCUSDT"],
                       interval="1m", params={"x": 1}, version="1.0")
        )  # fmt: skip
        s.add(
            m.BacktestRun(strategy_id="momentum", strategy_version="1.0", symbols=["BTCUSDT"],
                          interval="1m", start=OLD, end=OLD, segment="test", params={}, cost_model={},
                          data_hash="abc", status="DONE", metrics={"net_pnl": -3.2},
                          notes="DESCARTADA: sin ventaja")
        )  # fmt: skip
    lst = (await admin_client.get("/api/strategies")).json()
    assert lst["strategies"][0]["enabled"] is False
    assert lst["strategies"][0]["last_backtest_verdict"] == "DESCARTADA"
    assert "ninguna se considera rentable" in lst["disclaimer"]
    d = (await admin_client.get("/api/strategies/momentum")).json()
    assert d["params"] == {"x": 1} and d["params_editable"] is False
    assert d["out_of_sample"][0]["metrics"]["net_pnl"] == -3.2
    assert d["live_stats"]["trades"] == 0 and d["live_stats"]["win_rate"] is None
    assert (await admin_client.get("/api/strategies/nope")).status_code == 404


async def test_backtests_listing_and_detail(admin_client: httpx.AsyncClient) -> None:
    async with db.session_scope() as s:
        run = m.BacktestRun(
            strategy_id="trend", strategy_version="1", symbols=["BTCUSDT"], interval="1m",
            start=OLD, end=OLD, segment="full", params={}, cost_model={"taker_fee": "0.001"},
            data_hash="h", seed=7, status="DONE", metrics={}, notes="",
        )  # fmt: skip
        s.add(run)
        await s.flush()
        run_id = run.id
    lst = (await admin_client.get("/api/backtests?strategy=trend")).json()
    assert lst["total"] == 1 and lst["items"][0]["seed"] == 7
    d = (await admin_client.get(f"/api/backtests/{run_id}")).json()
    assert d["data_hash"] == "h" and "reproducibility" in d
    assert (await admin_client.get("/api/backtests?strategy=x y")).status_code == 422


async def test_system_status_never_enables_real_money(admin_client: httpx.AsyncClient) -> None:
    d = (await admin_client.get("/api/system/status")).json()
    assert d["real_money_enabled"] is False and d["mode"] == "PAPER"
    assert set(d["components"]) == {"api", "database", "engine", "market_data"}
    assert d["engine"]["status"] == "never_started"


async def test_alerts_listing_and_filters(admin_client: httpx.AsyncClient) -> None:
    async with db.session_scope() as s:
        from app.core.domain import Severity
        from app.monitoring.alerts import raise_alert, resolve_alert

        await raise_alert(s, severity=Severity.CRITICAL, code="a", message="crítica",
                          recommended_action="haz algo", dedup_key="a")  # fmt: skip
        await raise_alert(s, severity=Severity.WARNING, code="b", message="aviso",
                          recommended_action="mira", dedup_key="b")  # fmt: skip
        await resolve_alert(s, "b")
    active = (await admin_client.get("/api/alerts")).json()
    assert active["total"] == 1 and active["items"][0]["recommended_action"] == "haz algo"
    assert (await admin_client.get("/api/alerts?state=all")).json()["total"] == 2
    assert (await admin_client.get("/api/alerts?state=resolved&severity=WARNING")).json()[
        "total"
    ] == 1
    ov = (await admin_client.get("/api/dashboard/overview")).json()
    assert [a["code"] for a in ov["critical_alerts"]] == ["a"]


async def test_audit_and_events_are_admin_only(admin_client: httpx.AsyncClient) -> None:
    assert (await admin_client.get("/api/audit")).status_code == 200
    async with client_as("viewer", "viewer") as viewer:
        assert (await viewer.get("/api/audit")).status_code == 403
        assert (await viewer.get("/api/events")).status_code == 403
        assert (await viewer.get("/api/dashboard/overview")).status_code == 200  # lectura sí


async def test_responses_are_not_cacheable_and_have_security_headers(
    admin_client: httpx.AsyncClient,
) -> None:
    r = await admin_client.get("/api/dashboard/overview")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


async def test_audit_listing_filters(admin_client: httpx.AsyncClient) -> None:
    d = (await admin_client.get("/api/audit?action=auth.login")).json()
    assert d["total"] >= 1 and all(i["action"] == "auth.login" for i in d["items"])
    assert (await admin_client.get("/api/audit?action=DROP;--")).status_code == 422
    rows = await _audit_rows()
    assert rows


async def _audit_rows() -> list[m.AuditLog]:
    async with db.session_scope() as s:
        return list((await s.execute(select(m.AuditLog))).scalars())
