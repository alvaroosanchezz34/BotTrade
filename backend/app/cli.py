"""CLI de operación: ``python -m app.cli <comando>``."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.persistence import db


async def _create_admin(username: str) -> int:
    from app.auth.service import create_user

    password = getpass.getpass("Contraseña (mín. 12 caracteres): ")
    if password != getpass.getpass("Repite la contraseña: "):
        print("Las contraseñas no coinciden", file=sys.stderr)
        return 1
    settings = get_settings()
    db.init_engine(settings.database_url.get_secret_value())
    try:
        async with db.session_scope() as s:
            await create_user(s, username, password, role="admin")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        await db.dispose_engine()
    print(f"Usuario administrador '{username}' creado")
    return 0


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


async def _download_history(
    symbols: list[str], intervals: list[str], start: datetime, end: datetime, use_archive: bool
) -> int:
    from app.market_data import store
    from app.market_data.history import ensure_history
    from app.providers.binance.archive import BinanceArchiveClient
    from app.providers.binance.rest import BinanceMarketDataClient

    settings = get_settings()
    db.init_engine(settings.database_url.get_secret_value())
    rest = BinanceMarketDataClient(settings.market_data_rest_url)
    archive = (
        BinanceArchiveClient(settings.market_data_archive_url, Path(settings.data_dir) / "archive")
        if use_archive
        else None
    )
    exit_code = 0
    try:
        rules = await rest.exchange_info(symbols)
        async with db.session_scope() as s:
            await store.save_instruments(s, rules, provider="binance")
        for sym in symbols:
            for itv in intervals:
                rep = await ensure_history(
                    symbol=sym, interval=itv, start=start, end=end, rest=rest, archive=archive
                )
                print(
                    f"{sym} {itv}: +{rep.inserted} velas, {len(rep.files)} ficheros verificados, "
                    f"{rep.missing_candles} velas sin datos en el origen"
                )
                for g in rep.remaining_gaps[:10]:
                    print(
                        f"   hueco {g.start.isoformat()} → {g.end.isoformat()} ({g.missing_count()})"
                    )
    finally:
        await rest.aclose()
        if archive:
            await archive.aclose()
        await db.dispose_engine()
    return exit_code


async def _load_frames(
    symbols: list[str], interval: str, start: datetime, end: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    from app.market_data import store

    async with db.session_scope() as s:
        frames = {sym: await store.load_frame(s, sym, interval, start, end) for sym in symbols}
        rules = await store.load_instruments(s)
        gaps = {sym: await store.missing_ranges(s, sym, interval, start, end) for sym in symbols}
    for sym, g in gaps.items():
        if g:
            print(
                f"AVISO {sym} {interval}: {sum(x.missing_count() for x in g)} velas faltantes en el rango"
            )
    missing_rules = [sym for sym in symbols if sym not in rules]
    if missing_rules:
        raise SystemExit(
            f"Faltan reglas de instrumento para {missing_rules}: ejecuta download-history"
        )
    return frames, rules


async def _research(
    strategies: list[str],
    intervals: list[str],
    symbols: list[str],
    start: datetime,
    end: datetime,
    report_md: Path,
    report_dir: Path,
    seeds: int,
) -> int:
    from datetime import timedelta

    from app.backtesting import research as rs
    from app.backtesting.engine import dataset_hash
    from app.backtesting.report import export_json, markdown_report, save_result
    from app.risk.sizing import CostModel

    settings = get_settings()
    db.init_engine(settings.database_url.get_secret_value())
    costs = CostModel(
        taker_fee=settings.taker_fee_rate,
        maker_fee=settings.maker_fee_rate,
        slippage=settings.slippage_bps / 10_000,
    )
    results = []
    hashes: dict[str, str] = {}
    try:
        for interval in intervals:
            # 10 días previos de contexto para que los indicadores estén calientes al empezar.
            frames, rules = await _load_frames(symbols, interval, start - timedelta(days=10), end)
            hashes[interval] = dataset_hash(frames)
            rs.set_data(frames, rules)
            for sid in strategies:
                print(f"→ {sid} {interval} …", flush=True)
                r = rs.research_strategy(
                    sid,
                    symbols=tuple(symbols),
                    interval=interval,
                    start=start,
                    end=end,
                    costs=costs,
                    seeds=range(seeds),
                )
                print(
                    f"   {r.verdict}: prueba {r.test['trades']} ops, neto {r.test['net_pnl']:.2f}",
                    flush=True,
                )
                results.append(r)
                async with db.session_scope() as s:
                    await save_result(
                        s,
                        r.test_result,
                        r.test,
                        segment="test",
                        notes=f"{r.verdict}: " + "; ".join(r.verdict_reasons),
                    )
    finally:
        await db.dispose_engine()
    meta = {
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "symbols": symbols,
        "costs": costs.as_dict(),
        "capital": str(settings.reference_capital),
        "data_hashes": hashes,
        "random_seeds": seeds,
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    export_json(results, meta, report_dir / f"research-{stamp}.json")
    report_md.parent.mkdir(parents=True, exist_ok=True)
    report_md.write_text(markdown_report(results, meta), encoding="utf-8")  # noqa: ASYNC240
    print(f"Informe: {report_md} · datos: {report_dir / f'research-{stamp}.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bottrade")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_admin = sub.add_parser("create-admin", help="Crea un usuario administrador")
    p_admin.add_argument("username")

    p_hist = sub.add_parser("download-history", help="Descarga y verifica velas históricas")
    p_hist.add_argument("--symbols", default=None, help="Por defecto, BOTTRADE_SYMBOLS")
    p_hist.add_argument("--intervals", default=None, help="Por defecto, BOTTRADE_INTERVALS")
    p_hist.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    p_hist.add_argument("--end", default=None, help="YYYY-MM-DD (UTC), por defecto ahora")
    p_hist.add_argument("--no-archive", action="store_true", help="Solo REST")

    p_res = sub.add_parser("research", help="Protocolo de backtest: rejilla, OOS, walk-forward")
    p_res.add_argument("--strategies", default="momentum,trend,mean_reversion")
    p_res.add_argument("--intervals", default=None)
    p_res.add_argument("--symbols", default=None)
    p_res.add_argument("--start", required=True)
    p_res.add_argument("--end", required=True)
    p_res.add_argument("--seeds", type=int, default=10, help="Semillas del benchmark aleatorio")
    p_res.add_argument("--report", default="../docs/backtesting-results.md")
    p_res.add_argument("--report-dir", default="../reports")

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)

    if args.cmd == "create-admin":
        return asyncio.run(_create_admin(args.username))
    if args.cmd == "download-history":
        symbols = args.symbols.upper().split(",") if args.symbols else settings.symbols
        intervals = args.intervals.split(",") if args.intervals else settings.intervals
        end = _parse_date(args.end) if args.end else datetime.now(UTC)
        return asyncio.run(
            _download_history(symbols, intervals, _parse_date(args.start), end, not args.no_archive)
        )
    if args.cmd == "research":
        return asyncio.run(
            _research(
                args.strategies.split(","),
                args.intervals.split(",") if args.intervals else settings.intervals,
                args.symbols.upper().split(",") if args.symbols else settings.symbols,
                _parse_date(args.start),
                _parse_date(args.end),
                Path(args.report),
                Path(args.report_dir),
                args.seeds,
            )
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
