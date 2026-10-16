import { endpoints } from '../api/endpoints'
import { Bento, span } from '../components/Bento'
import { Card } from '../components/Card'
import { DrillDown } from '../components/DrillDown'
import { PageHead } from '../components/PageHead'
import { Skeleton } from '../components/Skeleton'
import { SourceTag } from '../components/SourceTag'
import { Warming } from '../components/Warming'
import { plainText } from '../lib/format'
import { useApi } from '../lib/useApi'

/**
 * 数据状态入口（计划书 §4.1）—— 替代旧「设置」页里与数据相关的部分：
 * 数据新鲜度、日历覆盖与滞后、采集状态、数据链路（审计与溯源）。
 *
 * ⚠️ 与「账本」的分工：本页只回答"数据从哪来、新不新、怎么修"；
 *    写操作（对账/净值/计划）在「账本」页 —— 这里**不做任何写操作**。
 * ⚠️ DESIGN §5.1：「审计 / 溯源 / 口径 / 命令表一律默认折叠，且不得出现在第一屏」。
 */

/** 数据相关命令清单 —— 与 `src/main.py` 真实命令表一一对应（不写不存在的命令） */
const CMDS: { cmd: string; use: string }[] = [
  { cmd: 'python src/main.py test', use: '数据源连通自检' },
  { cmd: 'python src/main.py snapshot', use: '全市场当日净值快照（日常增量主路径）' },
  { cmd: 'python src/main.py nav', use: '采集基金净值历史' },
  { cmd: 'python src/main.py index', use: '更新指数估值（PE/PB）' },
  { cmd: 'python src/main.py calendar', use: '刷新交易日历' },
  { cmd: 'python src/main.py collect', use: '采集全市场基金列表 + 指数估值' },
  { cmd: 'python src/main.py enrich', use: '补充基金详情（经理/费率等）' },
  { cmd: 'python src/main.py fees', use: '补采真·运作费率（可断点续采）' },
  { cmd: 'python src/main.py precompute', use: '预计算快照，让首屏免冷算' },
  { cmd: 'python src/main.py web', use: '启动 Web 界面（默认 :5020）' },
]

const TH = 'field pb-2 text-left'

/** 两个 `YYYY-MM-DD` 之间的整天数（a − b）；任一缺失返回 null。 */
function dayDiff(a: string | null, b: string | null): number | null {
  if (!a || !b) return null
  const d = (new Date(a).getTime() - new Date(b).getTime()) / 86400000
  return Number.isFinite(d) ? Math.round(d) : null
}

