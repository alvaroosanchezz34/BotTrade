"""Limitador de frecuencia en memoria (ventana deslizante por clave).

Suficiente con un único proceso ``api`` (ADR-003). Si se escalara a varios
workers, habría que moverlo a PostgreSQL o al proxy.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request, status


class SlidingWindowLimiter:
    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> tuple[bool, float]:
        """Registra un intento. Devuelve (permitido, segundos hasta poder reintentar)."""
        now = self._clock()
        q = self._hits[key]
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False, self.window - (now - q[0])
        q.append(now)
        return True, 0.0

    def reset(self) -> None:
        self._hits.clear()


def client_ip(request: Request) -> str:
    # Detrás de Caddy, uvicorn se arranca con --proxy-headers y --forwarded-allow-ips.
    return request.client.host if request.client else "unknown"


def limit(limiter: SlidingWindowLimiter, scope: str) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        allowed, retry = limiter.hit(f"{scope}:{client_ip(request)}")
        if not allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Demasiadas peticiones",
                headers={"Retry-After": str(max(1, int(retry) + 1))},
            )

    return dependency


login_limiter = SlidingWindowLimiter(limit=10, window_seconds=60)
control_limiter = SlidingWindowLimiter(limit=30, window_seconds=60)
