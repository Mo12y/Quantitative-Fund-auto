import { endpoints } from '../api/endpoints'
import type { DcaPlan, InvestmentPlan } from '../api/types'
import { Card } from '../components/Card'
import { CurveChart } from '../components/CurveChart'
import { HoldingsTable } from '../components/HoldingsTable'
import { LedgerActions } from '../components/LedgerActions'
import { OpsActions } from '../components/OpsActions'
import { PageHead } from '../components/PageHead'
import { Skeleton } from '../components/Skeleton'
import { SourceTag } from '../components/SourceTag'
import { Warming } from '../components/Warming'
import { dirClass, dirOf, dirSymbol, fmtMoney, fmtSignedMoney, fmtSignedPct, plainText } from '../lib/format'
import { useApi } from '../lib/useApi'

/**
 * 账本入口 —— 我持有什么 · 值多少 · 怎么改（DESIGN §5.4 页面职责）。
 *
 * 收敛旧前端的 position + settings 两个视图（计划书 §4.1）：
 *   组合总览（大数字 + 曲线）→ 持仓明细 + 建仓计划 → 记一笔 → 定投 → 运维写操作。
 * ⚠️ 调仓结论卡已删除 —— 它依赖的旧调仓端点随旧命题一并移除；
 *    新命题下"要不要动手"的证据在决策台（行为镜/反事实/门槛），账本页只回答"我有什么、怎么改"。
 */

