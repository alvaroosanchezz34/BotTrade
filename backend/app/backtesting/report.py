"""Persistencia y exportación de resultados de investigación."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.backtesting.engine import BacktestResult
from app.backtesting.research import StrategyResearch
from app.persistence.models import BacktestRun, BacktestTrade


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(type(o))


async def save_result(
    session: AsyncSession,
    res: BacktestResult,
    metrics: dict[str, Any],
    *,
    segment: str,
    parent_id: Any = None,
    notes: str = "",
) -> BacktestRun:
    cfg = res.config
    run = BacktestRun(
        strategy_id=res.strategy["id"],
        strategy_version=res.strategy["version"],
        symbols=list(cfg.symbols),
        interval=cfg.interval,
        start=cfg.start,
        end=cfg.end,
        segment=segment,
        params=json.loads(json.dumps(res.strategy["params"], default=_json_default)),
        cost_model=cfg.as_dict(),
        data_hash=res.data_hash,
        seed=cfg.seed,
        status="DONE",
        metrics=json.loads(json.dumps(metrics, default=_json_default)),
        parent_id=parent_id,
        notes=notes,
    )
    session.add(run)
    await session.flush()
    for t in res.trades:
        session.add(
            BacktestTrade(
                run_id=run.id,
                symbol=t.symbol,
                entry_time=t.entry_time,
                exit_time=t.exit_time,
                entry_price=t.entry_price,
                exit_price=t.exit_price,
                quantity=t.quantity,
                fees=t.fees,
                gross_pnl=t.gross_pnl,
                net_pnl=t.net_pnl,
                exit_reason=t.exit_reason,
            )
        )
    return run


def research_to_dict(r: StrategyResearch) -> dict[str, Any]:
    return {
        "strategy_id": r.strategy_id,
        "interval": r.interval,
        "grid_size": len(r.grid),
        "chosen_params": r.chosen,
        "chosen_reason": r.chosen_reason,
        "train": r.train,
        "validation": r.validation,
        "test": r.test,
        "test_with_real_limits": r.test_limited,
        "stress_costs": r.stress,
        "random_entries_test": r.random_test,
        "walk_forward": r.walk_forward,
        "walk_forward_trades": r.walk_forward_trades,
        "walk_forward_net": r.walk_forward_net,
        "buy_and_hold": r.buy_hold,
        "verdict": r.verdict,
        "verdict_reasons": r.verdict_reasons,
    }


def export_json(results: list[StrategyResearch], meta: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "results": [research_to_dict(r) for r in results]}
    path.write_text(json.dumps(payload, default=_json_default, indent=2, ensure_ascii=False))


def _fmt(v: Any, nd: int = 2, pct: bool = False) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{nd}f}{' %' if pct else ''}"
    return str(v)


def markdown_report(results: list[StrategyResearch], meta: dict[str, Any]) -> str:
    lines = [
        "# Resultados de backtesting",
        "",
        f"Generado: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"datos {meta['start']} → {meta['end']} · símbolos {', '.join(meta['symbols'])}",
        "",
        "Costes supuestos: " + ", ".join(f"{k}={v}" for k, v in meta["costs"].items()),
        f"Capital de referencia: {meta['capital']} USDT. Hash de datos por intervalo: "
        + ", ".join(f"{k}={v[:12]}…" for k, v in meta["data_hashes"].items()),
        "",
        "> Estos resultados son **históricos y simulados**. No predicen resultados futuros",
        "> ni autorizan operar con dinero real. Una estrategia «candidata» solo pasa a",
        "> paper trading prolongado (sección 23).",
        "",
        "## Resumen",
        "",
        "| Estrategia | Intervalo | Ops prueba | Neto prueba | PF prueba | DD máx. (límites reales) "
        "| Walk-forward neto | Supera aleatorio | Veredicto |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        rnd = [x["net_pnl"] for x in r.random_test]
        beaten = (sum(r.test["net_pnl"] > x for x in rnd) / len(rnd)) if rnd else None
        lines.append(
            f"| {r.strategy_id} | {r.interval} | {r.test['trades']} | {_fmt(r.test['net_pnl'])} "
            f"| {_fmt(r.test['profit_factor'])} | {_fmt(r.test_limited['max_drawdown_pct'], pct=True)} "
            f"| {_fmt(r.walk_forward_net)} ({r.walk_forward_trades} ops) "
            f"| {'—' if beaten is None else f'{beaten:.0%}'} | **{r.verdict}** |"
        )
    for r in results:
        lines += [
            "",
            f"## {r.strategy_id} · {r.interval}",
            "",
            f"Combinaciones probadas en entrenamiento: **{len(r.grid)}**. "
            f"Elegida: `{json.dumps({k: v for k, v in r.chosen.items() if k != 'filter'})}` "
            f"({r.chosen_reason}).",
            "",
            "| Tramo | Ops | Neto | Comisiones | Win rate | Expectativa | IC95 expectativa | PF "
            "| DD máx. | Sharpe | Comprar y mantener |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        train_best = max(r.train, key=lambda m: m["net_pnl"] if m["trades"] >= 20 else -1e18)
        for name, m, bh in (
            ("Entrenamiento (mejor)", train_best, r.buy_hold.get("train", {})),
            ("Validación", r.validation, r.buy_hold.get("validation", {})),
            ("Prueba", r.test, r.buy_hold.get("test", {})),
            ("Prueba, límites reales", r.test_limited, r.buy_hold.get("test", {})),
            ("Prueba, costes ×1,5/×2", r.stress, r.buy_hold.get("test", {})),
        ):
            ci = m.get("expectancy_ci95")
            lines.append(
                f"| {name} | {m['trades']} | {_fmt(m['net_pnl'])} | {_fmt(m['fees'])} "
                f"| {_fmt(m['win_rate'] * 100 if m['win_rate'] is not None else None, 1, True)} "
                f"| {_fmt(m['expectancy'], 3)} | {'—' if not ci else f'[{ci[0]:.3f}, {ci[1]:.3f}]'} "
                f"| {_fmt(m['profit_factor'])} | {_fmt(m['max_drawdown_pct'], pct=True)} "
                f"| {_fmt(m['sharpe'])} | {_fmt(bh.get('return_pct'), pct=True)} |"
            )
        lines += [
            "",
            "Sensibilidad (entrenamiento, todas las combinaciones):",
            "",
            "| Parámetros | Ops | Neto | PF | DD máx. |",
            "|---|---|---|---|---|",
        ]
        for m in r.train:
            p = {k: v for k, v in m["params"].items() if k != "filter"}
            lines.append(
                f"| `{json.dumps(p)}` | {m['trades']} | {_fmt(m['net_pnl'])} "
                f"| {_fmt(m['profit_factor'])} | {_fmt(m['max_drawdown_pct'], pct=True)} |"
            )
        if r.random_test:
            rnd = sorted(x["net_pnl"] for x in r.random_test)
            lines += [
                "",
                f"Entradas aleatorias en prueba ({len(rnd)} semillas, misma frecuencia y salidas): "
                f"neto mínimo {rnd[0]:.2f}, mediana {rnd[len(rnd) // 2]:.2f}, máximo {rnd[-1]:.2f}.",
            ]
        lines += ["", "Walk-forward (90 días de entrenamiento → 30 de prueba):", ""]
        for row in r.walk_forward:
            if "skipped" in row:
                lines.append(f"* {row['start'][:10]} → {row['end'][:10]}: {row['skipped']}")
            else:
                lines.append(
                    f"* {row['start'][:10]} → {row['end'][:10]}: {row['trades']} ops, neto "
                    f"{row['net_pnl']:.2f}, PF {_fmt(row['profit_factor'])}"
                )
        lines += ["", f"**Veredicto: {r.verdict}**", ""]
        lines += [f"* {x}" for x in r.verdict_reasons]
        rej = r.test.get("risk_rejections") or {}
        if rej:
            lines += [
                "",
                "Rechazos del motor de riesgo en prueba: "
                + ", ".join(f"{k}: {v}" for k, v in sorted(rej.items(), key=lambda kv: -kv[1])),
            ]
        exits = r.test.get("exit_reasons") or {}
        if exits:
            lines += [
                "",
                "Motivos de salida en prueba: " + ", ".join(f"{k}: {v}" for k, v in exits.items()),
            ]
    return "\n".join(lines) + "\n"
