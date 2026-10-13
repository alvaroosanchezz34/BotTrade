from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import numpy as np
import pytest
from sqlalchemy import delete, select, update

from app.core.domain import utcnow
from app.market_data import store
from app.persistence import db
from app.persistence import models as m
from app.services import lab_worker
from tests.api_helpers import client_as
from tests.exec_helpers import RULES
from tests.factories import candle

BODY = {
    "strategy_id": "momentum",
    "symbols": ["BTCUSDT"],
    "interval": "5m",
    "months": 1,
    "params": {"lookback": 20},
}


async def seed_market(n: int = 2600, symbol: str = "BTCUSDT", seed: int = 1) -> None:
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    px = 80000.0
    cs = []
    for i in range(n):
        o = px
        px = px * float(np.exp(rng.normal(0, 0.0012)))
        hi = max(o, px) * (1 + abs(float(rng.normal(0, 0.0006))))
        lo = min(o, px) * (1 - abs(float(rng.normal(0, 0.0006))))
        cs.append(
            candle(
                t0 + timedelta(minutes=5 * i), symbol=symbol, interval="5m",
                o=str(round(o, 2)), c=str(round(px, 2)), h=str(round(hi, 2)), l=str(round(lo, 2)), v="30",
            )
        )  # fmt: skip
    async with db.session_scope() as s:
        await store.save_candles(s, cs)
        await store.save_instruments(s, RULES, "test")


# ------------------------------------------------------------------ acceso y opciones


async def test_lab_requires_login_and_csrf(admin_client: httpx.AsyncClient) -> None:
    from app.api.main import create_app
    from tests.conftest import make_settings

    anon = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(make_settings(), manage_db=False)),
        base_url="http://t",
    )
    async with anon:
        assert (await anon.get("/api/lab/options")).status_code == 401
        assert (await anon.get("/api/lab/jobs")).status_code == 401
        assert (await anon.post("/api/lab/jobs", json=BODY)).status_code in (401, 403)
    await seed_market()
    token = admin_client.headers.pop("X-CSRF-Token")
    assert (await admin_client.post("/api/lab/jobs", json=BODY)).status_code == 403
    admin_client.headers["X-CSRF-Token"] = token
    assert (await admin_client.post("/api/lab/jobs", json=BODY)).status_code == 202


async def test_options_expose_templates_ranges_and_data(admin_client: httpx.AsyncClient) -> None:
    await seed_market()
    d = (await admin_client.get("/api/lab/options")).json()
    ids = {s["id"] for s in d["strategies"]}
    assert ids == {"momentum", "trend", "mean_reversion"}
    mom = next(s for s in d["strategies"] if s["id"] == "momentum")
    look = next(p for p in mom["params"] if p["name"] == "lookback")
    assert look["min"] == 2 and look["max"] == 1000 and look["type"] == "integer"
    assert d["data"]["5m"]["BTCUSDT"]["bars"] == 2600 and "ETHUSDT" not in d["data"]["5m"]
    assert d["max_months"] == {"1m": 6, "5m": 12} and "ficticio" in d["disclaimer"]


# ------------------------------------------------------------------ validación


@pytest.mark.parametrize(
    ("patch", "status", "fragment"),
    [
        ({"strategy_id": "ghost"}, 422, "desconocida"),
        ({"params": {"lookback": 1}}, 422, "lookback"),
        ({"params": {"lookback": 100000}}, 422, "lookback"),
        ({"params": {"filter": 1}}, 422, "no editables"),
        ({"params": {"os.system": 1}}, 422, "no editables"),
        ({"params": {"lookback": "20; DROP TABLE users"}}, 422, ""),
        ({"symbols": ["DOGEUSDT"]}, 422, "no disponible"),
        ({"symbols": ["ETHUSDT"]}, 422, "Sin datos"),
        ({"symbols": ["BTCUSDT", "ETHUSDT", "BNBUSDT"]}, 422, ""),
        ({"interval": "1h"}, 422, ""),
        ({"months": 2}, 422, ""),
        ({"interval": "1m", "months": 12}, 422, ""),
        ({"cost_level": "gratis"}, 422, ""),
        ({"hack": True}, 422, ""),
    ],
)
async def test_job_validation(
    admin_client: httpx.AsyncClient, patch: dict[str, object], status: int, fragment: str
) -> None:
    await seed_market()
    r = await admin_client.post("/api/lab/jobs", json={**BODY, **patch})
    assert r.status_code == status, r.text
    assert fragment in r.text
    async with db.session_scope() as s:
        assert (await s.execute(select(m.LabJob))).first() is None  # nada se encola


async def test_insufficient_data_is_rejected_with_reason(admin_client: httpx.AsyncClient) -> None:
    await seed_market(300)
    r = await admin_client.post("/api/lab/jobs", json=BODY)
    assert r.status_code == 422 and "Datos insuficientes" in r.text


# ------------------------------------------------------------------ cuotas


