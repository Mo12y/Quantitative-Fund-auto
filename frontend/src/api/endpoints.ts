/** 端点薄封装：路径 + 返回类型**只在此处出现**，页面不再直接写 URL 字符串。 */
import { apiGet, apiPost, type EnvelopeOptions, type GetOptions } from './client'
import type {
  Behavior,
  Breakeven,
  CalendarPayload,
  Counterfactual,
  DcaAction,
  DcaPlan,
  ExclusionPayload,
  Explain,
  HoldingsAction,
  HoldingsRefresh,
  InvestmentPlan,
  MarketLive,
  Overview,
  PeerPayload,
  PlanAction,
  PortfolioCurve,
  ReconcileResult,
  Research,
  RiskPayload,
} from './types'

export const endpoints = {
  /* ── 通用 ──────────────────────────────────────────────────── */
  overview: (o?: GetOptions) => apiGet<Overview>('/api/overview', o),
  /** 数据链路下钻（采集→清洗→特征→建模→评估，每格指回真实产物） */
  explain: (o?: GetOptions) => apiGet<Explain>('/api/explain', o),

  /* ══ 决策台（T2.1 新端点；契约 = 执行计划书 §3，2026-10 起） ══ */

  /** 行为镜：追高 / 割肉 / 卖飞 / 频率。
   *  ⚠️ 响应自带**样本充分性声明**（`sample.sufficient/warning`）—— 不足时必须原样展示。 */
  behavior: (o?: GetOptions) => apiGet<Behavior>('/api/behavior', o),
  /** 反事实归因：实际 vs 完全不动 / 只定投 / 照温度动 + 同标的换时机 + 卖出规则对比 */
  counterfactual: (o?: GetOptions) => apiGet<Counterfactual>('/api/counterfactual', o),
  /** 回本门槛 / 费率漏损：每笔持仓"要涨多少才不亏"（含 <7 天惩罚期） */
  breakeven: (o?: GetOptions) => apiGet<Breakeven>('/api/breakeven', o),
  /** 持仓风险刻画：波动 / 回撤 / 夏普 / 索提诺…（点数不足的基金显式声明） */
  risk: (o?: GetOptions) => apiGet<RiskPayload>('/api/risk', o),
  /** 同类分位：sharpe / 近1年回撤 / TER 三分位。
   *  ⚠️ 是**原始分位**（越大 = 该值越大；TER 越大 = 越贵），不是"好=高分"。 */
  peer: (o?: GetOptions) => apiGet<PeerPayload>('/api/peer', o),
  /** 排雷：两个可辩护判据 —— 同类夏普垫底（≤P10）/ TER 偏高（≥P90）。
   *  ⚠️ 判据不可用的进 `unrated`（不判、不保清白）—— 界面必须原样呈现三类。 */
  exclusion: (o?: GetOptions) => apiGet<ExclusionPayload>('/api/exclusion', o),
  /** 研究快照：离线模型结果（vol 预测 / 回撤预警 / 组合模拟），**非实时**。
   *  2026-10-16 T2.1 更名（由旧研究端点改名而来，见 git 历史）。 */
  research: (o?: GetOptions) => apiGet<Research>('/api/research', o),

  /* ── 账本 ──────────────────────────────────────────────────── */
  /** 定投计划（执行期数 / 是否到期未执行） */
  dca: (o?: GetOptions) => apiGet<DcaPlan[]>('/api/dca', o),
  /** 投资计划（单计划；含逐只进度） */
  plan: (o?: GetOptions) => apiGet<{ plan: InvestmentPlan | null; error?: string }>('/api/plan', o),
  /** 组合累计收益 / 资产净值曲线（独立端点）。
   *  与 `/api/overview` 里的 `curve` 同源（后端都走 `_portfolio_curve()`）。 */
  portfolioCurve: (o?: GetOptions) => apiGet<PortfolioCurve>('/api/portfolio/curve', o),

  /* ── 数据状态 ──────────────────────────────────────────────── */
  /**
   * 指数盘中快照 + 本地净值时滞。
   *
   * ⚠️ 出网失败返回 `ok:true` + `available:false` + `reason` —— **这是正常降级，不是错误**。
   *    界面要如实说"盘中行情不可用"，既不能报错，也不能拿旧值冒充实时。
   */
  marketLive: (o?: GetOptions) => apiGet<MarketLive>('/api/market/live', o),
  /** 交易日历（数据状态页的「日历覆盖」与刷新命令） */
  calendar: (o?: GetOptions) => apiGet<CalendarPayload>('/api/calendar', o),

  /* ══ 写端点（**真的会改 data/fund_quant.db**；返回整个信封，调用方自取） ══ */

  /** 持仓写操作：buy / sell / update / delete / dividend_policy。
   *  后端成功时返回 `{ok:true, message:"已记录买入…"}`（**没有 data**）。 */
  holdings: (body: HoldingsAction, o?: EnvelopeOptions) =>
    apiPost('/api/holdings', body, o),

  /**
   * 幂等对账：结算到期的待确认买入/卖出 + 分红自动落账。
   * ⚠️ **会真的改账本**（含 `auto_post` 的分红）。重复调用安全（幂等键），
   *   但界面上必须让用户知道"这可能落若干笔账"。
   */
  reconcile: (o?: EnvelopeOptions) => apiPost<ReconcileResult>('/api/reconcile', {}, o),

  /** 逐日重放刷新：对账 + 按生效日净值→最新净值重算。返回 portfolio + 净值时效。 */
  holdingsRefresh: (o?: EnvelopeOptions) =>
    apiPost<HoldingsRefresh>('/api/holdings/refresh', {}, o),

  /** 更新净值：只拉取当前持仓涉及的基金。`codes` 缺省 = 当前持仓基金。 */
  navUpdate: (body: { codes?: string[] } = {}, o?: EnvelopeOptions) =>
    apiPost('/api/nav/update', body, o),

  /** 投资计划维护：action = update / add_item / update_item / delete_item / delete_plan */
  planAction: (body: PlanAction, o?: EnvelopeOptions) => apiPost('/api/plan', body, o),

  /** 撤销一次写操作（凭 `commit_id`）。只允许撤销**最近 N 条**、且不能重复撤销。 */
  rollback: (commitId: number, o?: EnvelopeOptions) =>
    apiPost('/api/holdings/rollback', { commit_id: commitId }, o),

  /**
   * 定投维护：action = sync / backfill / add / run / pause / resume / delete。
   * ⚠️ `sync` / `backfill` / `run` 会**自己产生真实买入**（`auto_executed`）——
   *   界面必须二次确认，否则点一下就落若干笔真账。
   */
  dcaAction: (body: DcaAction, o?: EnvelopeOptions) => apiPost('/api/dca', body, o),
}
