"""Parada ordenada con Ctrl+C / SIGTERM, también en Windows.

``loop.add_signal_handler`` no existe en Windows (lanza ``NotImplementedError``): allí se usa
``signal.signal`` y se avisa al bucle de forma segura entre hilos.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable


def install_stop_handlers(loop: asyncio.AbstractEventLoop, callback: Callable[[], None]) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, callback)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(callback))
