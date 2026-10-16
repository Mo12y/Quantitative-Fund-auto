/**
 * 后端载荷类型（字段名**逐一对齐**真实响应 —— 2026-10-16 用 `/api` 实测 dump 核对）。
 * 只声明前端**真的会用**的字段；其余用可选字段或索引签名，避免过度约束后端演进。
 */

/* ── 通用 ─────────────────────────────────────────────────────── */

/** 样本充分性声明（behavior / counterfactual 共用）。
 *  ⚠️ `sufficient=false` 时界面**必须原样展示 `warning`**，不得自行给出结论。 */
export interface SampleAdequacy {
  years: number
  days: number
  sufficient: boolean
  warning: string
}

export interface ConstraintApplied {
  kind: string
  description: string
  source: string
  params: Record<string, unknown>
}

/** §4.6 三问：为什么入选 / 为什么落选 / 缺什么没评估 */
export interface ConstraintReview {
  applied: ConstraintApplied[]
  note?: string
  profile_note?: string
  overlap_note?: string
  holdings_n?: number
  mode?: 'annotate' | string
  counts: { before: number; kept: number; dropped: number; skipped: number }
  dropped: { code: string; name: string; reasons: string[] }[]
  skipped: { code: string; name: string; reasons: string[] }[]
  error?: string
}

/* ── 持仓 / 账户 ──────────────────────────────────────────────── */

export interface Holding {
  id: number
  code: string
  name?: string
  shares: number
  buy_amount: number
  current_nav: number | null
  current_value: number
  pnl: number
  /** 金额法：我的钱涨了多少（前端展示口径） */
  pnl_pct: number
  /** 净值比值法：这只基金净值涨了多少（与 pnl_pct 差一个份额舍入楔子） */
  replay_pct: number
  days_held: number
  buy_date: string
  status: string
  is_pending?: boolean
  cash_balance?: number
  curve?: { date: string; value: number }[]
}

export interface Portfolio {
  has_holdings: boolean
  total_invested: number
  total_market_value: number
  total_pnl: number
  total_return_pct: number
  alloc: Record<string, number>
  holdings: Holding[]
  realized: {
    count: number
    total_pnl: number
    total_gross: number
    total_cost: number
    total_fee: number
    dividend_total: number
    dividend_count: number
  }
  error?: string
}

export interface Stats {
  total_assets: number
  total_invested: number
  total_market_value: number
  total_pnl: number
  total_return_pct: number
  realized_pnl: number
  realized_count: number
  realized_fee: number
  realized_dividend: number
  holding_count: number
  pending_count: number
  pending_amount: number
  cash_reserve?: number
  type_alloc?: Record<string, number>
  board_alloc?: Record<string, number>
}

export interface PortfolioCurve {
  dates: string[]
  /** 组合累计收益率 %（资金加权：pnl ÷ 累计成本） */
  return_pct: number[]
  value: number[]
  cost: number[]
  pnl: number[]
  /** 同期沪深300（同起点归一，见后端 `_hs300_series`） */
  benchmark: number[]
  benchmark_name: string
  funds_used: number
  excluded_pending: number
  excluded_not_in: number
  excluded_amount: number
}

export interface PlanFund {
  code: string
  name: string
  role: string
  target: number
  target_pct: number
  invested: number
  remaining: number
  progress_pct: number
  next: { date: string; amount: number } | null
}

export interface InvestmentPlan {
  id: number
  name: string
  goal: string
  horizon: string
  risk_pref: string
  start_date: string
  total_capital: number
  total_invested: number
  cash_reserve: number
  funds: PlanFund[]
  notes?: string
}

/* ── 总览（/api/overview）—— T2.2 起不再含 temp（温度模块已归档） ── */

export interface Overview {
  portfolio: Portfolio
  stats: Stats
  curve: PortfolioCurve
  plan: InvestmentPlan | null
}

/* ── 数据链路下钻（/api/explain） ─────────────────────────────── */

export interface ExplainRow {
  label: string
  value: string
  tone?: 'flat' | 'rise' | 'fall' | string
}

export interface ExplainArtifact {
  name: string
  detail: string
}

export interface ExplainStage {
  key: 'collect' | 'clean' | 'feature' | 'model' | 'eval' | string
  title: string
  headline: string
  rows: ExplainRow[]
  artifacts: ExplainArtifact[]
}

export interface Explain {
  asof: string
  holdings_n: number
  account: Portfolio
  stages: ExplainStage[]
  error?: string
}

/* ── 研究快照（/api/research；2026-10 T2.1 由旧研究端点改名） ──── */

