from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.api.main import create_app
from app.auth.service import login
from app.core.domain import utcnow
from app.persistence import db
from app.persistence import models as m
from tests.api_helpers import client_as
from tests.conftest import ADMIN_PASSWORD, make_settings
from tests.test_engine_e2e import make_world

CONTROL = [
    ("/api/bot/pause", {}),
    ("/api/bot/resume", {"clear_kill_switch": False}),
    ("/api/bot/kill-switch", {"confirm": "KILL"}),
    ("/api/strategies/momentum/enable", {}),
    ("/api/strategies/momentum/disable", {}),
]


async def n_commands() -> int:
    async with db.session_scope() as s:
        return int((await s.execute(select(func.count()).select_from(m.BotCommand))).scalar_one())


# ------------------------------------------------------------------ autorización


@pytest.mark.parametrize(("path", "body"), CONTROL)
async def test_control_requires_authentication(
    client: httpx.AsyncClient, path: str, body: dict[str, object]
) -> None:
    assert (await client.post(path, json=body)).status_code == 401
    assert await n_commands() == 0


@pytest.mark.parametrize(("path", "body"), CONTROL)
async def test_control_requires_csrf_header(
    admin_client: httpx.AsyncClient, path: str, body: dict[str, object]
) -> None:
    del admin_client.headers["X-CSRF-Token"]
    r = await admin_client.post(path, json=body)
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]
    admin_client.headers["X-CSRF-Token"] = "x" * 43
    assert (await admin_client.post(path, json=body)).status_code == 403
    assert await n_commands() == 0


@pytest.mark.parametrize(("path", "body"), CONTROL)
async def test_viewer_cannot_control(path: str, body: dict[str, object]) -> None:
    async with client_as("viewer", "viewer") as v:
        assert (await v.post(path, json=body)).status_code == 403
    assert await n_commands() == 0


async def test_csrf_from_another_session_is_rejected(admin_client: httpx.AsyncClient) -> None:
    async with client_as("second", "admin") as other:
        stolen = other.headers["X-CSRF-Token"]
    admin_client.headers["X-CSRF-Token"] = stolen
    assert (await admin_client.post("/api/bot/pause", json={})).status_code == 403


async def test_control_with_expired_session_is_401(admin_client: httpx.AsyncClient) -> None:
    from sqlalchemy import update

    async with db.session_scope() as s:
        await s.execute(update(m.Session).values(expires_at=utcnow() - timedelta(minutes=1)))
    assert (await admin_client.post("/api/bot/pause", json={})).status_code == 401
    assert (await admin_client.get("/api/dashboard/overview")).status_code == 401


# ------------------------------------------------------------------ semántica de los comandos


async def test_pause_is_requested_not_confirmed(admin_client: httpx.AsyncClient) -> None:
    r = await admin_client.post("/api/bot/pause", json={})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "PENDING" and body["engine_alive"] is False
    assert "no está confirmada" in body["detail"]
    async with db.session_scope() as s:
        cmd = (await s.execute(select(m.BotCommand))).scalar_one()
        audit = (
            await s.execute(select(m.AuditLog).where(m.AuditLog.action == "command.requested"))
        ).scalar_one()
    assert cmd.command == "PAUSE" and cmd.created_by == "admin" and cmd.status == "PENDING"
    assert audit.actor == "admin" and audit.target == "PAUSE"
    got = (await admin_client.get(f"/api/commands/{body['command_id']}")).json()
    assert got["status"] == "PENDING" and got["processed_at"] is None


async def test_double_tap_does_not_duplicate_commands(admin_client: httpx.AsyncClient) -> None:
    a = (await admin_client.post("/api/bot/pause", json={})).json()
    b = (await admin_client.post("/api/bot/pause", json={})).json()
    assert b["command_id"] == a["command_id"] and b["duplicate_of_pending"] is True
    assert await n_commands() == 1
    # Parámetros distintos ⇒ solicitud distinta.
    await admin_client.post("/api/bot/resume", json={"clear_kill_switch": False})
    await admin_client.post("/api/bot/resume", json={"clear_kill_switch": True})
    assert await n_commands() == 3