export default function Data() {
  const overview = useApi(endpoints.overview)
  const explain = useApi(endpoints.explain)
  const calendar = useApi(endpoints.calendar)
  const live = useApi(endpoints.marketLive)

  const ov = overview.data
  const cal = calendar.data
  const lv = live.data

  const busy = overview.loading || explain.loading || calendar.loading || live.loading
  const wait = Math.max(
    overview.warmingWait, explain.warmingWait, calendar.warmingWait, live.warmingWait,
  )
  const refreshAll = () => {
    overview.refresh({ fresh: true })
    explain.refresh({ fresh: true })
    calendar.refresh({ fresh: true })
    live.refresh({ fresh: true })
  }

  const navLatest = ov && ov.curve.dates.length ? ov.curve.dates[ov.curve.dates.length - 1] : null
  const calEnd = cal && cal.dates.length ? cal.dates[cal.dates.length - 1] : null
  const calLag = dayDiff(navLatest, calEnd)

  return (
    <>
      <PageHead title="数据状态" sub="新鲜度 · 日历 · 采集状态 · 数据链路（只读）">
        <SourceTag source={overview.source} onRefresh={refreshAll} busy={busy} />
      </PageHead>

      <Warming seconds={busy ? wait : 0} />

      <Bento>
        {/* ── 数据新鲜度 ──────────────────────────────────────── */}
        <Card className={span(7)} title="数据新鲜度" note="· 本地净值与行情">
          <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
            <div>
              <div className="field">账本库</div>
              <div className="mono text-body text-fg-2">data/fund_quant.db</div>
            </div>
            <div>
              <div className="field">本地净值最新日</div>
              <div className="mono text-body text-fg-2">{navLatest ?? '—'}</div>
            </div>
            <div>
              <div className="field">净值刷新</div>
              <div className="text-body text-fg-2">在「账本」页执行（只拉当前持仓）</div>
            </div>
          </div>

          {/* 行情时滞：`lag_note` 是后端给的**量化**表述，必须原样显示（不许只说"可能偏旧"） */}
          <div className="mt-3.5 border-t border-line pt-2.5 text-body-sm">
            {live.error ? (
              <span className="text-fg-2">行情端点读取失败：{live.error}</span>
            ) : !lv ? (
              <span className="skeleton inline-block h-4 w-48 rounded-full" aria-hidden="true" />
            ) : lv.lag_note ? (
              <span className="text-fg-3">{plainText(lv.lag_note)}</span>
            ) : (
              <span className="text-fg-3">
                指数盘中行情{lv.available ? '可用' : '不可用'}
                {lv.reason ? `：${plainText(lv.reason)}` : ''}
              </span>
            )}
          </div>
        </Card>

        {/* ── 交易日历 ────────────────────────────────────────── */}
        <Card className={span(5)} title="交易日历" note="· T+1 确认 / 定投排期依赖">
          {cal ? (
            <>
              <div className="grid grid-cols-2 gap-x-6 gap-y-3">
                <div>
                  <div className="field">覆盖天数</div>
                  <div className="num text-metric font-semibold text-fg">{cal.count}</div>
                </div>
                <div>
                  <div className="field">覆盖区间</div>
                  <div className="mono text-body text-fg-2">
                    {cal.dates[0] ?? '—'} ~ {calEnd ?? '—'}
                  </div>
                </div>
              </div>
              <div className="mt-3 rounded-[var(--radius-md)] bg-inset px-3.5 py-2.5 text-body-sm">
                {calLag == null ? (
                  <span className="text-fg-3">日历滞后：无法计算（缺净值日或日历）</span>
                ) : calLag <= 0 ? (
                  <span className="text-fg-2">日历已覆盖到最新净值日（无滞后）</span>
                ) : (
                  <span className={calLag > 30 ? 'text-warn' : 'text-fg-2'}>
                    日历滞后 {calLag} 天（最新净值日 − 日历末日）—— 超出即需刷新
                  </span>
                )}
              </div>
              <div className="mt-2 text-caption leading-relaxed text-fg-4">
                刷新命令：<span className="mono text-fg-3">{cal.refresh_cmd}</span>
                （要出网，故不放请求路径）。
              </div>
            </>
          ) : calendar.error ? (
            <div className="text-sm text-fg-2">读取失败：{calendar.error}</div>
          ) : (
            <Skeleton lines={3} />
          )}
        </Card>

        {/* ── 数据链路（审计与溯源）────────────────────────────── */}
        <Card
          className={span(7)}
          title="数据链路"
          note="· 采集 → 清洗 → 特征 → 建模 → 评估（默认折叠）"
        >
          {explain.data ? (
            <DrillDown explain={explain.data} />
          ) : explain.error ? (
            <div className="text-sm text-fg-2">读取失败：{explain.error}</div>
          ) : (
            <Skeleton lines={3} />
          )}
        </Card>

        {/* ── 采集与运维 ──────────────────────────────────────── */}
        <Card className={span(5)} title="采集与运维" note="· 命令清单（默认折叠）">
          <details>
            <summary className="cursor-pointer list-none text-body-sm text-fg-2">
              <span className="mr-1 text-fg-3">⌄</span>
              运维命令 · {CMDS.length} 条（默认折叠）
            </summary>
            <table className="mt-2 w-full border-collapse">
              <thead>
                <tr>
                  <th className={TH}>命令</th>
                  <th className={TH}>用途</th>
                </tr>
              </thead>
              <tbody>
                {CMDS.map((c) => (
                  <tr key={c.cmd} className="row-hover border-t border-line">
                    <td className="mono py-1.5 pr-3 text-body-sm text-fg-2">{c.cmd}</td>
                    <td className="py-1.5 text-body-sm text-fg-3">{c.use}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
          <div className="mt-3 border-t border-line pt-2.5 text-caption leading-relaxed text-fg-4">
            本页只读：写操作（对账 / 净值更新 / 计划维护）在「账本」页。
            行情端点抓不到时<b className="font-semibold text-fg-3">如实返回不可用</b>，不会拿旧值冒充实时。
          </div>
        </Card>
      </Bento>
    </>
  )
}
