import { endpoints } from '../api/endpoints'
import { Card } from '../components/Card'
import { CurveChart } from '../components/CurveChart'
import { MarketStrip } from '../components/MarketStrip'
import { PageHead } from '../components/PageHead'
import { Skeleton } from '../components/Skeleton'
import { SourceTag } from '../components/SourceTag'
import { VerdictCard } from '../components/VerdictCard'
import { Warming } from '../components/Warming'
import { dirClass, dirOf, dirSymbol, fmtMoney, fmtSignedMoney, fmtSignedPct } from '../lib/format'
import { useApi } from '../lib/useApi'

/**
 * 「今天」入口 —— 只回答**今天要不要动手**（DESIGN §5.4 页面职责表）。
 *
 * ⚠️ 2026-10-06 信息密度瘦身（第 2、5 条）：本页收敛成 **3 块核心 + 1 条小条**：
 *     ① 大数字（总资产，带"变化额 + 变化率 + 时间窗"三个参照）
 *     ② 结论句（必须落到 `要动手 / 不用动手 / 待数据` 三态之一）
 *     ③ 组合收益曲线
 *     ④ 市场小条（指数行情 + 消息面压成一行，`MarketStrip`）
 *   搬走的：持仓明细表（→「持仓」页本就有）、数据链路审计（→「设置」）、
 *   市场温度卡（→「设置」，见 `TemperatureCard.tsx`）。
 *   依据：三个独立开源产品首屏结构的交集（Wealthfolio / Ghostfolio / Rotki，
 *   计划书 §13.3.2）—— 首屏核心 = 一个大数字 + 一条曲线，其余**至多一块**。
 */
export default function Today() {
  const overview = useApi(endpoints.overview)
  const rebalance = useApi(endpoints.rebalance)

  const ov = overview.data
  const st = ov?.stats ?? null
  const dates = ov?.curve.dates ?? []
  // 在持**基金**数：holdings 是批次行（29 行 = 7 只基金），去重后才是用户理解的"只"
  const fundCount = ov
    ? new Set(ov.portfolio.holdings.filter((h) => h.status === 'holding').map((h) => h.code)).size
    : 0
  const pnlDir = dirOf(st?.total_pnl)
  const busy = overview.loading || rebalance.loading
  const warmingWait = Math.max(overview.warmingWait, rebalance.warmingWait)

  const refreshAll = () => {
    overview.refresh({ fresh: true })
    rebalance.refresh({ fresh: true })
  }

  return (
    <>
      <PageHead title="今天要做什么" sub="总资产 · 结论 · 组合收益">
        <SourceTag
          source={overview.source}
          asof={dates.length ? dates[dates.length - 1] : null}
          onRefresh={refreshAll}
          busy={busy}
        />
      </PageHead>

      <Warming seconds={busy ? warmingWait : 0} note="首次计算较慢，之后会命中缓存" />

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
          <Skeleton lines={3} />
        </Card>
      )}

      {ov && st && (
        <>
          {/* ── ① 大数字：总资产（全页唯一的视觉主导，DESIGN §5.1.1）──────
              ⚠️ 口径（沿自被删除的 `KpiRow`，别改）：
              · `stats.total_assets` = 持仓市值 + 现金弹药 + 在途，**不是**累计投入；
                所以它天然大于"已投"，这不是 bug。
              · 参照的**变化率**（`total_return_pct`，资金加权 pnl ÷ 累计成本）与
                曲线卡终值**同源** —— 故曲线卡不再重复显示大字号收益率（见 CurveChart）。
              · 参照必须三件套齐（变化额 + 变化率 + 时间窗），否则孤立数字不带信息
                （DESIGN §5.5）。 */}
          <Card lead title="组合" note="· 资金加权">
            <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
              <div className="num text-[40px] font-semibold leading-none tracking-[-.02em] text-fg sm:text-[48px]">
                {fmtMoney(st.total_assets)}
              </div>
              <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 text-[13px]">
                <span className={'num font-medium ' + dirClass(pnlDir)}>
                  {dirSymbol(pnlDir)}
                  {fmtSignedMoney(st.total_pnl)}
                </span>
                <span className={'num ' + dirClass(pnlDir)}>{fmtSignedPct(st.total_return_pct)}</span>
                <span className="text-fg-4">
                  累计未实现 · {dates[0]} ~ {dates[dates.length - 1]}
                </span>
              </div>
            </div>
            <div className="mt-2.5 text-[11.5px] text-fg-4">
              在持 {fundCount} 只
              {st.pending_amount ? ` · 在途 ${fmtMoney(st.pending_amount)}` : ''}
            </div>
          </Card>

          {/* ── ② 结论句：必须落到三态之一（DESIGN §5.1）──────────────── */}
          <VerdictCard
            rebalance={rebalance.data}
            loading={rebalance.loading}
            error={rebalance.error}
            support
          />

          {/* ── ③ 曲线 ───────────────────────────────────────────────── */}
          <CurveChart curve={ov.curve} />

          {/* ④ 市场小条（瘦身第 5 条）：指数行情 + 消息面压成**一块、一行**，
              逐条告警明细收进折叠区。改造前两者各占一张卡。 */}
          <MarketStrip />
        </>
      )}
    </>
  )
}
