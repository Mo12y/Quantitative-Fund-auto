import { endpoints } from '../api/endpoints'
import { dirClass, dirOf, dirSymbol, fmtSignedPct, plainText } from '../lib/format'
import { useApi } from '../lib/useApi'
import { useSentiment } from '../lib/useSentiment'
import { Card } from './Card'

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
 * 市场小条 —— 指数盘中行情 + 消息面，**压在一行**（2026-10-06 瘦身第 5 条）。
 *
 * 改造前：两者各占一张卡（`LiveQuoteCard` / `SentimentCard`），在「今天」页加起来是
 * 两块独立卡片（首屏外还要再占半屏）。它们回答的其实是同一个问题 ——
 * "市场现在什么状态" —— 故合成一块、一行讲完；逐条告警明细收进折叠区（§5.3 渐进披露）。
 *
 * ⚠️ 两条**不能改**的语义（后端 `api_market_live` / `api_sentiment` docstring，原文保留）：
 *  1. 行情**出网失败不是错误**：`ok:true` + `available:false` + `reason` 是正常降级。
 *     界面必须如实说"盘中行情不可用"，既不能报错、也不能拿旧值冒充实时。
 *  2. `lag_note` 是**时滞的量化**（"本地净值落后 N 天"）—— 必须显示。
 *     只说"可能偏旧"等于没说。
 *  3. 消息面 `scanning` 时 `data` 可能是**上一次的旧值**，必须标注"刷新中"。
 *
 * 涨跌走 `dirOf`/`dirClass`/`dirSymbol`（涨红跌绿 + ▲▼ 符号，色盲可读）。
 */
export function MarketStrip({ className = '' }: { className?: string }) {
  const live = useApi(endpoints.marketLive)
  const senti = useSentiment()
  const d = live.data
  const sd = senti.data
  const alerts = sd && !sd.all_clear ? sd.alerts.slice(0, 5) : []

  return (
    <Card className={className} title="市场" note="· 指数行情 / 消息面">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-body-sm">
        {/* ── 指数行情 ─────────────────────────────────────────────── */}
        {live.error ? (
          <span className="text-fg-2">行情读取失败：{live.error}</span>
        ) : !d ? (
          <span className="skeleton inline-block h-4 w-40 rounded-full" aria-hidden="true" />
        ) : !d.available ? (
          <span className="text-fg-3">指数盘中行情不可用：{plainText(d.reason) || '未知原因'}</span>
        ) : (
          <>
            <span className="text-fg-4">
              {d.trading ? '盘中' : '收盘'}
              {d.asof ? ` ${d.asof}` : ''}
            </span>
            {d.quotes.map((q) => {
              const dir = dirOf(q.pct)
              return (
                <span key={q.code} className="inline-flex max-w-full items-baseline gap-1.5">
                  <span className="truncate text-fg-3">{plainText(q.name)}</span>
                  <b className={'num shrink-0 ' + dirClass(dir)}>
                    {dirSymbol(dir)}
                    {q.pct == null ? '—' : fmtSignedPct(q.pct)}
                  </b>
                  <span className="num shrink-0 text-fg-2">{q.price.toFixed(2)}</span>
                </span>
              )
            })}
          </>
        )}

        <span className="text-fg-4" aria-hidden="true">
          ·
        </span>

        {/* ── 消息面（一行摘要；明细进下方折叠区） ───────────────── */}
        {senti.errMsg ? (
          <span className="inline-flex flex-wrap items-center gap-x-2">
            <span className="text-fg-2">消息面加载失败：{plainText(senti.errMsg)}</span>
            <button
              type="button"
              onClick={senti.reload}
              className="rounded-md border border-line-strong px-2 py-0.5 text-caption text-fg-2 hover:text-fg"
            >
              重试
            </button>
          </span>
        ) : !sd ? (
          <span className="text-fg-3">消息面扫描中…</span>
        ) : sd.all_clear ? (
          <span className="inline-flex items-center gap-1.5 text-fg-2">
            <i className="h-[7px] w-[7px] shrink-0 rounded-full bg-ok" aria-hidden="true" />
            消息面：本周无需要关注的信号
          </span>
        ) : (
          <span className="inline-flex min-w-0 items-center gap-1.5 text-fg-2">
            <i
              className={'h-[7px] w-[7px] shrink-0 rounded-full ' + levelDot(alerts[0]?.level ?? '')}
              aria-hidden="true"
            />
            <span className="truncate">
              消息面：{plainText(sd.signal_summary) || `${alerts.length} 条信号`}
            </span>
          </span>
        )}

        {/* 旧值标注（scanning）与时滞量化（lag_note）—— 两条都是**必须显示**的披露。
            ⚠️ "以下是上次结果"只在**真的拿着旧值**（sd 非空）时才说 —— 没有旧值时
            它和"扫描中…"同时出现会自相矛盾（实测踩到）。 */}
        {senti.scanning && sd && <span className="text-caption text-fg-4">（刷新中…以下为上次结果）</span>}
        {d?.lag_note && <span className="text-caption text-fg-4">{plainText(d.lag_note)}</span>}
      </div>

      {/* 消息面明细 —— 默认折叠（DESIGN §5.3 渐进披露）：摘要已经在上面一行讲完，
          逐条告警是"我要看细节"时才需要的。折叠 ≠ 隐藏：标题行写清了条数。 */}
      {alerts.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer list-none text-caption text-fg-2">
            <span className="mr-1 text-fg-3">⌄</span>
            消息面明细 · {alerts.length} 条（默认折叠）
          </summary>
          <div className="mt-2 flex flex-col gap-1.5">
            {alerts.map((a, i) => (
              <div key={i} className="rounded-[var(--radius-md)] bg-inset px-3 py-2 text-body-sm">
                <span className="inline-flex items-center gap-2 text-fg">
                  <i
                    className={'h-[6px] w-[6px] shrink-0 rounded-full ' + levelDot(a.level)}
                    aria-hidden="true"
                  />
                  <span className="min-w-0">
                    {a.category ? <span className="text-fg-3">[{plainText(a.category)}] </span> : null}
                    {plainText(a.title)}
                  </span>
                </span>
                {a.detail ? (
                  <div className="mt-1 pl-3.5 text-caption leading-relaxed text-fg-4">
                    {plainText(a.detail)}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
          <div className="mt-2 text-caption text-fg-4">只显示能影响决策的信号</div>
        </details>
      )}
    </Card>
  )
}
