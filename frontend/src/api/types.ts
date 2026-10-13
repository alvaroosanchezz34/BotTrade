/** Contratos del backend. Los importes llegan como cadenas Decimal (precisión exacta). */

export type Mode = "BACKTEST" | "PAPER" | "TESTNET";
export type Dec = string;

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Me {
  username: string;
  role: "admin" | "viewer";
}

export interface EngineView {
  status: "running" | "stopped" | "never_started";
  mode?: Mode;
  started_at?: string;
  last_beat_at: string | null;
  age_seconds: number | null;
  state: Record<string, unknown> | null;
}

export interface Trade {
  id: string;
  symbol: string;
  strategy_id: string;
  quantity: Dec;
  entry_price: Dec;
  exit_price: Dec | null;
  gross_pnl: Dec;
  fees: Dec;
  net_pnl: Dec;
  duration_seconds: number;
  opened_at: string;
  closed_at: string;
  entry_reason: string;
  exit_reason: string | null;
  signal_id: string | null;
}

export interface RiskSummary {
  limits: {
    risk_per_trade: Dec;
    max_daily_loss: Dec;
    max_drawdown: Dec;
    max_positions: number;
    max_exposure: Dec;
    max_trades_per_day: number;
    max_consecutive_losses: number;
    max_spread_bps: Dec;
    max_execution_errors: number;
    long_only_spot: boolean;
    leverage: number;
  };
  state: {
    trading_day: string;
    paused: boolean;
    kill_switch_active: boolean;
    latched_reasons: string[];
    consecutive_losses: number;
    entries_today: number;
    execution_errors: number;
    cooldown_until_day: string | null;
    entries_allowed: boolean;
  } | null;
  usage: {
    daily_loss: Dec;
    drawdown: Dec;
    peak_equity: Dec;
    day_start_equity: Dec;
    open_positions: number;
    exposure: Dec;
  } | null;
}

export interface Overview {
  mode: Mode;
  mode_matches_engine: boolean;
  data_origin: string;
  as_of: string;
  engine: EngineView;
  account: {
    currency: string;
    initial_capital: Dec;
    cash: Dec;
    equity: Dec;
    realized_pnl: Dec;
    unrealized_pnl: Dec;
    fees_total: Dec;
    return_pct: Dec;
    marks_stale: boolean;
    valuation: string;
  } | null;
  drawdown: { current: Dec | null; max_observed: Dec; limit: Dec; reference: string } | null;
  open_positions: number | null;
  closed_trades: number;
  recent_trades: Trade[];
  active_strategies: string[];
  connections: {
    name: string;
    status: string;
    last_message_at: string | null;
    last_valid_data_at: string | null;
    detail: Record<string, unknown>;
  }[];
  last_data_at: string | null;
  critical_alerts: {
    id: string;
    code: string;
    message: string;
    recommended_action: string;
    last_seen_at: string;
    occurrences: number;
  }[];
  risk: RiskSummary;
}

export interface Signal {
  id: string;
  created_at: string;
  symbol: string;
  strategy_id: string;
  strategy_version: string;
  action: string;
  status: string;
  reference_price: Dec;
  stop_price: Dec | null;
  target_price: Dec | null;
  score: number | null;
  score_is_calibrated: boolean;
  rationale: string;
  invalidation: string;
  risk_approved: boolean | null;
  risk_reasons: string[];
  risk_quantity: Dec | null;
}

export interface MarketRow {
  symbol: string;
  price: Dec | null;
  price_as_of: string | null;
  price_source: string;
  data_status: "al_dia" | "obsoleto" | "sin_datos";
  data_age_seconds: number | null;
  change_24h_pct: number | null;
  volume_24h: Dec | null;
  volatility_1h_pct: number | null;
  strategies: string[];
  latest_signals: Signal[];
}

export interface Candles {
  symbol: string;
  interval: string;
  source: string;
  candles: { t: number; o: Dec; h: Dec; l: Dec; c: Dec; v: Dec }[];
  trade_markers: { time: string; type: "entry" | "exit"; price: Dec }[];
}

