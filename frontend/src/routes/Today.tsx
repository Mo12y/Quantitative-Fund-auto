import { endpoints } from '../api/endpoints'
import { Card } from '../components/Card'
import { CurveChart } from '../components/CurveChart'
import { HoldingsTable } from '../components/HoldingsTable'
import { KpiRow } from '../components/KpiRow'
import { SourceTag } from '../components/SourceTag'
import { VerdictCard } from '../components/VerdictCard'
import { plainText } from '../lib/format'
import { useApi } from '../lib/useApi'

/** 温度分解维度的中文名（后端键名 → 界面标签） */
const DIM_LABEL: Record<string, string> = {
  pe_score: 'PE 分位',
  pb_score: 'PB 分位',
  erp_score: '性价比',
  sentiment_score: '情绪',
  volume_score: '量能',
}

/** 温度档位取色（冷→强调色 / 适中→正文 / 热→警示）。与旧前端一致的 20/40/60/80 分档 */
function tempTone(t: number): string {
  if (t <= 40) return 'text-accent'
  if (t <= 60) return 'text-fg'
  if (t <= 80) return 'text-warn'
  return 'text-rise'
}
function tempBar(t: number): string {
  if (t <= 60) return 'bg-accent'
  if (t <= 80) return 'bg-warn'
  return 'bg-rise'
}

export default function Today() {
  const overview = useApi(endpoints.overview)
  const rebalance = useApi(endpoints.rebalance)
  const explain = useApi(endpoints.explain)

  const ov = overview.data
  // 在持**基金**数：holdings 是批次行（29 行 = 7 只基金），去重后才是用户理解的"只"
  const fundCount = ov
    ? new Set(ov.portfolio.holdings.filter((h) => h.status === 'holding').map((h) => h.code)).size
    : 0
  const busy = overview.loading || rebalance.loading || explain.loading
  const warmingWait = Math.max(overview.warmingWait, rebalance.warmingWait, explain.warmingWait)

  const refreshAll = () => {
    overview.refresh({ fresh: true })
    rebalance.refresh({ fresh: true })
    explain.refresh({ fresh: true })
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-[17px] font-medium tracking-[.2px]">
          今天要做什么
          <span className="ml-1.5 text-[13px] font-normal text-fg-3">量化基金 · 个人账本</span>
        </h1>
        <SourceTag
          source={overview.source}
          asof={ov ? ov.curve.dates[ov.curve.dates.length - 1] : null}
          onRefresh={refreshAll}
          busy={busy}
        />
      </div>

      {warmingWait > 0 && busy && (
        <div className="mb-3.5 rounded-md border border-line bg-inset px-3 py-2 text-xs text-fg-2">
          正在计算…还需约 {warmingWait} 秒（首次计算较慢，之后会命中缓存）
        </div>
      )}

      {overview.error && (
        <Card lead title="今天" note="· 结论">
          <div className="text-sm text-fg-2">读取失败：{overview.error}</div>
          <button
            type="button"
            onClick={() => overview.refresh()}
            className="mt-3 rounded-md border border-line-strong px-3 py-1 text-xs text-fg-2 hover:text-fg"
          >
            重试
          </button>
        </Card>
      )}

      {!ov && !overview.error && (
        <Card lead title="今天" note="· 结论">
          <div className="text-sm text-fg-3">加载中…</div>
        </Card>
      )}

      {ov && (
        <>
          <VerdictCard rebalance={rebalance.data} explain={explain.data} loading={rebalance.loading} />

          <KpiRow stats={ov.stats} rebalance={rebalance.data} holdingFunds={fundCount} />

          <CurveChart curve={ov.curve} />

          <div className="grid items-start gap-3.5 md:grid-cols-[1.35fr_1fr]">
            <HoldingsTable holdings={ov.portfolio.holdings} />

            <Card title="温度" note="· A 股权益估值分位">
              <div className="flex items-baseline gap-2.5">
                <span className={'text-3xl font-medium ' + tempTone(ov.temp.temperature)}>
                  {Math.round(ov.temp.temperature)}°
                </span>
                <span className="text-[13.5px] text-fg-2">
                  {plainText(ov.temp.level_desc)} · {plainText(ov.temp.action)}
                </span>
              </div>
              <div className="my-2.5 h-[5px] overflow-hidden rounded-[3px] bg-inset">
                <i
                  className={'block h-full ' + tempBar(ov.temp.temperature)}
                  style={{ width: `${Math.max(0, Math.min(100, ov.temp.temperature))}%` }}
                />
              </div>
              <div className="text-[11.5px] leading-relaxed text-fg-4">
                {Object.entries(ov.temp.components ?? {})
                  .map(([k, v]) => `${DIM_LABEL[k] ?? k} ${v == null ? '缺失' : Math.round(v) + '°'}`)
                  .join(' · ')}
              </div>
              {ov.temp.scope?.note && (
                <div className="mt-2 border-t border-line pt-2 text-[11.5px] leading-relaxed text-fg-4">
                  {plainText(ov.temp.scope.note)}
                </div>
              )}
            </Card>
          </div>
        </>
      )}
    </>
  )
}