import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import { endpoints } from '../api/endpoints'
import type { SentimentState } from '../api/types'
import { Card } from './Card'
import { Skeleton } from './Skeleton'
import { plainText } from '../lib/format'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** 最多轮询几次後放弃（后端建议的 `retry_in` 通常是 15~25s，9 次够覆盖首扫）。 */
const MAX_POLLS = 9

/**
 * 告警等级 → 圆点颜色。
 *
 * ⚠️ 后端 `level` 给的是 **emoji**（`🔴` / `🟡`）。这里**不显示 emoji**，只用来选颜色：
 *  · emoji 会被 `plainText()` 剥掉，依赖它等于没信息；
 *  · 红绿/黄绿组合对色觉障碍不可靠，故圆点只是**辅助**，等级同时由文案（标题）承载。
 */
function levelDot(level: string): string {
  if (level.includes('🔴')) return 'bg-danger'
  if (level.includes('🟡')) return 'bg-warn'
  return 'bg-accent'
}

/**
 * 消息面（`/api/sentiment`）—— 旧前端 `loadSentiment()` + `signalsInner()` 的等价物。
 *
 * ⚠️ 这个端点有**三态**，与 `warming` 是两件事，**不能复用 Warming 分支**：
 *  · `ok`       → 命中成功缓存
 *  · `scanning` → 后台**正在联网抓取**；`data` 可能是**上一次的旧值**（也可能为 null）
 *  · `error`    → 3 分钟内扫描失败过（后端主动拒绝重扫，避免反复出网）
 * 等待时长与文案都与 `warming`（本地重算）不同，所以这里自己管轮询。
 *
 * ⚠️ 旧值要**标注出来**：`scanning` 且有 `data` 时显示"正在刷新…以下是上一次的结果"，
 * 否则用户会把旧结果当成刚扫出来的。
 */
export function SentimentCard({ className = '' }: { className?: string }) {
  const [state, setState] = useState<SentimentState | null>(null)
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
        setState(s)
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

  const scanning = state?.phase === 'scanning'
  const data = state && state.phase !== 'error' ? state.data : null
  const errMsg = failed ?? (state?.phase === 'error' ? state.message : null)

  return (
    <Card className={className} title="消息面" note="· 宏观与持仓信号">
      {errMsg ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span className="text-[12.5px] text-fg-2">信号加载失败：{plainText(errMsg)}</span>
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-md border border-line-strong px-2.5 py-1 text-[11.5px] text-fg-2 hover:text-fg"
          >
            重试
          </button>
        </div>
      ) : !state ? (
        <Skeleton lines={3} />
      ) : data ? (
        <>
          {scanning && (
            <div className="mb-2 text-[11px] text-fg-4">正在刷新…以下是上一次的结果</div>
          )}
          {data.all_clear ? (
            <div className="flex items-center gap-2 text-[12.5px] text-fg-2">
              <i className="h-[7px] w-[7px] shrink-0 rounded-full bg-ok" aria-hidden="true" />
              本周无需要关注的信号
            </div>
          ) : (
            <>
              {data.signal_summary ? (
                <div className="text-[12.5px] leading-relaxed text-fg-2">
                  {plainText(data.signal_summary)}
                </div>
              ) : null}
              <div className="mt-2 flex flex-col gap-2">
                {data.alerts.slice(0, 5).map((a, i) => (
                  <div key={i} className="rounded-[var(--radius-md)] bg-inset px-3 py-2.5">
                    <div className="flex items-center gap-2 text-[12.5px] text-fg">
                      <i
                        className={'h-[6px] w-[6px] shrink-0 rounded-full ' + levelDot(a.level)}
                        aria-hidden="true"
                      />
                      <span className="min-w-0">
                        {a.category ? (
                          <span className="text-fg-3">[{plainText(a.category)}] </span>
                        ) : null}
                        {plainText(a.title)}
                      </span>
                    </div>
                    {a.detail ? (
                      <div className="mt-1 pl-3.5 text-[11.5px] leading-relaxed text-fg-4">
                        {plainText(a.detail)}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
              <div className="mt-2 text-[11px] text-fg-4">只显示能影响决策的信号</div>
            </>
          )}
        </>
      ) : (
        <div className="flex items-center gap-2.5 text-[12.5px] text-fg-2">
          <span className="skeleton h-3 w-3 rounded-full" aria-hidden="true" />
          后台联网扫描宏观/持仓信号中…（首次较慢，可稍后刷新）
        </div>
      )}
    </Card>
  )
}