export interface Order {
  id: string;
  client_order_id: string;
  created_at: string;
  symbol: string;
  side: string;
  type: string;
  purpose: string;
  status: string;
  quantity: Dec;
  limit_price: Dec | null;
  filled_qty: Dec;
  avg_fill_price: Dec | null;
  fees: Dec;
  strategy_id: string | null;
  last_error: string | null;
  signal_id: string | null;
  risk_decision_id: string | null;
}

export interface OrderDetail {
  order: Order;
  signal: Signal | null;
  events: { at: string; from: string | null; to: string; detail: Record<string, unknown> }[];
  fills: { trade_id: string; price: Dec; quantity: Dec; fee: Dec; fee_asset: string; at: string }[];
}

export interface Position {
  id: string;
  symbol: string;
  status: string;
  strategy_id: string;
  quantity: Dec;
  entry_quantity: Dec;
  avg_entry_price: Dec;
  stop_price: Dec | null;
  target_price: Dec | null;
  mark_price: Dec | null;
  unrealized_pnl: Dec | null;
  realized_pnl: Dec | null;
  entry_fees: Dec;
  exit_fees: Dec;
  entry_reason: string;
  opened_at: string;
}

export interface PerfStats {
  trades: number;
  net_pnl: Dec;
  fees: Dec;
  win_rate: number | null;
  avg_win: Dec | null;
  avg_loss: Dec | null;
  expectancy_net: Dec | null;
  profit_factor: Dec | null;
  avg_duration_seconds: number | null;
  largest_win_share: number | null;
}

export interface Performance {
  mode: Mode;
  overall: PerfStats;
  open_positions_excluded: number;
  by_strategy: Record<string, PerfStats>;
  by_symbol: Record<string, PerfStats>;
  max_drawdown_observed: Dec | null;
  equity_curve: { at: string; equity: Dec }[];
  sample_warning: string | null;
  notes: string;
}

export interface StrategyListItem {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  symbols: string[];
  interval: string;
  version: string;
  last_signal_at: string | null;
  last_backtest_at: string | null;
  last_backtest_verdict: string | null;
}

export interface BacktestBrief {
  id: string;
  created_at: string;
  segment: string;
  interval: string;
  symbols: string[];
  start: string;
  end: string;
  strategy_version: string;
  data_hash: string;
  metrics: Record<string, number | string | null>;
  notes: string;
}

export interface StrategyDetail extends StrategyListItem {
  params: Record<string, unknown>;
  params_editable: boolean;
  indicators: string[] | null;
  code_hash: string | null;
  versions: { version: string; code_hash: string; created_at: string }[];
  recent_signals: Signal[];
  open_positions: number;
  live_stats: PerfStats & { mode: Mode };
  recent_trades: Trade[];
  backtests: BacktestBrief[];
  out_of_sample: BacktestBrief[];
  note: string;
}

export interface Alert {
  id: string;
  severity: "INFO" | "WARNING" | "CRITICAL";
  code: string;
  message: string;
  context: Record<string, unknown>;
  recommended_action: string;
  occurrences: number;
  created_at: string;
  last_seen_at: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  resolved_at: string | null;
}

export interface Command {
  id: string;
  created_at: string;
  created_by: string;
  command: string;
  payload: Record<string, unknown>;
  status: "PENDING" | "APPLIED" | "REJECTED";
  result: string | null;
  processed_at: string | null;
}

export interface CommandAccepted {
  command_id: string;
  status: string;
  engine_alive: boolean;
  duplicate_of_pending: boolean;
  detail: string;
}

export interface RiskDetail extends RiskSummary {
  marks_stale: boolean;
  recent_decisions: { id: string; approved: boolean; at: string; reasons: string[] }[];
  recent_rejection_counts: Record<string, number>;
  policy: string;
  editable: boolean;
}

export interface SystemStatus {
  status: string;
  mode: Mode;
  checked_at: string;
  components: Record<string, { status: string; detail: string; last_ok?: string | null }>;
  engine: EngineView;
  environment: string;
  symbols: string[];
  intervals: string[];
  real_money_enabled: boolean;
}