export interface QuantVolRow {
  model: string
  ic_mean: number | null
  icir: number | null
  qlike: number | null
  mz_beta: number | null
  dm_p_vs_base: number | null
  perm_delta: number | null
  perm_p_value: number | null
}

export interface QuantDrawdownRow {
  model: string
  threshold: number | null
  auc: number | null
  brier: number | null
  f1: number | null
  precision_pos: number | null
  recall_pos: number | null
  miss_rate: number | null
}

export interface QuantPortfolioRow {
  scheme: string
  months: number
  total_return: number
  annual_return: number
  annual_volatility: number
  sharpe: number
  calmar: number
  max_drawdown: number
  avg_position: number
  avg_turnover: number
}

export interface QuantSignal {
  month: string
  pred_vol: number | null
  vol_target_pos: number | null
  dd_triggered: number
  dd_warning_frac: number
  combined_pos: number
}

/** 研究快照（`/api/research`）：↔ `QuantModels` 结构 + `snapshot`/`note` 说明字段 */
export interface Research {
  snapshot: boolean
  note: string
  vol: QuantVolRow[]
  drawdown: QuantDrawdownRow[]
  portfolio: QuantPortfolioRow[]
  signals: QuantSignal[]
  reports: Record<string, boolean>
  error?: string
}

/* ── 定投计划（/api/dca） ─────────────────────────────────────── */

export interface DcaPlan {
  id: number
  fund_code: string
  fund_name: string
  amount_per_period: number
  frequency: string
  executed_periods: number
  expected_periods: number
  /** 是否有**到期未执行**的期数（前端据此给「待执行」提示，不是装饰） */
  due: boolean
  last_synced_at?: string | null
}

/* ── 投资计划（/api/plan） ────────────────────────────────────── */

export interface PlanPayload {
  plan: InvestmentPlan | null
  error?: string
}

/* ── 交易日历（/api/calendar） ────────────────────────────────── */

export interface CalendarPayload {
  count: number
  dates: string[]
  start: string | null
  end: string | null
  refresh_cmd: string
}

/* ── 盘中行情（/api/market/live） ─────────────────────────────── */

/** 单个指数盘中报价（东财 push2 快照）。
 *  ⚠️ `price` 缺失的条目后端**直接跳过**（不填 0）—— 填 0 会被读成"平盘"。 */
export interface MarketQuote {
  code: string
  name: string
  price: number
  /** 涨跌幅 %。缺失为 `null`（停牌等），**不是 0** */
  pct: number | null
  /** 报价时间，形如 `MM-DD HH:MM` */
  asof: string | null
}

export interface MarketLive {
  /** ⚠️ **出网失败不算错误**：`available=false` + `reason` 是**正常降级结果**。
   *  界面要如实显示"盘中行情不可用"，而不是报错、也不能拿旧值冒充实时。 */
  available: boolean
  reason: string | null
  quotes: MarketQuote[]
  /** 是否在交易时段（仅 `available=true` 时有意义） */
  trading?: boolean
  asof?: string | null
  /** 本地净值的最新日期（T+1 数据的实际截止日） */
  local_nav_latest?: string | null
  /** 本地净值落后多少天的一句话 —— 时滞要**量化**，不能只说"可能偏旧" */
  lag_note?: string
}

/* ═══════════════════════════════════════════════════════════════
 * 决策台（T2.1 新端点；契约 = 执行计划书 §3，字段按真实响应核对）
 * ═══════════════════════════════════════════════════════════════ */

/* ── /api/behavior 行为镜（契约 §3.2） ────────────────────────── */

/** 单只基金的追高对照明细（key = 基金代码） */
export interface BehaviorPerFund {
  n_buys: number
  median_trail20: number | null
  fund_median_trail20: number | null
  spread_trail20?: number
  median_pct: number | null
  fund_median_pct: number | null
  spread_pct?: number
}

export interface BehaviorBuy {
  n_buys: number
  n_distinct_buy_dates: number
  span_used: [string, string | null]
  n_with_trail: number
  median_trail20: number | null
  median_trail20_baseline: number | null
  frac_bought_after_rise: number | null
  trail_range: [number, number] | null
  n_with_pct: number
  median_pct: number | null
  median_pct_baseline: number | null
  n_high: number
  n_low: number
  frac_high: number | null
  /** ⭐ 追高对照差值（pp）：买入中位 − 同基金平常中位。**正 = 买得比平常更"涨过"** */
  chase_spread: number | null
  pct_spread: number | null
  n_funds_with_baseline: number
  per_fund: Record<string, BehaviorPerFund>
}

