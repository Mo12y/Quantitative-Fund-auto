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

export type ApiSource = 'cache' | 'snapshot' | 'fresh' | 'warming' | 'live' | (string & {})

export interface ApiEnvelope<T> {
  ok: boolean
  data: T | null
  source?: ApiSource
  /** ⚠️ **不止 `warming`**：`/api/sentiment` 还会回 `ok` / `scanning`（联网扫描中）。
   *  别把非 `warming` 的状态当成"没有状态"。 */
  status?: 'warming' | 'scanning' | 'ok' | (string & {})
  retry_in?: number
  error?: string
  /** ⚠️ **写端点成功时常常只有 `message`、没有 `data`**（如 `/api/holdings` 返回
   *  `{ok:true, message:"已记录买入…"}`）—— 所以 `apiPost` 返回整个信封而不是只返回 `data`。 */
  message?: string
  /** `/api/sentiment` 命中成功缓存时为 true */
  cached?: boolean
  /** ⚠️ `/api/recommend` 把**免责说明放在信封层**（与 `ok`/`data` 平级，不在 `data` 里）：
   *  `purpose` / `methodology_note` 是"这不是推荐、是历史回测"的文案来源，
   *  界面必须能拿到 —— 否则只能自己编，容易把回测验证说成"推荐"。 */
  purpose?: string
  methodology_note?: string
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
  /** 原始信封。少数端点把文案放在信封层（如 `/api/recommend` 的 `purpose`），
   *  调用方需要时从这里取；不需要就忽略。 */
  envelope?: ApiEnvelope<T>
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

/** `apiEnvelope` / `apiPost` 用得上的最小选项（它们都不轮询，故无 warming 相关项） */
export interface EnvelopeOptions {
  fresh?: boolean
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
    return { data: env.data, source: env.source ?? 'fresh', envelope: env }
  }
}

/**
 * 取**原始信封**，不做任何解释 —— 供**多态端点**使用。
 *
 * 为什么需要它：`/api/sentiment` 有**三态**（`ok` / `scanning` / `ok:false`），
 * 其中 `scanning` 允许 `data` 为 `null`。走 `apiGet` 会被判成"后端返回了空数据"而抛错，
 * 于是"正在联网扫描"被误报成"出错了"。这里只负责网络层与 HTTP 层，**语义交给调用方**。
 *
 * ⚠️ `scanning` **不是** `warming`：前者是"后台在联网抓取"，后者是"服务端在本地重算"，
 *    等待时长与界面提示都不同 —— **不要复用同一套分支**（计划书 §12 B-1 已知坑）。
 *
 * 不抛 `ok:false`（调用方必须自己判断 `env.ok`）；网络 / HTTP 错误照抛。
 */
export async function apiEnvelope<T>(
  path: string,
  opts: EnvelopeOptions = {},
): Promise<ApiEnvelope<T>> {
  const url = new URL(path, window.location.origin)
  if (opts.fresh) url.searchParams.set('fresh', '1')
  const res = await fetch(url.toString(), { signal: opts.signal })
  if (!res.ok) {
    throw new ApiError(`HTTP ${res.status}`, res.status)
  }
  return (await res.json()) as ApiEnvelope<T>
}

/**
 * POST 一个**写**端点（对齐后端 `{ok, data, source}` 信封）。
 *
 * ⚠️ 返回的是**整个信封**（不是 `ApiResult`），因为后端写端点的返回形状**不统一**：
 *   · `/api/holdings`      → `{ok:true, message:"已记录买入…"}`（**没有 data**）
 *   · `/api/reconcile`     → `{ok:true, data:{settled_buys,…}, changed}`
 *   · `/api/nav/update`    → `{ok:true, message:"…", failed:[…]}`
 * 所以调用方自己取 `message`（给人看的提示）或 `data`（要看结构时）。
 *
 * ⚠️ 与 `apiGet` 两处**刻意**的不同：
 *  1. **不做 warming 轮询** —— 写操作不存在"正在算"这一态；真收到 `warming` 说明服务端
 *     把这个端点接到了预热路径上，属实现错误。直接抛出来比静默等待好：否则用户点了
 *     「记买入」却一直没反应，分不清是慢还是失败。
 *  2. 只在 `ok:false` 时抛错（`ApiError` 带后端给的中文原因）；**不检查 `data` 是否为空**。
 *
 * 写端点见 `endpoints.ts`（`holdings` / `reconcile` / `holdingsRefresh` / `navUpdate` /
 * `plan` / `dca`）。界面接入在 B-4。
 */
export async function apiPost<T = unknown>(
  path: string,
  body: unknown = {},
  opts: EnvelopeOptions = {},
): Promise<ApiEnvelope<T>> {
  const url = new URL(path, window.location.origin)
  const res = await fetch(url.toString(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
    signal: opts.signal,
  })
  if (!res.ok) {
    throw new ApiError(`HTTP ${res.status}`, res.status)
  }
  const env = (await res.json()) as ApiEnvelope<T>
  if (env.status === 'warming') {
    throw new ApiError('写端点返回了预热态 —— 写操作不应走预热路径')
  }
  if (!env.ok) {
    throw new ApiError(env.error || '后端返回 ok=false')
  }
  return env
}

/** 来源标签 → 中文（界面标注用，避免各页面自己乱写） */
export const SOURCE_LABEL: Record<string, string> = {
  cache: '内存缓存',
  snapshot: '本地快照',
  fresh: '刚刚重算',
  warming: '正在计算',
  live: '刚刚计算',
}