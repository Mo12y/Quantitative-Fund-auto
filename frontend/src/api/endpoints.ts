/** 端点薄封装：路径 + 返回类型**只在此处出现**，页面不再直接写 URL 字符串。 */
import { apiEnvelope, apiGet, apiPost, type EnvelopeOptions, type GetOptions } from './client'
import type {
  AllDashboard,
  BoardPool,
  DcaAction,
  DcaPlan,
  Explain,
  FundsPayload,
  HoldingsAction,
  HoldingsRefresh,
  InvestmentPlan,
  MarketLive,
  Overview,
  PlanAction,
  PortfolioCurve,
  QuantModels,
  Rebalance,
  RecommendPayload,
  ReconcileResult,
  SectorsPayload,
  SentimentData,
  SentimentState,
} from './types'

export const endpoints = {
  overview: (o?: GetOptions) => apiGet<Overview>('/api/overview', o),
  rebalance: (o?: GetOptions) => apiGet<Rebalance>('/api/rebalance', o),
  /** 数据链路下钻（采集→清洗→特征→建模→评估，每格指回真实产物） */
  explain: (o?: GetOptions) => apiGet<Explain>('/api/explain', o),

  /* ── 研究 ──────────────────────────────────────────────────── */
  /** 筛选池（全市场质量筛；冷算慢，返回 warming 时客户端自动轮询） */
  funds: (o?: GetOptions) => apiGet<FundsPayload>('/api/funds', o),
  /** 筛选池总榜：**按板块分组**（「研究」里把池子与板块合到一页就靠它） */
  fundsBoard: (size = 5, limit = 300, o?: GetOptions) =>
    apiGet<BoardPool>(`/api/funds/board?size=${size}&limit=${limit}`, o),
  /** 申万一级行业排名（动量 + 趋势 + 风险） */
  sectors: (o?: GetOptions) => apiGet<SectorsPayload>('/api/sectors', o),
  /** 量化模型（vol 预测 / 回撤预警 / 组合模拟） */
  quantModels: (o?: GetOptions) => apiGet<QuantModels>('/api/quant_models', o),

  /* ── 持仓 ──────────────────────────────────────────────────── */
  /** 定投计划（执行期数 / 是否到期未执行） */
  dca: (o?: GetOptions) => apiGet<DcaPlan[]>('/api/dca', o),

  /* ── 设置 ──────────────────────────────────────────────────── */
  /** 投资计划（单计划；只读首版用它，写操作未接） */
  plan: (o?: GetOptions) => apiGet<{ plan: InvestmentPlan | null; error?: string }>('/api/plan', o),

  /* ══ B-1：补齐双向差集里的纯读端点 ═══════════════════════════ */

  /** 组合累计收益 / 资产净值曲线（独立端点）。
   *  与 `/api/overview` 里的 `curve` 同源（后端都走 `_portfolio_curve()`）。 */
  portfolioCurve: (o?: GetOptions) => apiGet<PortfolioCurve>('/api/portfolio/curve', o),

  /**
   * 指数盘中快照 + 本地净值时滞。
   *
   * ⚠️ 出网失败返回 `ok:true` + `available:false` + `reason` —— **这是正常降级，不是错误**。
   *    界面要如实说"盘中行情不可用"，既不能报错，也不能拿旧值冒充实时。
   */
  marketLive: (o?: GetOptions) => apiGet<MarketLive>('/api/market/live', o),

  /**
   * 消息面 —— **三态**端点，**不能走 `apiGet`**：
   *  · `ok`       命中成功缓存，`data` 有值
   *  · `scanning` 后台正在**联网**扫描；`data` 可能是上一次的旧值，也可能为 `null`
   *  · `ok:false` 3 分钟内扫描失败过（后端主动拒绝重扫，避免反复出网）
   *
   * ⚠️ 别把它接进 `warming` 分支：`warming` 是"本地重算中"、`scanning` 是"联网抓取中"，
   *    且 `scanning` 允许 `data=null` —— 用 `apiGet` 会当场抛"后端返回了空数据"。
   */
  sentiment: async (o?: EnvelopeOptions): Promise<SentimentState> => {
    const env = await apiEnvelope<SentimentData>('/api/sentiment', o)
    if (!env.ok) {
      return { phase: 'error', message: env.error || '消息面扫描失败，请稍后重试' }
    }
    if (env.status === 'scanning') {
      return { phase: 'scanning', data: env.data, retryInSec: env.retry_in ?? 15 }
    }
    if (env.data == null) {
      // 既不是 scanning、又没有数据 —— 后端契约破了，如实报出来而不是显示空白卡
      return { phase: 'error', message: '消息面返回空数据（非扫描中）' }
    }
    return { phase: 'ok', data: env.data, cached: !!env.cached }
  },

  /**
   * **历史回测验证**（⚠️ 不是"推荐"，文案不得混称；加载 15~20 秒，界面必须给加载态）。
   *
   * "这不是推荐"的说明在**信封层**（`envelope.purpose` / `envelope.methodology_note`），
   * 不在 `data` 里 —— 渲染必须用它，别自己编。
   */
  recommend: (o?: GetOptions) => apiGet<RecommendPayload>('/api/recommend', o),

  /**
   * 旧前端的"一次拉全量"聚合端点。新前端按页拆分请求，**原则上不用它** ——
   * 封装是为了补齐 §10.3 的双向差集（B-1 的验收项之一）。
   */
  all: (o?: GetOptions) => apiGet<AllDashboard>('/api/all', o),

  /* ══ B-4：写端点（界面接入在 B-4b —— 通道先铺好）═════════════
     ⚠️ 这些端点**真的会改 `data/fund_quant.db`**。它们返回**整个信封**
     （后端成功时可能只给 `message`、不给 `data`），调用方自己取。 */

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

  /**
   * 定投维护：action = sync / backfill / add / run / pause / resume / delete。
   * ⚠️ `sync` / `backfill` / `run` 会**自己产生真实买入**（`auto_executed`）——
   *   界面必须二次确认（B-4b），否则点一下就落若干笔真账。
   */
  dcaAction: (body: DcaAction, o?: EnvelopeOptions) => apiPost('/api/dca', body, o),
}