@pytest.mark.parametrize(
    "body",
    [{}, {"confirm": "kill"}, {"confirm": "yes"}, {"confirm": "KILL", "liquidate_positions": "x"},
     {"confirm": "KILL", "arbitrary": 1}],
)  # fmt: skip
async def test_kill_switch_needs_server_validated_confirmation(
    admin_client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    assert (await admin_client.post("/api/bot/kill-switch", json=body)).status_code == 422
    assert await n_commands() == 0


async def test_kill_switch_payload_defaults_to_not_liquidating(
    admin_client: httpx.AsyncClient,
) -> None:
    assert (
        await admin_client.post("/api/bot/kill-switch", json={"confirm": "KILL"})
    ).status_code == 202
    async with db.session_scope() as s:
        cmd = (await s.execute(select(m.BotCommand))).scalar_one()
    assert cmd.command == "KILL_SWITCH" and cmd.payload == {"liquidate_positions": False}


async def test_extra_fields_cannot_smuggle_parameters(admin_client: httpx.AsyncClient) -> None:
    r = await admin_client.post("/api/bot/pause", json={"mode": "LIVE", "sudo": True})
    assert r.status_code == 422
    r = await admin_client.post("/api/bot/resume", json={"skip_checks": True})
    assert r.status_code == 422


async def test_strategy_toggle_validates_existence(admin_client: httpx.AsyncClient) -> None:
    assert (await admin_client.post("/api/strategies/ghost/enable", json={})).status_code == 404
    async with db.session_scope() as s:
        s.add(m.Strategy(id="momentum", name="M", symbols=["BTCUSDT"], params={}, version="1"))
    r = await admin_client.post("/api/strategies/momentum/enable", json={})
    assert r.status_code == 202
    async with db.session_scope() as s:
        cmd = (await s.execute(select(m.BotCommand))).scalar_one()
        strat = await s.get(m.Strategy, "momentum")
    assert cmd.payload == {"strategy_id": "momentum"}
    assert strat is not None and strat.enabled is False  # el API NO la activa: lo hace el engine
    assert (await admin_client.post("/api/strategies/mom%27;--/enable", json={})).status_code == 404


async def test_no_endpoint_can_enable_real_trading(admin_client: httpx.AsyncClient) -> None:
    spec = (await admin_client.get("/openapi.json")).json()
    paths = " ".join(spec["paths"]).lower()
    assert "live" not in paths and "real" not in paths and "withdraw" not in paths
    for path in ("/api/bot/live", "/api/trading/real", "/api/config/mode"):
        assert (await admin_client.post(path, json={})).status_code in (404, 405)


async def test_control_rate_limit(admin_client: httpx.AsyncClient) -> None:
    codes = [(await admin_client.post("/api/bot/pause", json={})).status_code for _ in range(35)]
    assert codes.count(202) == 30 and codes[-1] == 429
    r = await admin_client.post("/api/bot/pause", json={})
    assert "retry-after" in r.headers


async def test_alert_ack_requires_admin_and_csrf(admin_client: httpx.AsyncClient) -> None:
    from app.core.domain import Severity
    from app.monitoring.alerts import raise_alert

    async with db.session_scope() as s:
        await raise_alert(
            s, severity=Severity.WARNING, code="x", message="m", recommended_action="r"
        )
        alert = (await s.execute(select(m.Alert))).scalar_one()
    async with client_as("viewer", "viewer") as v:
        assert (await v.post(f"/api/alerts/{alert.id}/ack")).status_code == 403
    r = await admin_client.post(f"/api/alerts/{alert.id}/ack")
    assert r.status_code == 200 and r.json()["acknowledged_by"] == "admin"
    async with db.session_scope() as s:
        a = await s.get(m.Alert, alert.id)
    assert a is not None and a.resolved_at is None  # reconocer no resuelve


# ------------------------------------------------------------------ round trip con el engine


async def test_api_request_is_confirmed_only_after_engine_applies_it(
    monkeypatch: pytest.MonkeyPatch, admin_client: httpx.AsyncClient
) -> None:
    w = await make_world(monkeypatch)
    r = await admin_client.post("/api/bot/kill-switch", json={"confirm": "KILL"})
    cid = r.json()["command_id"]
    assert (await admin_client.get("/api/risk")).json()["state"]["kill_switch_active"] is False
    assert (await admin_client.get(f"/api/commands/{cid}")).json()["status"] == "PENDING"

    await w.engine.heartbeat()
    await w.engine.process_commands()

    cmd = (await admin_client.get(f"/api/commands/{cid}")).json()
    assert cmd["status"] == "APPLIED" and cmd["processed_at"]
    risk = (await admin_client.get("/api/risk")).json()
    assert risk["state"]["kill_switch_active"] is True and risk["state"]["entries_allowed"] is False
    ov = (await admin_client.get("/api/dashboard/overview")).json()
    assert ov["engine"]["status"] == "running"
    assert any(a["code"] == "kill_switch" for a in ov["critical_alerts"])


async def test_rejected_resume_is_reported_with_reason(
    monkeypatch: pytest.MonkeyPatch, admin_client: httpx.AsyncClient
) -> None:
    w = await make_world(monkeypatch)
    w.feed.connected = False
    await admin_client.post("/api/bot/kill-switch", json={"confirm": "KILL"})
    await w.engine.process_commands()
    cid = (await admin_client.post("/api/bot/resume", json={"clear_kill_switch": True})).json()[
        "command_id"
    ]
    await w.engine.process_commands()
    cmd = (await admin_client.get(f"/api/commands/{cid}")).json()
    assert cmd["status"] == "REJECTED" and "conexiones" in cmd["result"]
    assert (await admin_client.get("/api/risk")).json()["state"]["kill_switch_active"] is True


async def test_stale_resume_command_is_not_executed_late(
    monkeypatch: pytest.MonkeyPatch, admin_client: httpx.AsyncClient
) -> None:
    w = await make_world(monkeypatch)
    await admin_client.post("/api/bot/kill-switch", json={"confirm": "KILL"})
    await w.engine.process_commands()
    cid = (await admin_client.post("/api/bot/resume", json={"clear_kill_switch": True})).json()[
        "command_id"
    ]
    async with db.session_scope() as s:
        cmd = await s.get(m.BotCommand, __import__("uuid").UUID(cid))
        assert cmd is not None
        cmd.created_at = utcnow() - timedelta(minutes=30)
    await w.engine.process_commands()
    got = (await admin_client.get(f"/api/commands/{cid}")).json()
    assert got["status"] == "REJECTED" and "caducada" in got["result"]
    assert (await admin_client.get("/api/risk")).json()["state"]["kill_switch_active"] is True


async def test_old_pause_and_kill_still_apply(
    monkeypatch: pytest.MonkeyPatch, admin_client: httpx.AsyncClient
) -> None:
    """Lo que endurece (pausa/kill) nunca caduca: ante la duda, se bloquea."""
    w = await make_world(monkeypatch)
    await admin_client.post("/api/bot/pause", json={})
    async with db.session_scope() as s:
        cmd = (
            await s.execute(select(m.BotCommand).where(m.BotCommand.command == "PAUSE"))
        ).scalar_one()
        cmd.created_at = utcnow() - timedelta(hours=5)
    await w.engine.process_commands()
    assert (await admin_client.get("/api/risk")).json()["state"]["paused"] is True


# ------------------------------------------------------------------ cuenta y cabeceras


async def test_change_password_flow(admin_client: httpx.AsyncClient) -> None:
    async with client_as("other", "admin") as _:
        pass
    new = "another-long-passphrase"
    bad = await admin_client.post(
        "/api/auth/change-password",
        json={"current_password": "wrong-wrong-wrong", "new_password": new},
    )
    assert bad.status_code == 403
    short = await admin_client.post(
        "/api/auth/change-password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "short"},
    )
    assert short.status_code == 422
    ok = await admin_client.post(
        "/api/auth/change-password", json={"current_password": ADMIN_PASSWORD, "new_password": new}
    )
    assert ok.status_code == 204
    assert (await admin_client.get("/api/auth/me")).status_code == 200  # la sesión actual sigue
    settings = make_settings()
    async with db.session_scope() as s:
        from app.auth.service import AuthError

        with pytest.raises(AuthError):
            await login(s, settings, "admin", ADMIN_PASSWORD, ip=None, user_agent=None)
        await login(s, settings, "admin", new, ip=None, user_agent=None)
    async with db.session_scope() as s:
        actions = (await s.execute(select(m.AuditLog.action))).scalars().all()
    assert "auth.change_password" in actions and "auth.change_password_failed" in actions


async def test_change_password_revokes_other_sessions(admin_client: httpx.AsyncClient) -> None:
    app = create_app(make_settings(), manage_db=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as phone:
        await phone.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        assert (await phone.get("/api/auth/me")).status_code == 200
        sessions = (await admin_client.get("/api/auth/sessions")).json()["sessions"]
        assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
        r = await admin_client.post(
            "/api/auth/change-password",
            json={"current_password": ADMIN_PASSWORD, "new_password": "another-long-passphrase"},
        )
        assert r.status_code == 204
        assert (await phone.get("/api/auth/me")).status_code == 401


async def test_cors_allows_only_configured_origins(client: httpx.AsyncClient) -> None:
    ok = await client.options(
        "/api/dashboard/overview",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert ok.headers.get("access-control-allow-credentials") == "true"
    bad = await client.options(
        "/api/dashboard/overview",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in bad.headers
    methods = ok.headers.get("access-control-allow-methods", "")
    assert "DELETE" not in methods and "PUT" not in methods


async def test_internal_errors_do_not_leak_details(
    admin_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.routes.dashboard as dash

    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("password=hunter2 host=10.0.0.5")

    monkeypatch.setattr(dash, "load_account", boom)
    transport = httpx.ASGITransport(app=admin_client._transport.app, raise_app_exceptions=False)  # type: ignore[attr-defined]
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", cookies=admin_client.cookies
    ) as c:
        r = await c.get("/api/dashboard/overview")
    assert r.status_code == 500 and r.json() == {"detail": "Error interno"}
    assert "hunter2" not in r.text


async def test_production_settings_are_validated() -> None:
    from app.core.config import Environment

    with pytest.raises(ValueError, match="insegura"):
        make_settings(environment=Environment.PRODUCTION)  # cookies no seguras, BD débil...


async def test_docs_are_hidden_in_production() -> None:
    from app.core.config import Environment

    s = make_settings(
        environment=Environment.PRODUCTION,
        cookie_secure=True,
        cors_origins=["https://bot.example.com"],
        database_url="postgresql+asyncpg://u:aVeryLongRandomPassword123@db:5432/x",
    )
    app = create_app(s, manage_db=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/docs")).status_code == 404
        assert (await c.get("/openapi.json")).status_code == 404
        r = await c.get("/health")
        assert "strict-transport-security" in r.headers


# ------------------------------------------------------------------ WebSocket


class FakeWs:
    def __init__(self, *, origin: str | None, token: str | None, host: str = "test") -> None:
        self.headers = {"host": host, **({"origin": origin} if origin else {})}
        self.cookies = {"bt_session": token} if token else {}
        self.sent: list[dict[str, object]] = []
        self.accepted = False
        self.closed: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.closed = code

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))


async def session_token(role: str = "admin") -> str:
    from app.auth.service import create_user

    settings = make_settings()
    async with db.session_scope() as s:
        await create_user(s, "wsuser", ADMIN_PASSWORD, role=role)
    async with db.session_scope() as s:
        issued = await login(s, settings, "wsuser", ADMIN_PASSWORD, ip=None, user_agent=None)
    return issued.token


async def test_ws_rejects_foreign_origin_and_missing_auth() -> None:
    from app.api.routes.ws import serve

    settings = make_settings()
    token = await session_token()
    for ws in (
        FakeWs(origin="https://evil.example", token=token),  # CSWSH
        FakeWs(origin=None, token=token),
        FakeWs(origin="http://localhost:5173", token=None),
        FakeWs(origin="http://localhost:5173", token="forged-token"),
    ):
        await serve(ws, settings, max_cycles=1)
        assert ws.closed == 1008 and not ws.accepted and ws.sent == []


async def test_ws_sends_pulse_with_state_recovery_and_command_updates() -> None:
    from app.api.routes.ws import serve

    settings = make_settings()
    token = await session_token()
    ws = FakeWs(origin="http://localhost:5173", token=token)
    async with db.session_scope() as s:
        s.add(m.BotCommand(created_by="admin", command="PAUSE", payload={}))
    await serve(ws, settings, max_cycles=2)
    assert ws.accepted and ws.closed is None
    assert len(ws.sent) == 1  # el segundo ciclo no cambió nada: no se repite
    pulse = ws.sent[0]
    assert pulse["type"] == "pulse" and pulse["seq"] == 1
    data = pulse["data"]
    assert data["engine"] == "never_started" and data["equity"] is None  # type: ignore[index]
    assert data["commands"][0]["status"] == "PENDING"  # type: ignore[index]

    # Misma máquina, mismo host (proxy inverso): origen coincide con Host.
    ws2 = FakeWs(origin="https://bot.example.com", token=token, host="bot.example.com")
    await serve(ws2, settings, max_cycles=1)
    assert ws2.accepted and len(ws2.sent) == 1  # reconexión ⇒ pulso completo inmediato


async def test_ws_closes_when_session_is_revoked_mid_connection() -> None:
    from sqlalchemy import update

    from app.api.routes.ws import serve

    settings = make_settings()
    token = await session_token()
    ws = FakeWs(origin="http://localhost:5173", token=token)

    original_send = ws.send_text

    async def send_then_revoke(data: str) -> None:
        await original_send(data)
        async with db.session_scope() as s:
            await s.execute(update(m.Session).values(revoked_at=utcnow()))

    ws.send_text = send_then_revoke  # type: ignore[method-assign]
    await serve(ws, settings, max_cycles=3)
    assert ws.sent[-1] == {"type": "auth_expired"} and ws.closed == 1008
