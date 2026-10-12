import type { Command, CommandAccepted, Overview } from "../api/types";

export function overview(over: Partial<Overview> = {}): Overview {
  return {
    mode: "PAPER",
    mode_matches_engine: true,
    data_origin: "Cuenta simulada local; precios reales de mercado",
    as_of: "2026-03-02T12:00:00Z",
    engine: { status: "running", mode: "PAPER", last_beat_at: "2026-03-02T11:59:58Z", age_seconds: 2, started_at: "2026-03-02T10:00:00Z", state: {} },
    account: {
      currency: "USDT", initial_capital: "500", cash: "300.5", equity: "498.7", realized_pnl: "-1.3", unrealized_pnl: "0",
      fees_total: "0.4", return_pct: "-0.26", marks_stale: false, valuation: "posiciones a último cierre 1m",
    },
    drawdown: { current: "1.3", max_observed: "1.3", limit: "25", reference: "pico → actual" },
    open_positions: 1,
    closed_trades: 2,
    recent_trades: [],
    active_strategies: ["momentum"],
    connections: [{ name: "binance-ws", status: "UP", last_message_at: null, last_valid_data_at: "2026-03-02T11:59:00Z", detail: {} }],
    last_data_at: "2026-03-02T11:59:00Z",
    critical_alerts: [],
    risk: {
      limits: {
        risk_per_trade: "1.25", max_daily_loss: "5", max_drawdown: "25", max_positions: 2, max_exposure: "250",
        max_trades_per_day: 20, max_consecutive_losses: 5, max_spread_bps: "10", max_execution_errors: 3,
        long_only_spot: true, leverage: 1,
      },
      state: {
        trading_day: "2026-03-02", paused: false, kill_switch_active: false, latched_reasons: [], consecutive_losses: 0,
        entries_today: 1, execution_errors: 0, cooldown_until_day: null, entries_allowed: true,
      },
      usage: { daily_loss: "1.3", drawdown: "1.3", peak_equity: "500", day_start_equity: "500", open_positions: 1, exposure: "120" },
    },
    ...over,
  };
}

export const accepted = (over: Partial<CommandAccepted> = {}): CommandAccepted => ({
  command_id: "c1", status: "PENDING", engine_alive: true, duplicate_of_pending: false,
  detail: "Solicitud registrada; pendiente de que el engine la confirme.", ...over,
});

export const command = (over: Partial<Command> = {}): Command => ({
  id: "c1", created_at: "2026-03-02T12:00:00Z", created_by: "admin", command: "KILL_SWITCH", payload: {},
  status: "PENDING", result: null, processed_at: null, ...over,
});
