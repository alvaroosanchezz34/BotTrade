"""Primitivas de autenticación: Argon2id, tokens opacos y CSRF (ADR-007)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Parámetros por defecto de argon2-cffi (RFC 9106, perfil de baja memoria): Argon2id.
_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 12

# Hash ficticio para igualar el tiempo de respuesta cuando el usuario no existe.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres")
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Los tokens de sesión y CSRF se guardan solo como SHA-256."""
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(token: str | None, expected_hash: str) -> bool:
    if not token:
        return False
    return hmac.compare_digest(token_hash(token), expected_hash)