/** 建仓计划进度 —— 每只计划基金的已投 / 目标 / 下一步。 */
function PlanProgress({ plan }: { plan: InvestmentPlan | null }) {
  if (!plan) {
    return (
      <Card title="建仓计划">
        <div className="text-sm text-fg-3">当前没有生效中的投资计划。</div>
      </Card>
    )
  }
  return (
    <Card title="建仓计划" note={`· ${plainText(plan.name)}`}>
      <div className="mb-3 text-caption text-fg-4">
        计划的完整配置（目标 / 期限 / 资金口径）见下方「投资计划维护」折叠区。
      </div>
      <div className="space-y-2.5">
        {plan.funds.map((f) => (
          <div key={f.code}>
            <div className="flex items-baseline justify-between gap-2 text-body-sm">
              <span className="text-fg">
                {plainText(f.name)}
                <span className="mono ml-1.5 text-caption text-fg-4">{f.code}</span>
                <span className="ml-1.5 text-caption text-fg-4">{plainText(f.role)}</span>
              </span>
              <span className="mono shrink-0 text-fg-3">
                {fmtMoney(f.invested)} / {fmtMoney(f.target)}
              </span>
            </div>
            {/* 进度条：百分比来自后端 progress_pct（不自己算，避免与后端口径分叉） */}
            <div className="mt-1 h-[5px] overflow-hidden rounded-full bg-inset">
              <i
                className="grad-brand grow-x block h-full rounded-full"
                style={{ width: `${Math.max(0, Math.min(100, f.progress_pct))}%` }}
              />
            </div>
            <div className="mt-0.5 text-right text-caption text-fg-4">
              余 <span className="num">{fmtMoney(f.remaining)}</span>
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

/** 定投计划执行情况。「到期未执行」是**待办提示**，不是装饰 —— 用状态色显式标出。 */
function DcaList({ plans }: { plans: DcaPlan[] }) {
  const due = plans.filter((p) => p.due).length
  return (
    <Card title="定投计划" note={plans.length ? `· ${plans.length} 条${due ? '（含到期未执行）' : ''}` : ''}>
      {plans.length === 0 ? (
        <div className="text-sm text-fg-3">当前没有定投计划。</div>
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="field pb-2 text-left">基金</th>
              <th className="field pb-2 text-right">每期</th>
              <th className="field pb-2 text-right">已执行</th>
              <th className="field pb-2 text-right">状态</th>
            </tr>
          </thead>
          <tbody>
            {plans.map((p) => (
              <tr key={p.id} className="border-t border-line">
                <td className="py-2 pr-3 text-body">
                  <span className="text-fg">{plainText(p.fund_name)}</span>
                  <span className="mono ml-1.5 text-caption text-fg-4">{p.fund_code}</span>
                </td>
                <td className="num py-2 text-right text-body-sm text-fg-2">{fmtMoney(p.amount_per_period)}</td>
                <td className="num py-2 text-right text-body-sm text-fg-2">
                  {p.executed_periods} / {p.expected_periods}
                </td>
                <td className="py-2 text-right text-body-sm">
                  {p.due ? <span className="text-warn">待执行</span> : <span className="text-fg-3">已跟上</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="mt-2 text-caption text-fg-4">
        频率：daily = 每个交易日；「已执行 / 应为」按计划起始日算。
      </div>
    </Card>
  )
}

export default function Ledger() {
  const overview = useApi(endpoints.overview)
  const plan = useApi(endpoints.plan)
  const dca = useApi(endpoints.dca)

  const ov = overview.data
  const busy = overview.loading || plan.loading || dca.loading
  const wait = Math.max(overview.warmingWait, plan.warmingWait, dca.warmingWait)

  const refreshAll = () => {
    overview.refresh({ fresh: true })
    plan.refresh({ fresh: true })
    dca.refresh({ fresh: true })
  }

  const st = ov?.stats ?? null
  const dates = ov?.curve.dates ?? []
  // 在持**基金**数：holdings 是批次行（29 行 = 7 只基金），去重后才是用户理解的"只"
  const fundCount = ov
    ? new Set(ov.portfolio.holdings.filter((h) => h.status === 'holding').map((h) => h.code)).size
    : 0
  const pnlDir = dirOf(st?.total_pnl)

  return (
    <>
      <PageHead title="账本" sub="持仓 · 计划 · 定投 · 对账与净值">
        <SourceTag
          source={overview.source}
          asof={dates.length ? dates[dates.length - 1] : null}
          onRefresh={refreshAll}
          busy={busy}
        />
      </PageHead>

      <Warming seconds={busy ? wait : 0} />

      {overview.error && (
        <Card lead title="账本">
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
        <Card lead title="账本">
          <Skeleton lines={3} />
        </Card>
      )}

      {ov && st && (
        <>
          {/* ── 组合总览：大数字（全页唯一的视觉主导，DESIGN §5.1.1）──────
              ⚠️ 口径（沿自旧「今天」页，别改）：
              · `stats.total_assets` = 持仓市值 + 现金弹药 + 在途，**不是**累计投入；
              · 参照三件套齐（变化额 + 变化率 + 时间窗），否则孤立数字不带信息（§5.5）。 */}
          <Card lead title="组合" note="· 资金加权">
            <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
              <div className="num text-hero font-semibold leading-none tracking-[-.02em] text-fg">
                {fmtMoney(st.total_assets)}
              </div>
              <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 text-body">
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
            <div className="mt-2.5 text-caption text-fg-4">
              在持 {fundCount} 只
              {st.pending_amount ? ` · 在途 ${fmtMoney(st.pending_amount)}` : ''}
            </div>
          </Card>

          <CurveChart curve={ov.curve} />

          <div className="grid items-start gap-4 md:grid-cols-[1.4fr_1fr]">
            <HoldingsTable holdings={ov.portfolio.holdings} stats={ov.stats} />
            <PlanProgress plan={plan.data?.plan ?? ov.plan} />
          </div>

          {/* 写操作面板：买卖改删 + 撤销（每个写操作先出确认卡） */}
          <LedgerActions holdings={ov.portfolio.holdings} onDone={refreshAll} />

          {dca.data ? (
            <DcaList plans={dca.data} />
          ) : (
            <Card title="定投计划">
              {dca.error ? <div className="text-sm text-fg-2">读取失败：{dca.error}</div> : <Skeleton lines={2} />}
            </Card>
          )}

          {/* 运维写操作（迁移自旧「设置」页的 OpsActions）：计划 / 定投 / 净值 / 对账。
              会自己产生真实买入的（对账 / 定投执行）在确认卡里明示笔数与金额。 */}
          <OpsActions
            holdings={ov.portfolio.holdings}
            plans={dca.data ?? []}
            plan={plan.data?.plan ?? ov.plan}
            onDone={refreshAll}
          />
        </>
      )}
    </>
  )
}