export interface Pulse {
  mode: Mode;
  engine: "running" | "stopped" | "never_started";
  paused: boolean;
  kill_switch: boolean;
  entries_allowed: boolean;
  equity: string | null;
  marks_stale: boolean;
  open_positions: number | null;
  critical_alerts: number;
  last_order_at: string | null;
  last_signal_at: string | null;
  commands: { id: string; command: string; status: string; result: string | null }[];
}

export interface BacktestListItem {
  id: string;
  created_at: string;
  strategy_id: string;
  strategy_version: string;
  symbols: string[];
  interval: string;
  start: string;
  end: string;
  segment: string;
  notes: string;
  initial_capital: Dec;
  final_capital: Dec;
  closed_trades: number;
}

export interface BacktestDetail {
  id: string;
  created_at: string;
  strategy_id: string;
  strategy_version: string;
  symbols: string[];
  interval: string;
  start: string;
  end: string;
  segment: string;
  notes: string;
  data_hash: string;
  seed: number;
  disclaimer: string;
  summary: {
    initial_capital: Dec;
    final_capital: Dec;
    net_pnl: Dec;
    return_pct: Dec | null;
    closed_trades: number;
    win_rate: number | null;
    max_drawdown: Dec;
    max_drawdown_pct: Dec | null;
    best_trade: Dec | null;
    worst_trade: Dec | null;
    curve: { at: string | null; equity: Dec }[];
  };
  trades: {
    symbol: string;
    entry_time: string;
    exit_time: string | null;
    entry_price: Dec;
    exit_price: Dec | null;
    quantity: Dec;
    fees: Dec;
    net_pnl: Dec | null;
    exit_reason: string | null;
  }[];
}

export interface LabParamSpec {
  name: string;
  type: "integer" | "number";
  default: number | null;
  min: number | null;
  min_exclusive: boolean;
  max: number | null;
  description: string;
}

export interface LabOptions {
  strategies: { id: string; name: string; description: string; params: LabParamSpec[] }[];
  symbols: string[];
  intervals: ("1m" | "5m")[];
  months: number[];
  max_months: Record<string, number>;
  cost_levels: Record<string, { taker_fee: string; slippage: string; spread: string }>;
  data: Record<string, Record<string, { from: string; to: string; bars: number }>>;
  limits: { active_jobs_per_user: number; jobs_per_hour: number; min_trades_for_verdict: number };
  disclaimer: string;
}

export interface LabRequest {
  strategy_id: string;
  symbols: string[];
  interval: "1m" | "5m";
  months: number;
  params: Record<string, number>;
  cost_level: "normal" | "conservador";
}

export interface LabMetrics {
  trades: number;
  net_pnl: number;
  fees: number;
  win_rate: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  expectancy_ci95: [number, number] | null;
  max_drawdown_pct: number | null;
  return_pct: number | null;
  final_equity: number | null;
}

export interface LabReport {
  verdict: "PROMETEDORA" | "SIN EVIDENCIA DE VENTAJA" | "NO CONCLUYENTE";
  checks: { name: string; passed: boolean; detail: string }[];
  spec: { strategy_id: string; symbols: string[]; interval: string; start: string; end: string; params: Record<string, number>; cost_level: string };
  effective_params: Record<string, unknown>;
  main: LabMetrics;
  thirds: { label: string; start: string; end: string; trades: number; net_pnl: number; profit_factor: number | null; return_pct: number | null }[];
  stress: LabMetrics;
  limited: LabMetrics;
  random: { n: number; net_min: number | null; net_median: number | null; net_max: number | null };
  buy_hold: { return_pct: number | null; max_drawdown_pct?: number | null };
  variants_tried: number;
  multiple_testing_note: string | null;
  data: { hash: string; bars: number; missing_bars?: number };
  caveats: string[];
}

export interface LabJobOut {
  id: string;
  status: "QUEUED" | "RUNNING" | "DONE" | "FAILED";
  progress: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  spec: LabReport["spec"];
  variants_tried: number | null;
  run_id: string | null;
  error: string | null;
  verdict: LabReport["verdict"] | null;
  report: LabReport | null;
}
