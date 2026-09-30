/** 端点薄封装：路径 + 返回类型**只在此处出现**，页面不再直接写 URL 字符串。 */
import { apiGet, type GetOptions } from './client'
import type { Explain, Overview, Rebalance } from './types'

export const endpoints = {
  overview: (o?: GetOptions) => apiGet<Overview>('/api/overview', o),
  rebalance: (o?: GetOptions) => apiGet<Rebalance>('/api/rebalance', o),
  /** 数据链路下钻（采集→清洗→特征→建模→评估，每格指回真实产物） */
  explain: (o?: GetOptions) => apiGet<Explain>('/api/explain', o),
}