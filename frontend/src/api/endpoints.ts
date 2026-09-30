/** 端点薄封装：路径 + 返回类型**只在此处出现**，页面不再直接写 URL 字符串。 */
import { apiGet, type GetOptions } from './client'
import type {
  BoardPool,
  DcaPlan,
  Explain,
  FundsPayload,
  InvestmentPlan,
  Overview,
  QuantModels,
  Rebalance,
  SectorsPayload,
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
}
