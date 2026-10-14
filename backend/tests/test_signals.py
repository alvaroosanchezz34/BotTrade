import asyncio
import signal

from app.core.signals import install_stop_handlers


class _WindowsLikeLoop:
    """Bucle sin add_signal_handler (como en Windows)."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def add_signal_handler(self, *_: object) -> None:
        raise NotImplementedError

    def call_soon_threadsafe(self, cb: object) -> None:
        self.calls.append(cb)


async def test_stop_handlers_work_with_posix_loop() -> None:
    stop = asyncio.Event()
    install_stop_handlers(asyncio.get_running_loop(), stop.set)
    signal.raise_signal(signal.SIGTERM)
    await asyncio.wait_for(stop.wait(), 2)


def test_stop_handlers_fall_back_when_loop_has_no_signal_support() -> None:
    previous = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
    loop = _WindowsLikeLoop()
    fired: list[int] = []
    try:
        install_stop_handlers(loop, lambda: fired.append(1))  # type: ignore[arg-type]
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)  # type: ignore[operator]
        assert len(loop.calls) == 1
    finally:
        signal.signal(signal.SIGINT, previous[0])
        signal.signal(signal.SIGTERM, previous[1])
