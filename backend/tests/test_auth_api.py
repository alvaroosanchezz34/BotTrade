from datetime import timedelta

import httpx
from sqlalchemy import func, select, update

from app.auth.service import create_user
from app.core.domain import utcnow
from app.persistence import db
from app.persistence.models import AuditLog, Session, User
from tests.conftest import ADMIN_PASSWORD


async def _make_admin() -> None:
    async with db.session_scope() as s:
        await create_user(s, "admin", ADMIN_PASSWORD)


async def test_login_sets_httponly_session_and_csrf_cookie(client: httpx.AsyncClient) -> None:
    await _make_admin()
    r = await client.post("/api/auth/login", json={"username": "Admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 200
    assert r.json() == {"username": "admin", "role": "admin"}
    set_cookie = r.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith("bt_session="))
    csrf_cookie = next(c for c in set_cookie if c.startswith("bt_csrf="))
    assert "HttpOnly" in session_cookie and "SameSite=strict" in session_cookie
    assert "HttpOnly" not in csrf_cookie


async def test_session_token_is_stored_hashed(client: httpx.AsyncClient) -> None:
    await _make_admin()
    await client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    token = client.cookies["bt_session"]
    async with db.get_sessionmaker()() as s:
        stored = (await s.scalars(select(Session.token_hash))).all()
    assert stored and token not in stored


async def test_wrong_password_rejected_and_audited(client: httpx.AsyncClient) -> None:
    await _make_admin()
    r = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "nope-nope-nope"}
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Credenciales inválidas"
    async with db.get_sessionmaker()() as s:
        actions = (await s.scalars(select(AuditLog.action))).all()
    assert "auth.login_failed" in actions


async def test_unknown_user_gets_same_error(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/auth/login", json={"username": "ghost", "password": "whatever-123"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Credenciales inválidas"


async def test_lockout_after_max_attempts(client: httpx.AsyncClient) -> None:
    await _make_admin()
    for _ in range(5):
        await client.post(
            "/api/auth/login", json={"username": "admin", "password": "bad-password-x"}
        )
    # Incluso con la contraseña correcta, la cuenta está bloqueada.
    r = await client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 401
    async with db.get_sessionmaker()() as s:
        user = await s.scalar(select(User))
    assert user is not None and user.locked_until is not None


async def test_login_rate_limited(client: httpx.AsyncClient) -> None:
    codes = [
        (await client.post("/api/auth/login", json={"username": "x", "password": "y"})).status_code
        for _ in range(12)
    ]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


async def test_me_requires_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_me_with_session(admin_client: httpx.AsyncClient) -> None:
    r = await admin_client.get("/api/auth/me")
    assert r.status_code == 200 and r.json()["username"] == "admin"


async def test_expired_session_rejected(admin_client: httpx.AsyncClient) -> None:
    async with db.session_scope() as s:
        await s.execute(update(Session).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert (await admin_client.get("/api/auth/me")).status_code == 401


async def test_logout_requires_csrf(admin_client: httpx.AsyncClient) -> None:
    csrf = admin_client.headers.pop("X-CSRF-Token")
    assert (await admin_client.post("/api/auth/logout")).status_code == 403
    admin_client.headers["X-CSRF-Token"] = "forged"
    assert (await admin_client.post("/api/auth/logout")).status_code == 403
    admin_client.headers["X-CSRF-Token"] = csrf
    assert (await admin_client.post("/api/auth/logout")).status_code == 204
    async with db.get_sessionmaker()() as s:
        revoked = await s.scalar(select(func.count()).where(Session.revoked_at.is_not(None)))
    assert revoked == 1


async def test_security_headers_present(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
