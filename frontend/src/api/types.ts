/**
 * 后端载荷类型（字段名**逐一对齐**真实响应，见 2026-09-30 dump 的结构快照）。
 * 只声明前端**真的会用**的字段；其余用可选字段或索引签名，避免过度约束后端演进。
 */

/* ── 通用 ─────────────────────────────────────────────────────── */

export interface PeerPercentile {
  group?: string
  group_n?: number
  percentiles?: Record<string, number | null>
  percentiles_scale?: string
  note?: string
}

/** 调仓指令（RebalanceAdvisor 的序列化结果） */
export interface Instruction {
  action: '卖出' | '买入' | '持有' | string
  amount: number
  fund_code: string
  fund_name: string
  reason: string
  priority: number
  current_pct: number
  target_pct: number
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

export interface Rebalance {
  need_rebalance: boolean
  current_equity_pct: number
  target_equity_pct: number | null
  /** 目标是谁定的：'temperature' = 温度模型 / 'user_profile' = 用户在本地画像里覆盖。
   *  界面必须说出来 —— 否则用户看到一个与温度不符的目标会以为模型算错。 */
  target_source?: 'temperature' | 'user_profile' | null
  gap_pct: number | null
  /** 与 gap_pct **同基数**的差额金额（元）；正 = 权益不足需补，负 = 权益过多需减。
   *  ⚠️ 不要用别的口径自己乘出这个数 —— 旧前端就是拿 portfolio.total_invested 乘的，
   *  基数与百分比差 33%（¥314.72 vs 应为 ¥418.63）。 */
  gap_amount?: number | null
  /** 调仓容忍带（±百分点）。用它把差额说成"在容忍范围内"，而不是一个待办。 */
  rebalance_pp?: number
  /** 百分比的基数 = 持仓市值 + 现金弹药。金额必须按它算才与百分比自洽。 */
  total_capital?: number
  portfolio_value?: number
  summary: { verdict: string; detail: string }
  instructions: Instruction[]
  degraded?: string[]
  cash_reserve?: number
  /** 温度不覆盖的部分（QDII-海外/黄金等）——界面必须如实标注，否则用户以为漏算 */
  non_applicable_pct?: number
  scope_note?: string
  constraint_review?: ConstraintReview | null
  constraint_blocked_buy?: boolean
  error?: string
}

/* ── 温度 ─────────────────────────────────────────────────────── */

export interface Temperature {
  temperature: number
  level: string
  level_desc: string
  target_equity_pct: number | null
  action: string
  insufficient_data: boolean
  degraded_dimensions: string[]
  components: Record<string, number | null>
  market_style?: { dominant: string; detail: string }
  divergence?: { level: string; max_diff: number; message: string }
  scope?: { applies_to: string[]; not_applicable_to: string[]; note: string }
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

/* ── 总览（/api/overview） ────────────────────────────────────── */

export interface Overview {
  portfolio: Portfolio
  stats: Stats
  temp: Temperature
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

/* ── 筛选池（/api/funds、/api/funds/board） ───────────────────── */

/** B 层约束在池子里的标注结果（§4.4）：kept 通过 / dropped 落选 / skipped 未评估 */
export type ConstraintStatus = 'kept' | 'dropped' | 'skipped' | string

export interface PoolFund {
  code: string
  name: string
  type: string
  /** 风险标签，形如「🟢 稳健」（emoji 由 `plainText` 统一剥） */
  risk: string
  /** 运作费率（%）。⚠️ 缺失为 `null` —— **不是 0**，界面不得当 0 显示 */
  fee: number | null
  score: number | null
  /** 申购状态文案（如「开放申购」/「限大额」） */
  purchase_status: string
  purchasable: string
  momentum_3m: number | null
  max_dd_1y: number | null
  sharpe: number | null
  ann_vol: number | null
  /** 同类分组名与组内样本数（分位必须带组，否则会被读成全市场排名） */
  group: string
  group_n: number
  percentiles: Record<string, number | null> | null
  percentiles_scale?: string | null
  nav_asof: string | null
  insufficient_data: boolean
  reason?: string | null
  metrics?: Record<string, number | null>
  /** ⚠️ 池子是**只标注不剔除**（浏览面保持完整）；这里只记状态，不代表被过滤掉 */
  constraint_status?: ConstraintStatus
  constraint_reasons?: string[]
  reasons?: string[]
}

export interface PoolSummary {
  total: number
  avg_fee: number
  fee_n: number
  limited_n: number
  status_unknown_n: number
  by_risk: Record<string, number>
}

export interface FundsPayload {
  funds: PoolFund[]
  summary: PoolSummary
  constraint_review?: ConstraintReview | null
}

/** 筛选池总榜的一块（按板块分组，每块取前 size 只） */
export interface BoardGroup {
  board: string
  total: number
  funds: PoolFund[]
}

export interface BoardPool {
  boards: BoardGroup[]
  total_funds: number
  size: number
  constraint_review?: ConstraintReview | null
  error?: string
}

/* ── 行业板块（/api/sectors） ─────────────────────────────────── */

export interface Sector {
  name: string
  rank: number
  ret_1m: number
  ret_3m: number
  ret_6m: number
  score: number
  ma_ratio: number
  volatility: number
  max_dd_6m: number
}

export interface SectorLite {
  name: string
  ret_1m: number
  ret_3m: number
}

export interface SectorsPayload {
  cached_at?: string
  sectors: Sector[]
  momentum_leaders: SectorLite[]
  value_candidates: SectorLite[]
  error?: string
}

/* ── 量化模型（/api/quant_models） ────────────────────────────── */

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

export interface QuantModels {
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