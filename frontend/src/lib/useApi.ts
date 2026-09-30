/**
 * 统一的数据加载 hook —— 把「加载中 / 预热中 / 失败 / 来源」四态收拢到一处。
 *
 * 为什么要统一：后端有**预热约定**（`status:"warming"`），如果每个组件各自 fetch，
 * 就会出现"N 个组件各自轮询、各自显示不同的加载文案"，且极易把
 * `data:null`（还在算）误报成"没有数据"。这里保证：
 *  · 预热期间 `warming=true` 且**不把 data 置空**（保留上一次成功的数据，避免闪烁）；
 *  · 失败时带**后端给的中文原因**（不是"网络错误"这种无信息量的话）。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, type ApiResult, type GetOptions } from '../api/client'

export interface ApiState<T> {
  data: T | null
  source: string | null
  loading: boolean
  /** 正在等后端预热（界面应显示"正在计算…还需 N 秒"） */
  warming: boolean
  warmingWait: number
  error: string | null
  refresh: (opts?: GetOptions) => void
}

export function useApi<T>(
  loader: (opts?: GetOptions) => Promise<ApiResult<T>>,
  initial?: GetOptions,
): ApiState<T> {
  const [data, setData] = useState<T | null>(null)
  const [source, setSource] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [warming, setWarming] = useState(false)
  const [warmingWait, setWarmingWait] = useState(0)
  const [error, setError] = useState<string | null>(null)

  // 防竞态：只有最后一次请求的结果能落地（"重算"按钮连点时不至于旧结果盖新结果）
  const seq = useRef(0)
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const run = useCallback(
    async (opts?: GetOptions) => {
      const my = ++seq.current
      setLoading(true)
      setError(null)
      try {
        const res = await loader({
          ...opts,
          onWarming: (wait) => {
            if (mounted.current && my === seq.current) {
              setWarming(true)
              setWarmingWait(wait)
            }
          },
        })
        if (!mounted.current || my !== seq.current) return
        setData(res.data)
        setSource(res.source)
      } catch (e) {
        if (!mounted.current || my !== seq.current) return
        setError(e instanceof ApiError ? e.message : String(e))
      } finally {
        if (mounted.current && my === seq.current) {
          setLoading(false)
          setWarming(false)
        }
      }
    },
    [loader],
  )

  useEffect(() => {
    void run(initial)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run])

  const refresh = useCallback((opts?: GetOptions) => void run(opts), [run])
  return { data, source, loading, warming, warmingWait, error, refresh }
}