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
  gap_pct: number | null
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