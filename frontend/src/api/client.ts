/**
 * API 客户端 —— 对接 Flask 后端（src/web/app.py）。
 *
 * 后端响应有三条**必须处理**的约定（旧前端 app.js 里是散落处理的，这里收拢成一处）：
 *  1. 统一包装 `{ ok, data, source }`；`ok=false` 时 `error` 字段是给人看的中文原因。
 *  2. **预热约定**：冷启动时慢端点不阻塞请求，而是返回
 *     `{ ok:true, data:null, status:"warming", retry_in:<秒> }` ——
 *     调用方要按 `retry_in` 轮询，而不是把 `data:null` 当成"没有数据"（那会把
 *     "还在算"误报成"算出来是空的"）。
 *  3. `source` 说明数据来自 内存缓存 / SQLite 快照 / 现算 —— 界面上要能如实标注
 *     （用户拿它判断"我看到的数字是不是刚算的"）。
 */

export type ApiSource = 'cache' | 'snapshot' | 'fresh' | 'warming' | (string & {})

export interface ApiEnvelope<T> {
  ok: boolean
  data: T | null
  source?: ApiSource
  status?: 'warming'
  retry_in?: number
  error?: string
}

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message)
    this.name = 'ApiError'
  }
}

export interface ApiResult<T> {
  data: T
  /** 数据来源（用于界面标注"缓存/快照/刚算"） */
  source: ApiSource
}

export interface GetOptions {
  /** 忽略服务端缓存，强制重算（对应 `?fresh=1`，即"重算数据"按钮） */
  fresh?: boolean
  /** 每次进入"预热中"时回调（用于显示"正在计算…还需 N 秒"） */
  onWarming?: (retryInSec: number) => void
  /** 预热最多轮询多少次（默认 40 —— 足够覆盖最慢的 110s 首算） */
  maxWarming?: number
  signal?: AbortSignal
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/**
 * GET 一个后端端点。自动处理「预热 → 轮询」，并把 `ok:false` 抛成 `ApiError`。
 */
export async function apiGet<T>(path: string, opts: GetOptions = {}): Promise<ApiResult<T>> {
  const url = new URL(path, window.location.origin)
  if (opts.fresh) url.searchParams.set('fresh', '1')

  const maxWarming = opts.maxWarming ?? 40
  for (let attempt = 0; ; attempt++) {
    const res = await fetch(url.toString(), { signal: opts.signal })
    if (!res.ok) {
      throw new ApiError(`HTTP ${res.status}`, res.status)
    }
    const env = (await res.json()) as ApiEnvelope<T>

    if (!env.ok) {
      throw new ApiError(env.error || '后端返回 ok=false')
    }
    if (env.status === 'warming') {
      // 「还在算」≠「没数据」——必须等，不能返回 null
      if (attempt >= maxWarming) {
        throw new ApiError('预热等待超时（服务端计算仍未完成）')
      }
      const wait = Math.max(1, Math.min(30, env.retry_in ?? 3))
      opts.onWarming?.(wait)
      await sleep(wait * 1000)
      continue
    }
    if (env.data == null) {
      throw new ApiError('后端返回了空数据（非预热）')
    }
    return { data: env.data, source: env.source ?? 'fresh' }
  }
}

/** 来源标签 → 中文（界面标注用，避免各页面自己乱写） */
export const SOURCE_LABEL: Record<string, string> = {
  cache: '内存缓存',
  snapshot: '本地快照',
  fresh: '刚刚重算',
  warming: '正在计算',
}