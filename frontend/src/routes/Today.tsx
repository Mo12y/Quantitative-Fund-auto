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

/**
 * 温度显示精度：保留 1 位小数。
 *
 * ⚠️ 2026-09-30 口径对账后定案（原为 `Math.round`，只显示整数）：
 *   1. **与阈值判定保持一致**。取色分档是 40/60/80，后端 `level_desc` 也按未取整值算。
 *      只取整显示会出现「显示 60°，却套着 >60 的警示色 / 写着『偏热』」的矛盾
 *      —— 用户看到的数与系统的判断依据不是同一个数。
 *   2. **与旧前端一致**。旧前端显示 46.2°，新前端显示 46°，并行对照期（计划书 §7 阶段 4
 *      要求对照跑至少两周）两套界面数字不同会削弱信任，也会掩盖真实差异。
 *   1 位小数是后端 `thermometer._r()` 的实际精度（round(v, 1)），不是伪精度。
 */
function fmtTemp(t: number | null | undefined): string {
  return t == null ? '数据不足' : `${t.toFixed(1)}°`
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
              {/* ⚠️ 数据不足时必须显示「数据不足」，**绝不能显示一个具体数字**。
                  黑箱验收审计 F-02 的教训：旧实现兜底成 50.0，让"数据缺失"伪装成
                  一个中性真实读数，还顺着给出"适中 / 保持定投 / 建议权益 35%"。
                  重写时这里写的是 `Math.round(ov.temp.temperature)`，而 JS 里
                  `Math.round(null) === 0` → 数据不足时会显示 **"0°"**（还配着冷色），
                  等于把同一个反模式又带了回来。后端此时明确返回 temperature=null
                  （thermometer.py:145），故以前端判空为准。 */}
              {ov.temp.insufficient_data || ov.temp.temperature == null ? (
                <div className="text-3xl font-medium text-fg-3">数据不足</div>
              ) : (
                <div className="flex items-baseline gap-2.5">
                  <span className={'text-3xl font-medium ' + tempTone(ov.temp.temperature)}>
                    {fmtTemp(ov.temp.temperature)}
                  </span>
                  <span className="text-[13.5px] text-fg-2">
                    {plainText(ov.temp.level_desc)} · {plainText(ov.temp.action)}
                  </span>
                </div>
              )}
              {/* 温度条只在有读数时画 —— 数据不足时画一条 0% 的条同样是"假装有读数" */}
              {!ov.temp.insufficient_data && ov.temp.temperature != null && (
                <div className="my-2.5 h-[5px] overflow-hidden rounded-[3px] bg-inset">
                  <i
                    className={'block h-full ' + tempBar(ov.temp.temperature)}
                    style={{ width: `${Math.max(0, Math.min(100, ov.temp.temperature))}%` }}
                  />
                </div>
              )}
              <div className="text-[11.5px] leading-relaxed text-fg-4">
                {Object.entries(ov.temp.components ?? {})
                  .map(([k, v]) => `${DIM_LABEL[k] ?? k} ${v == null ? '缺失' : `${v.toFixed(1)}°`}`)
                  .join(' · ')}
              </div>
              {/* ⚠️ 估值分化告警。后端一直在给（temp.divergence.level / message），
                  旧前端也一直显示，但重写时**整个漏掉了** —— 属于安全披露丢失：
                  系统在说"我的几个估值信号互相矛盾，别过度相信这个读数"，
                  而界面装作没有这回事。审计 F-02 属于同一类问题。 */}
              {ov.temp.divergence?.level && ov.temp.divergence.level !== '一致' && (
                <div className="mt-2 rounded-md border border-line bg-inset px-2.5 py-2 text-[11.5px] leading-relaxed text-warn">
                  ⚠ {plainText(ov.temp.divergence.level)}
                  {ov.temp.divergence.message ? `：${plainText(ov.temp.divergence.message)}` : ''}
                </div>
              )}
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