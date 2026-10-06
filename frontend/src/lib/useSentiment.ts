import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import { endpoints } from '../api/endpoints'
import type { SentimentData } from '../api/types'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** 最多轮询几次后放弃（后端建议的 `retry_in` 通常是 15~25s，9 次够覆盖首扫）。 */
const MAX_POLLS = 9

export interface SentimentView {
  /** 后台**正在联网**抓取（`data` 可能是上一次的旧值，也可能为 null）。 */
  scanning: boolean
  /** 成功缓存或旧值；`error` 态为 null。 */
  data: SentimentData | null
  /** 需要展示的失败原因（网络失败 或 后端主动拒绝重扫）。 */
  errMsg: string | null
  reload: () => void
}

/**
 * 消息面（`/api/sentiment`）的**三态取数**——从原 `SentimentCard` 抽出。
 *
 * ⚠️ 这个端点有**三态**，与 `warming` 是两件事，**不能复用 Warming 分支**：
 *  · `ok`       → 命中成功缓存
 *  · `scanning` → 后台**正在联网抓取**；`data` 可能是**上一次的旧值**（也可能为 null）
 *  · `error`    → 3 分钟内扫描失败过（后端主动拒绝重扫，避免反复出网）
 * 等待时长与文案都与 `warming`（本地重算）不同，所以这里自己管轮询。
 *
 * ⚠️ 旧值必须由调用方**标注出来**（"正在刷新…以下是上一次的结果"），
 * 否则用户会把旧结果当成刚扫出来的 —— 故这里把 `scanning` 一并返回。
 *
 * 抽成 hook 的原因（2026-10-06 瘦身第 5 条）：行情与消息面要并成**一行小条**
 * （`MarketStrip`），不能再各占一张卡片，但这段轮询逻辑是易错的、不该复制第二份。
 */
export function useSentiment(): SentimentView {
  const [state, setState] = useState<
    { phase: 'ok'; data: SentimentData } | { phase: 'scanning'; data: SentimentData | null } | null
  >(null)
  const [failed, setFailed] = useState<string | null>(null)
  const seq = useRef(0)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const load = useCallback(async () => {
    const my = ++seq.current
    setFailed(null)
    for (let tries = 0; ; tries++) {
      try {
        const s = await endpoints.sentiment()
        if (!mounted.current || my !== seq.current) return
        if (s.phase === 'error') {
          setFailed(s.message)
          return
        }
        setState(s.phase === 'ok' ? { phase: 'ok', data: s.data } : { phase: 'scanning', data: s.data })
        if (s.phase === 'scanning') {
          if (tries >= MAX_POLLS) {
            setFailed('扫描耗时较长，可稍后重试')
            return
          }
          await sleep(Math.max(5, Math.min(30, s.retryInSec)) * 1000)
          continue
        }
        return
      } catch (e) {
        if (!mounted.current || my !== seq.current) return
        setFailed(e instanceof ApiError ? e.message : String(e))
        return
      }
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  return {
    scanning: state?.phase === 'scanning',
    data: state?.data ?? null,
    errMsg: failed,
    reload: () => void load(),
  }
}