export interface BehaviorSell {
  n_lots: number
  n_distinct_sell_dates: number
  by_sell_date: { date: string; n_lots: number; median_ret_pct: number | null; after20_pct: number | null }[]
  median_ret_pct: number | null
  worst_pct: number | null
  worst_code: string
  worst_buy_date: string
  worst_sell_date: string
  best_pct: number | null
  best_code: string
  range: [number, number] | null
  n_loss: number
  frac_loss: number
  n_with_followup: number
  n_sold_too_early: number
  frac_sold_too_early: number
  median_after20_pct: number | null
  median_after20_pct_by_date: number | null
  lots: {
    code: string; buy_date: string; sell_date: string; held_days: number
    amount: number; ret_pct: number | null; after20: number | null
  }[]
}

export interface BehaviorFrequency {
  n_buys: number
  n_sells: number
  first_buy: string
  last_buy: string
  months: number
  buys_per_month: number
  sells_per_month: number
  monthly: Record<string, { buy: number; sell: number }>
  hold_buckets: { label: string; n: number; invested: number; median_ret_pct: number | null }[]
  median_held_days: number | null
  n_under_7d: number
}

export interface Behavior {
  span: string
  sample: SampleAdequacy
  benchmark_pct: number | null
  benchmark_name: string
  buy: BehaviorBuy
  sell: BehaviorSell
  frequency: BehaviorFrequency
  verdict: string
  notes: string[]
  error?: string
}

/* ── /api/counterfactual 反事实归因（契约 §3.3） ──────────────── */

export interface CfCase {
  label: string
  n_buys: number
  invested: number
  end_value: number
  pnl: number
  /** 定投/温度两个 case 才有；actual 无此字段 */
  pnl_pct?: number
  xirr_pct: number
  first_date?: string
  last_date?: string
  missing_nav?: number
}

export interface CounterfactualCompare {
  end_date: string
  span: string
  n_buys_total: number
  n_buys_dca: number
  dca_rule: string
  sample: SampleAdequacy
  benchmark_pct: number | null
  benchmark_name: string
  cases: { actual: CfCase; hold_all: CfCase; dca_only: CfCase; temp_tilt: CfCase }
  notes: string[]
}

export interface SameFundRow {
  code: string
  name: string
  n_buys: number
  invested: number
  span: string
  actual: CfCase
  lump: CfCase
  even_dca: CfCase
  /** 实际 − 一次性（pp）：正 = 分期比一次好 */
  vs_lump: number | null
  /** 实际 − 等额定投（pp）：正 = 随意择时比机械定投好 */
  vs_even_dca: number | null
}

export interface SameFundSummary {
  n: number
  median: number | null
  mean: number | null
  better: number
  worse: number
}

export interface CounterfactualSameFund {
  end_date: string
  funds: SameFundRow[]
  summary: {
    n_funds: number
    n_funds_total_bought: number
    skipped_few_buys: number
    skipped_small_amount: number
    invested_total: number
    vs_lump: SameFundSummary
    vs_even_dca: SameFundSummary
  }
  notes: string[]
}

export interface SellRuleRow {
  key: string
  label: string
  n_buys: number
  n_sold: number
  first_sell_date: string | null
  invested: number
  end_value: number
  proceeds: number
  total_value: number
  pnl: number
  pnl_pct: number
  xirr_pct: number
}

export interface CounterfactualSellRules {
  end_date: string
  n_buys: number
  span: string
  results: SellRuleRow[]
  notes: string[]
}

export interface Counterfactual {
  compare: CounterfactualCompare | { error: string }
  same_fund: CounterfactualSameFund | { error: string }
  sell_rules: CounterfactualSellRules | { error: string }
}

/* ── /api/breakeven 回本门槛（契约 §3.4） ─────────────────────── */

export interface BreakevenLot {
  fund_code: string
  fund_name: string
  held_days: number
  in_penalty: boolean
  days_to_free: number
  free_date: string
  buy_fee_pct: number
  redeem_fee_pct: number
  /** 门槛 %：要涨多少才不亏（= 申购费 + 现在赎回的费率） */
  threshold_pct: number
  market_value: number
  net_if_sell: number | null
  net_if_sell_pct: number | null
  redeem_cost: number
}

export interface Breakeven {
  today: string
  n_lots: number
  lots: BreakevenLot[]
  n_in_penalty: number
  penalty_value: number
  penalty_cost: number
  next_free: { fund_code: string; fund_name: string; free_date: string; days_to_free: number }[] | null
  total_market_value: number
  total_redeem_cost: number
  notes: string[]
  error?: string
}