async def test_only_one_active_job_per_user_and_hourly_cap(admin_client: httpx.AsyncClient) -> None:
    await seed_market()
    first = await admin_client.post("/api/lab/jobs", json=BODY)
    assert first.status_code == 202 and first.json()["variants_tried"] == 1
    second = await admin_client.post("/api/lab/jobs", json=BODY)
    assert second.status_code == 409 and "en curso" in second.text

    async with db.session_scope() as s:  # acaba la primera
        await s.execute(update(m.LabJob).values(status="DONE"))
    for i in range(9):
        r = await admin_client.post("/api/lab/jobs", json={**BODY, "params": {"lookback": 20 + i}})
        assert r.status_code == 202, (i, r.text)
        async with db.session_scope() as s:
            await s.execute(update(m.LabJob).values(status="DONE"))
    capped = await admin_client.post("/api/lab/jobs", json=BODY)
    assert (
        capped.status_code == 429 and "por hora" in capped.text and "retry-after" in capped.headers
    )


async def test_variants_tried_counts_same_idea_only(admin_client: httpx.AsyncClient) -> None:
    await seed_market()
    ids = []
    for look in (20, 30, 40):
        r = await admin_client.post("/api/lab/jobs", json={**BODY, "params": {"lookback": look}})
        assert r.status_code == 202
        ids.append(r.json()["variants_tried"])
        async with db.session_scope() as s:
            await s.execute(update(m.LabJob).values(status="DONE"))
    assert ids == [1, 2, 3]
    other = await admin_client.post(
        "/api/lab/jobs", json={**BODY, "strategy_id": "trend", "params": {}}
    )
    assert other.json()["variants_tried"] == 1  # otra idea: contador aparte


# ------------------------------------------------------------------ ciclo completo y privacidad


async def test_full_lifecycle_report_and_privacy(admin_client: httpx.AsyncClient) -> None:
    await seed_market()
    created = (await admin_client.post("/api/lab/jobs", json=BODY)).json()
    job_id = uuid.UUID(created["job_id"])
    queued = (await admin_client.get(f"/api/lab/jobs/{job_id}")).json()
    assert queued["status"] == "QUEUED" and queued["report"] is None

    claimed = await lab_worker.claim_next()
    assert claimed == job_id and await lab_worker.claim_next() is None  # no se reclama dos veces
    await lab_worker.process_job(job_id, workers=1)

    done = (await admin_client.get(f"/api/lab/jobs/{job_id}")).json()
    assert done["status"] == "DONE", done
    rep = done["report"]
    assert rep["verdict"] in ("PROMETEDORA", "SIN EVIDENCIA DE VENTAJA", "NO CONCLUYENTE")
    assert len(rep["checks"]) >= 8 and len(rep["thirds"]) == 3
    assert any("dinero ficticio" in c for c in rep["caveats"])
    assert done["verdict"] == rep["verdict"] and done["run_id"]

    # El detalle estándar de simulación (capital final, curva) funciona para el dueño…
    d = (await admin_client.get(f"/api/backtests/{done['run_id']}")).json()
    assert Decimal(d["summary"]["initial_capital"]) == 500 and "curve" in d["summary"]
    # …y la simulación del laboratorio NO sale en los listados generales.
    assert (await admin_client.get("/api/backtests")).json()["total"] == 0

    # Otro usuario: no ve el trabajo ni la simulación (404, sin distinguir «no existe»).
    async with client_as("otro", "viewer") as other:
        assert (await other.get(f"/api/lab/jobs/{job_id}")).status_code == 404
        assert (await other.get(f"/api/backtests/{done['run_id']}")).status_code == 404
        assert (await other.get("/api/lab/jobs")).json()["total"] == 0
    assert (await admin_client.get("/api/lab/jobs")).json()["total"] == 1


async def test_viewer_can_use_lab_but_not_control_the_bot() -> None:
    await seed_market()
    async with client_as("publico", "viewer") as v:
        assert (await v.post("/api/lab/jobs", json=BODY)).status_code == 202
        assert (await v.post("/api/bot/pause", json={})).status_code == 403


async def test_worker_failure_is_reported_not_hidden(admin_client: httpx.AsyncClient) -> None:
    await seed_market()
    job_id = uuid.UUID((await admin_client.post("/api/lab/jobs", json=BODY)).json()["job_id"])
    async with db.session_scope() as s:
        await s.execute(delete(m.CandleRow))  # los datos desaparecen antes de simular
    await lab_worker.claim_next()
    await lab_worker.process_job(job_id, workers=1)
    j = (await admin_client.get(f"/api/lab/jobs/{job_id}")).json()
    assert j["status"] == "FAILED" and "No hay datos" in j["error"] and j["report"] is None


async def test_stale_running_jobs_are_failed_on_worker_restart(
    admin_client: httpx.AsyncClient,
) -> None:
    await seed_market()
    job_id = uuid.UUID((await admin_client.post("/api/lab/jobs", json=BODY)).json()["job_id"])
    await lab_worker.claim_next()  # queda RUNNING y el «worker» muere
    assert await lab_worker.recover_stale() == 1
    j = (await admin_client.get(f"/api/lab/jobs/{job_id}")).json()
    assert j["status"] == "FAILED" and "reinicio" in j["error"]


async def test_lab_job_rows_are_audited(admin_client: httpx.AsyncClient) -> None:
    await seed_market()
    await admin_client.post("/api/lab/jobs", json=BODY)
    async with db.session_scope() as s:
        acts = (await s.execute(select(m.AuditLog.action, m.AuditLog.actor))).all()
    assert ("lab.job_created", "admin") in acts
    assert utcnow()  # sin efectos secundarios
