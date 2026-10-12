"""Engine de trading: el único proceso que decide y ejecuta (nunca el navegador).

Flujo por cada vela cerrada::

    vela → (avance del exchange simulado) → valoración y estado de riesgo
         → gestión de salidas (stop / objetivo / tiempo / señal de salida)
         → estrategias activas → coordinación → RiskEngine → OrderManager → persistencia

Reglas que fija este módulo (todas con tests):

* Sin datos aptos (huecos, obsoletos, WS caído) **no se generan señales nuevas**; las
  salidas de posiciones existentes sí se siguen gestionando.
* Política de coordinación: como máximo una entrada por símbolo y vela. Si varias
  estrategias coinciden, gana la de mayor puntuación (desempate por id) y las demás se
  guardan como ``SUPERSEDED``. Nunca hay posiciones duplicadas ni contradictorias.
* Toda señal se persiste, también las rechazadas, con su decisión de riesgo.
* Los comandos del panel (pausa, reanudar, kill switch, activar estrategias) llegan por la
  tabla ``bot_commands`` y la UI los muestra como «solicitados» hasta que aquí pasan a
  ``APPLIED`` o ``REJECTED``.
* Tras un reinicio no hay entradas hasta completar una reconciliación limpia.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol, cast

import numpy as np
from sqlalchemy import select

from app.audit.log import audit, system_event
from app.core.domain import Candle, Quote, Severity, Signal, interval_delta, utcnow
from app.core.money import ZERO
from app.execution.accounting import AccountSnapshot, account_snapshot, get_initial_capital
from app.execution.controls import (
    KillSwitchPolicy,
    ResumeContext,
    kill_switch,
    pause_entries,
    resume,
)
from app.execution.order_manager import OrderManager, record_signal, record_signal_and_decision
from app.execution.positions import exit_purpose_for_bar, exit_purpose_for_price, time_exit_due
from app.execution.reconciler import ReconciliationReport, reconcile
from app.market_data import store
from app.monitoring.alerts import raise_alert, resolve_alert
from app.persistence import db
from app.persistence import models as m
from app.risk.engine import MarketContext
from app.risk.store import load_or_create_risk_state, load_risk_state, save_risk_state
from app.strategies.base import Strategy
from app.strategies.registry import STRATEGIES, build

log = logging.getLogger(__name__)

COMMAND_TTL = timedelta(minutes=10)

StrategyBuilder = Callable[[str, dict[str, Any] | None], Strategy[Any]]


class DataStatus(Protocol):
    """Lo que el engine necesita saber del feed de datos."""

    connected: bool
    quotes: dict[str, Quote]

    def readiness(self, symbol: str, interval: str) -> tuple[bool, str]: ...


class MarketAwareAdapter(Protocol):
    """Adaptadores simulados que necesitan que el engine les cuente el mercado."""

    def set_price(
        self, symbol: str, price: Decimal, *, available_qty: Decimal | None = None
    ) -> None: ...
    def set_quote(self, quote: Quote, *, available_qty: Decimal | None = None) -> None: ...
    def advance(
        self, symbol: str, *, low: Decimal, high: Decimal, available_qty: Decimal | None = None
    ) -> None: ...


@dataclass
class EngineConfig:
    mode: str
    symbols: list[str]
    reference_capital: Decimal
    max_volume_participation: Decimal = Decimal("0.1")
    reconcile_interval: float = 60.0
    heartbeat_interval: float = 5.0
    command_poll_interval: float = 1.0
    snapshot_interval: float = 300.0
    balance_tolerance_pct: Decimal = Decimal(0)
    history_bars: int = 1600  # cubre la ventana de volatilidad (1440) + calentamiento


@dataclass
class Engine:
    config: EngineConfig
    manager: OrderManager
    feed: DataStatus
    strategy_builder: StrategyBuilder = build
    clock: Callable[[], datetime] = utcnow
    last_reconcile: ReconciliationReport | None = None
    started_at: datetime = field(default_factory=utcnow)
    _marks: dict[str, Decimal] = field(default_factory=dict)
    _correlations: dict[tuple[str, str], float] = field(default_factory=dict)
    _baseline: dict[str, Decimal] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _initial_capital: Decimal = ZERO

    # ================================================================ arranque

    async def start(self) -> None:
        """Prepara el estado y reconcilia. Las entradas siguen bloqueadas hasta que acabe."""
        mode = self.config.mode
        adapter = self.manager.adapter
        async with db.session_scope() as s:
            self._initial_capital = await get_initial_capital(
                s, mode, self.config.reference_capital
            )
            await self._ensure_strategies(s)
            await self._ensure_baseline(s)
            await self._detect_unclean_restart(s)
            await system_event(
                s, kind="engine_started", source="engine", detail={"mode": mode, "pid": os.getpid()}
            )
        if hasattr(adapter, "restore_from_ledger"):
            async with db.session_scope() as s:
                await cast(Any, adapter).restore_from_ledger(s, mode, self._initial_capital)
        snap = await self.snapshot()
        async with db.session_scope() as s:
            await load_or_create_risk_state(s, mode, snap.equity)
        await self.run_reconciliation()
        await self.heartbeat()

    async def _detect_unclean_restart(self, s: Any) -> None:
        last = (
            await s.execute(
                select(m.SystemEvent)
                .where(m.SystemEvent.kind.in_(("engine_started", "engine_stopped")))
                .order_by(m.SystemEvent.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if last is not None and last.kind == "engine_started":
            await raise_alert(
                s,
                severity=Severity.WARNING,
                code="engine_unexpected_restart",
                message="El engine se reinició sin una parada ordenada previa",
                recommended_action=(
                    "Revisa los logs. Las entradas siguen bloqueadas hasta reconciliar "
                    "órdenes y posiciones."
                ),
                context={"previous_start": last.at.isoformat()},
                dedup_key="engine_unexpected_restart",
            )

    async def _ensure_strategies(self, s: Any) -> None:
        """Crea filas para las estrategias del catálogo (desactivadas por defecto)."""
        existing = {r.id: r for r in (await s.execute(select(m.Strategy))).scalars()}
        for sid, cls in STRATEGIES.items():
            inst = cls()
            if sid not in existing:
                s.add(
                    m.Strategy(
                        id=sid,
                        name=cls.name,
                        description=cls.description,
                        enabled=False,
                        symbols=list(self.config.symbols),
                        interval="1m",
                        params=inst.params.model_dump(mode="json"),
                        version=cls.version,
                    )
                )
            else:
                existing[sid].version = cls.version
            have = (
                await s.execute(
                    select(m.StrategyVersion).where(
                        m.StrategyVersion.strategy_id == sid,
                        m.StrategyVersion.version == cls.version,
                    )
                )
            ).scalar_one_or_none()
            if have is None:
                await s.flush()
                s.add(
                    m.StrategyVersion(
                        strategy_id=sid,
                        version=cls.version,
                        params=inst.params.model_dump(mode="json"),
                        code_hash=cls.code_hash(),
                    )
                )

    async def _ensure_baseline(self, s: Any) -> None:
        key = f"account:{self.config.mode}"
        row = await s.get(m.SystemSetting, key)
        assert row is not None
        if "baseline" not in row.value:
            balances = await self.manager.adapter.balances()
            bases = {r.base_asset for r in self.manager.rules.values()}
            baseline = {a: str(balances.get(a, ZERO)) for a in bases}
            row.value = {**row.value, "baseline": baseline}
        self._baseline = {k: Decimal(str(v)) for k, v in row.value["baseline"].items()}

    def _sim(self) -> MarketAwareAdapter | None:
        a = self.manager.adapter
        return cast(MarketAwareAdapter, a) if hasattr(a, "advance") else None

    # ================================================================ valoración

    def _mark_for(self, symbol: str) -> Decimal | None:
        q = self.feed.quotes.get(symbol)
        return q.mid if q is not None else self._marks.get(symbol)

    async def snapshot(self) -> AccountSnapshot:
        marks = {sym: p for sym in self.config.symbols if (p := self._mark_for(sym)) is not None}
        async with db.session_scope() as s:
            return await account_snapshot(
                s,
                self.config.mode,
                initial_capital=self._initial_capital,
                marks=marks,
                rules=self.manager.rules,
            )

    async def _refresh_risk(self, now: datetime) -> AccountSnapshot:
        """Rueda el día, actualiza pico y bloqueos con el equity actual y lo persiste."""
        snap = await self.snapshot()
        async with db.session_scope() as s:
            state = await load_or_create_risk_state(s, self.config.mode, snap.equity)
            state.roll(now, snap.equity)
            for reason in self.manager.risk_engine.update_latches(state, snap.equity):
                await raise_alert(
                    s,
                    severity=Severity.CRITICAL,
                    code="risk_latch",
                    message=f"Entradas bloqueadas: {reason}",
                    recommended_action="Investiga la causa; reanudar exige comprobaciones.",
                    context={"reason": reason},
                    dedup_key=f"risk_latch:{reason}",
                )
            await save_risk_state(s, self.config.mode, state)
        return snap

    # ================================================================ eventos de mercado

    async def on_quote(self, quote: Quote) -> None:
        """Vigila stops con la cotización en vivo (sin esperar al cierre de vela)."""
        if quote.symbol not in self.config.symbols:
            return
        async with self._lock:
            sim = self._sim()
            if sim is not None:
                sim.set_quote(quote)
            async with db.session_scope() as s:
                positions = await self._open_positions(s, quote.symbol)
            for pos in positions:
                purpose = exit_purpose_for_price(
                    stop=pos.stop_price, target=pos.target_price, bid=quote.bid
                )
                if purpose:
                    await self.manager.submit_exit(pos.id, purpose, now=self.clock())

    async def on_candle(self, c: Candle) -> None:
        """Punto de entrada del pipeline. Solo velas cerradas y de símbolos configurados."""
        if not c.is_closed or c.symbol not in self.config.symbols:
            return
        async with self._lock:
            now = self.clock()
            self._marks[c.symbol] = c.close
            self._sync_exchange(c)
            await self._refresh_risk(now)
            await self._manage_positions(c, now)
            await self._evaluate_strategies(c, now)

    def _sync_exchange(self, c: Candle) -> None:
        adapter = self._sim()
        if adapter is None:
            return
        quote = self.feed.quotes.get(c.symbol)
        # Liquidez disponible: fracción del volumen de la vela (supuesto prudente).
        avail = c.volume * self.config.max_volume_participation
        if quote is not None:
            adapter.set_quote(quote, available_qty=avail)
        else:
            adapter.set_price(c.symbol, c.close, available_qty=avail)
        adapter.advance(c.symbol, low=c.low, high=c.high, available_qty=avail)

    # ================================================================ salidas

    async def _open_positions(self, s: Any, symbol: str | None = None) -> list[m.Position]:
        q = select(m.Position).where(
            m.Position.mode == self.config.mode, m.Position.status == "OPEN"
        )
        if symbol:
            q = q.where(m.Position.symbol == symbol)
        return list((await s.execute(q)).scalars())

    async def _manage_positions(self, c: Candle, now: datetime) -> None:
        async with db.session_scope() as s:
            positions = await self._open_positions(s, c.symbol)
            strategies = {r.id: r for r in (await s.execute(select(m.Strategy))).scalars()}
        for pos in positions:
            purpose = exit_purpose_for_bar(
                stop=pos.stop_price, target=pos.target_price, low=c.low, high=c.high
            )
            if purpose is None:
                row = strategies.get(pos.strategy_id)
                max_bars = int((row.params if row else {}).get("max_hold_bars", 0) or 0)
                if max_bars and time_exit_due(
                    opened_at=pos.opened_at,
                    now=now,
                    max_hold=interval_delta(c.interval) * max_bars,
                ):
                    purpose = "EXIT_TIME"
            if purpose:
                await self.manager.submit_exit(pos.id, purpose, now=now)

    # ================================================================ señales

    async def _enabled_strategies(
        self, interval: str, symbol: str
    ) -> list[tuple[str, Strategy[Any]]]:
        async with db.session_scope() as s:
            rows = (
                (
                    await s.execute(
                        select(m.Strategy)
                        .where(m.Strategy.enabled.is_(True))
                        .order_by(m.Strategy.id)
                    )
                )
                .scalars()
                .all()
            )
        out = []
        for r in rows:
            if r.interval == interval and symbol in r.symbols and r.id in STRATEGIES:
                out.append((r.id, self.strategy_builder(r.id, dict(r.params))))
        return out

    async def _frame(self, c: Candle, strategy: Strategy[Any]) -> Any:
        step = interval_delta(c.interval)
        n = max(self.config.history_bars, strategy.warmup + 50)
        async with db.session_scope() as s:
            df = await store.load_frame(
                s, c.symbol, c.interval, c.open_time - step * n, c.open_time
            )
        if df.empty or df.index[-1].to_pydatetime() != c.open_time:
            return None  # la vela aún no está persistida: jamás se evalúa con datos incompletos
        return df

    async def _evaluate_strategies(self, c: Candle, now: datetime) -> None:
        ready, why = self.feed.readiness(c.symbol, c.interval)
        if not ready:
            log.info("sin señales: datos no aptos", extra={"symbol": c.symbol, "reason": why})
            return
        candidates: list[tuple[Signal, Strategy[Any]]] = []
        for sid, strat in await self._enabled_strategies(c.interval, c.symbol):
            df = await self._frame(c, strat)
            if df is None:
                continue
            ev = strat.evaluate(df)
            if bool(ev.iloc[-1]["exit"]):
                await self._exit_by_strategy(c.symbol, sid, now)
            signal = strat.signal_at(c.symbol, c.interval, df, ev)
            if signal is not None:
                candidates.append((signal, strat))
        if not candidates:
            return
        # Coordinación: una entrada por símbolo y vela; gana la mayor puntuación.
        candidates.sort(key=lambda t: (-(t[0].score or 0.0), t[0].strategy_id))
        winner, *losers = (sig for sig, _ in candidates)
        async with db.session_scope() as s:
            for sig in losers:
                await record_signal(s, self.config.mode, sig, "SUPERSEDED")
        await self._handle_entry(winner, now, c.interval)

    async def _exit_by_strategy(self, symbol: str, strategy_id: str, now: datetime) -> None:
        async with db.session_scope() as s:
            positions = await self._open_positions(s, symbol)
        for pos in positions:
            if pos.strategy_id == strategy_id:
                await self.manager.submit_exit(pos.id, "EXIT_SIGNAL", now=now)

    async def _has_unknown_orders(self) -> bool:
        async with db.session_scope() as s:
            row = (
                await s.execute(
                    select(m.OrderRow.id).where(
                        m.OrderRow.mode == self.config.mode, m.OrderRow.status == "UNKNOWN"
                    )
                )
            ).first()
        return row is not None

    async def _handle_entry(self, signal: Signal, now: datetime, interval: str) -> None:
        mode = self.config.mode
        snap = await self._refresh_risk(now)
        ready, why = self.feed.readiness(signal.symbol, interval)
        reconciled = (
            self.last_reconcile is not None
            and self.last_reconcile.ok
            and not await self._has_unknown_orders()
        )
        market = MarketContext(
            data_ready=ready,
            data_reason=why,
            connections_ok=self.feed.connected,
            reconciliation_ok=reconciled,
            quote=self.feed.quotes.get(signal.symbol),
            correlations=self._correlations,
        )
        async with db.session_scope() as s:
            state = await load_risk_state(s, mode)
            if state is None:
                raise RuntimeError("Estado de riesgo no inicializado")
            decision = self.manager.risk_engine.evaluate(
                signal,
                now=now,
                state=state,
                portfolio=snap.portfolio,
                rules=self.manager.rules[signal.symbol],
                market=market,
            )
            await save_risk_state(s, mode, state)  # update_latches pudo cambiarlo
            await record_signal_and_decision(s, mode, signal, decision)
        if not decision.approved:
            log.info(
                "señal rechazada por riesgo",
                extra={"symbol": signal.symbol, "reasons": decision.reasons},
            )
            return
        await self.manager.submit_entry(signal, decision, now=now)

    # ================================================================ correlaciones

    async def refresh_correlations(self, interval: str = "1m", window: int = 1440) -> None:
        syms = self.config.symbols
        if len(syms) < 2:
            return
        end = self.clock()
        start = end - interval_delta(interval) * window
        returns = {}
        async with db.session_scope() as s:
            for sym in syms:
                df = await store.load_frame(s, sym, interval, start, end)
                if len(df) >= window // 2:
                    returns[sym] = df["close"].pct_change().dropna()
        corr: dict[tuple[str, str], float] = {}
        names = sorted(returns)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                joined = returns[a].to_frame("a").join(returns[b].to_frame("b"), how="inner")
                if len(joined) >= window // 2:
                    val = float(np.corrcoef(joined["a"], joined["b"])[0, 1])
                    if not np.isnan(val):
                        corr[(a, b)] = val
        self._correlations = corr

    # ================================================================ reconciliación y comandos

    async def run_reconciliation(self) -> ReconciliationReport:
        async with self._lock:
            report = await reconcile(
                self.manager,
                baseline=self._baseline,
                balance_tolerance_pct=self.config.balance_tolerance_pct,
            )
        self.last_reconcile = report
        return report

    def _resume_context(self, equity: Decimal) -> ResumeContext:
        problems = []
        for sym in self.config.symbols:
            ok, why = self.feed.readiness(sym, "1m")
            if not ok:
                problems.append(f"{sym}: {why}")
        return ResumeContext(
            connections_ok=self.feed.connected,
            data_ready=not problems,
            data_reason="; ".join(problems) or "ok",
            equity=equity,
        )

    async def process_commands(self) -> int:
        """Aplica los comandos pendientes del panel. Devuelve cuántos procesó."""
        async with db.session_scope() as s:
            pending = list(
                (
                    await s.execute(
                        select(m.BotCommand)
                        .where(m.BotCommand.status == "PENDING")
                        .order_by(m.BotCommand.created_at)
                    )
                ).scalars()
            )
        for cmd in pending:
            try:
                ok, result = await self._apply_command(cmd)
            except Exception as exc:  # un comando defectuoso no puede tumbar el engine
                log.exception("fallo aplicando comando")
                ok, result = False, f"error interno: {type(exc).__name__}"
            async with db.session_scope() as s:
                row = await s.get(m.BotCommand, cmd.id)
                assert row is not None
                row.status = "APPLIED" if ok else "REJECTED"
                row.result = result
                row.processed_at = utcnow()
        return len(pending)

    async def _apply_command(self, cmd: m.BotCommand) -> tuple[bool, str]:
        mode, actor = self.config.mode, cmd.created_by
        # Las órdenes que aflojan restricciones caducan: una reanudación pedida mientras el
        # engine estaba caído no debe ejecutarse por sorpresa minutos u horas después.
        # Pausa y kill switch (que endurecen) se aplican siempre, por antiguas que sean.
        if cmd.command in ("RESUME", "ENABLE_STRATEGY") and (
            utcnow() - cmd.created_at > COMMAND_TTL
        ):
            return False, "solicitud caducada (más de 10 min sin procesar): repítela"
        if cmd.command == "PAUSE":
            r = await pause_entries(mode, actor)
            return r.ok, r.message
        if cmd.command == "KILL_SWITCH":
            policy = KillSwitchPolicy(
                liquidate_positions=bool(cmd.payload.get("liquidate_positions", False))
            )
            r = await kill_switch(self.manager, actor, policy)
            return r.ok, r.message
        if cmd.command == "RESUME":
            snap = await self.snapshot()
            r = await resume(
                self.manager,
                actor,
                self._resume_context(snap.equity),
                clear_kill_switch=bool(cmd.payload.get("clear_kill_switch", False)),
            )
            self.last_reconcile = None if not r.ok else self.last_reconcile
            return r.ok, r.message + ("" if r.ok else ": " + "; ".join(r.reasons))
        if cmd.command in ("ENABLE_STRATEGY", "DISABLE_STRATEGY"):
            sid = str(cmd.payload.get("strategy_id", ""))
            enable = cmd.command == "ENABLE_STRATEGY"
            async with db.session_scope() as s:
                row = await s.get(m.Strategy, sid)
                if row is None:
                    return False, f"estrategia desconocida: {sid}"
                before = {"enabled": row.enabled}
                row.enabled = enable
                await audit(
                    s, actor=actor, action="strategy.enable" if enable else "strategy.disable",
                    target=sid, before=before, after={"enabled": enable},
                )  # fmt: skip
            return True, f"{sid} {'activada' if enable else 'desactivada'}"
        return False, f"comando desconocido: {cmd.command}"

    # ================================================================ latido y bucles

    async def heartbeat(self) -> None:
        mode = self.config.mode
        now = utcnow()
        async with db.session_scope() as s:
            state = await load_risk_state(s, mode)
            hb = await s.get(m.EngineHeartbeat, "engine")
            payload = {
                "connected": self.feed.connected,
                "paused": bool(state and state.paused),
                "kill_switch": bool(state and state.kill_switch_active),
                "latched": list(state.latched_reasons) if state else [],
                "reconciliation_ok": bool(self.last_reconcile and self.last_reconcile.ok),
                "last_reconciliation_at": (
                    self.last_reconcile.checked_at.isoformat() if self.last_reconcile else None
                ),
            }
            if hb is None:
                s.add(
                    m.EngineHeartbeat(
                        id="engine", mode=mode, started_at=self.started_at, last_beat_at=now,
                        pid=os.getpid(), state=payload,
                    )
                )  # fmt: skip
            else:
                hb.mode, hb.started_at, hb.last_beat_at = mode, self.started_at, now
                hb.pid, hb.state = os.getpid(), payload

    async def save_balance_snapshot(self) -> None:
        snap = await self.snapshot()
        async with db.session_scope() as s:
            s.add(
                m.BalanceSnapshot(
                    mode=self.config.mode,
                    at=utcnow(),
                    cash=snap.cash,
                    equity=snap.equity,
                    realized_pnl=snap.realized_pnl,
                    unrealized_pnl=snap.unrealized_pnl,
                    fees_total=snap.fees_total,
                )
            )

    async def _loop(self, interval: float, fn: Callable[[], Any], name: str) -> None:
        while not self._stop.is_set():
            try:
                await fn()
            except Exception as exc:
                log.exception("fallo en el bucle %s", name)
                try:
                    async with db.session_scope() as s:
                        await raise_alert(
                            s,
                            severity=Severity.WARNING,
                            code="engine_loop_error",
                            message=f"Error en el bucle {name}: {type(exc).__name__}",
                            recommended_action="Revisa los logs del engine.",
                            dedup_key=f"engine_loop_error:{name}",
                        )
                except Exception:
                    log.exception("no se pudo registrar la alerta de error")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=interval)

    async def run(self) -> None:
        """Bucles auxiliares (latido, comandos, reconciliación, snapshots, correlaciones)."""
        cfg = self.config
        tasks = [
            asyncio.create_task(self._loop(cfg.heartbeat_interval, self.heartbeat, "heartbeat")),
            asyncio.create_task(
                self._loop(cfg.command_poll_interval, self.process_commands, "commands")
            ),
            asyncio.create_task(
                self._loop(cfg.reconcile_interval, self.run_reconciliation, "reconcile")
            ),
            asyncio.create_task(
                self._loop(cfg.snapshot_interval, self.save_balance_snapshot, "snapshots")
            ),
            asyncio.create_task(self._loop(900.0, self.refresh_correlations, "correlations")),
        ]
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            async with db.session_scope() as s:
                await system_event(
                    s, kind="engine_stopped", source="engine", detail={"clean": True}
                )
                await resolve_alert(s, "engine_unexpected_restart")

    def stop(self) -> None:
        self._stop.set()