/* ── /api/risk 风险刻画（契约 §3.5） ──────────────────────────── */

export interface RiskMetrics {
  annual_return: number
  ann_vol: number
  max_drawdown_1y: number
  max_drawdown_win: number
  max_drawdown_all: number
  momentum_3m: number
  sharpe: number
  sortino: number
  calmar: number
  downside_dev: number
  window_points: number
  window_years: number
}

export interface RiskFund {
  fund_code: string
  fund_name: string
  metrics: RiskMetrics | null
  insufficient: boolean
  reason: string | null
}

export interface RiskPayload {
  funds: RiskFund[]
  error?: string
}

/* ── /api/peer 同类分位（契约 §3.6） ──────────────────────────── */

/** ⚠️ `percentile` 是**原始分位**（越大 = 该值越大；TER 越大 = 越贵） */
export interface PeerNode {
  group: string
  group_n: number
  percentile: number | null
  insufficient_data: boolean
  reason: string | null
  nav_asof: string | null
  reliability: number | null
  reliability_note: string | null
}

export interface PeerFund {
  fund_code: string
  fund_name: string
  group: string | null
  sharpe: PeerNode | null
  max_drawdown_1y: PeerNode | null
  ter: PeerNode | null
  insufficient_data?: boolean
  reason?: string
}

export interface PeerPayload {
  asof: string | null
  funds: PeerFund[]
  error?: string
}

/* ── /api/exclusion 排雷（契约 §3.7） ─────────────────────────── */

export interface ExclusionHit {
  fund_code: string
  fund_name: string
  reasons: string[]
  sharpe_percentile: number | null
  ter_percentile: number | null
  ter?: number | null
}

export interface ExclusionClean {
  fund_code: string
  fund_name: string
  sharpe_percentile: number | null
  ter_percentile: number | null
}

export interface ExclusionPayload {
  asof: string | null
  excluded: ExclusionHit[]
  clean: ExclusionClean[]
  unrated: { fund_code: string; reason: string }[]
  error?: string
}

/* ── 写端点参数 ─────────────────────────────────────────────────
   ⚠️ 后端写端点的返回形状**不统一**：`/api/holdings` 只给 `message`、
   `/api/reconcile` 给 `data`。所以 `apiPost` 返回整个信封，调用方各取所需。 */

/** `/api/holdings` POST —— action 决定需要哪些字段 */
export interface HoldingsAction {
  action: 'buy' | 'sell' | 'update' | 'delete' | 'dividend_policy'
  /** buy: 基金代码 */
  code?: string
  /** buy: 基金名（缺省时后端用库内名） */
  name?: string
  /** buy / sell: 日期 `YYYY-MM-DD`（缺省今天） */
  date?: string
  /** buy / sell: 金额（元） */
  amount?: number
  /** sell / update / delete / dividend_policy: 持仓 id */
  id?: number
  notes?: string
  /** buy / sell: 是否 15:00 后提交（按下一交易日确认） */
  after_cutoff?: boolean
  /** dividend_policy: `reinvest`（红利再投）| `cash`（现金分红） */
  policy?: 'reinvest' | 'cash'
}

/** `/api/reconcile` 返回 —— ⚠️ 这是**幂等自动补录**：真的会改账本 */
export interface ReconcileResult {
  settled_buys?: number
  settled_sells?: number
  dividend?: { posted_n?: number; posted_amount?: number; error?: string; [k: string]: unknown }
  [k: string]: unknown
}

/** `/api/holdings/refresh` 返回 —— 对账 + 按最新净值重算 */
export interface HoldingsRefresh {
  portfolio: Portfolio
  /** 本地净值最新日期（T+1 数据的实际截止日） */
  nav_latest_date: string | null
  today: string
  /** 净值是否落后于今天（界面的"更新净值"提示依据） */
  stale: boolean
}

/** `/api/plan` POST —— 投资计划维护 */
export interface PlanAction {
  action: 'update' | 'add_item' | 'update_item' | 'delete_item' | 'delete_plan'
  [k: string]: unknown
}

/** `/api/dca` POST —— 定投维护。
 *  ⚠️ `sync` / `backfill` / `run` 会**自己产生真实买入**（`auto_executed`）——
 *  界面上必须二次确认，不能点一下就落若干笔真账。 */
export interface DcaAction {
  action: 'sync' | 'backfill' | 'add' | 'run' | 'pause' | 'resume' | 'delete'
  [k: string]: unknown
}
