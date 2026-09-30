import { endpoints } from '../api/endpoints'
import type { DcaPlan, InvestmentPlan } from '../api/types'
import { Card } from '../components/Card'
import { HoldingsTable } from '../components/HoldingsTable'
import { SourceTag } from '../components/SourceTag'
import { VerdictCard } from '../components/VerdictCard'
import { fmtMoney, fmtPct, plainText } from '../lib/format'
import { useApi } from '../lib/useApi'

/**
 * 建仓计划进度 —— 每只计划基金的已投 / 目标 / 下一步。
 * 只陈列数字与进度条；**不在这里给"该不该继续投"的建议**（那是调仓卡的职责）。
 */
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
      <div className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[11.5px] text-fg-3">
        <div>
          目标 <span className="mono text-fg-2">{plainText(plan.goal)}</span>
        </div>
        <div>
          期限 <span className="mono text-fg-2">{plainText(plan.horizon)}</span>
        </div>
        <div>
          风险偏好 <span className="text-fg-2">{plainText(plan.risk_pref)}</span>
        </div>
        <div>
          现金弹药 <span className="mono text-fg-2">{fmtMoney(plan.cash_reserve)}</span>
        </div>
        <div>
          计划资金 <span className="mono text-fg-2">{fmtMoney(plan.total_capital)}</span>
        </div>
        <div>
          已投 <span className="mono text-fg-2">{fmtMoney(plan.total_invested)}</span>
        </div>
      </div>

      <div className="space-y-2.5">
        {plan.funds.map((f) => (
          <div key={f.code}>
            <div className="flex items-baseline justify-between gap-2 text-[12.5px]">
              <span className="text-fg">
                {plainText(f.name)}
                <span className="mono ml-1.5 text-[11px] text-fg-4">{f.code}</span>
                <span className="ml-1.5 text-[11px] text-fg-4">{plainText(f.role)}</span>
              </span>
              <span className="mono shrink-0 text-fg-3">
                {fmtMoney(f.invested)} / {fmtMoney(f.target)}
              </span>
            </div>
            {/* 进度条：百分比来自后端 progress_pct（不自己算，避免与后端口径分叉） */}
            <div className="mt-1 h-[4px] overflow-hidden rounded-[2px] bg-inset">
              <i
                className="block h-full bg-accent"
                style={{ width: `${Math.max(0, Math.min(100, f.progress_pct))}%` }}
              />
            </div>
            <div className="mt-0.5 flex justify-between text-[11px] text-fg-4">
              <span>已投 {fmtPct(f.progress_pct)}</span>
              <span>余 {fmtMoney(f.remaining)}</span>
            </div>
          </div>
        ))}
      </div>

      {plan.notes && (
        <div className="mt-3 border-t border-line pt-2 text-[11.5px] leading-relaxed text-fg-4">
          {plainText(plan.notes)}
        </div>
      )}
    </Card>
  )
}

/** 定投计划执行情况。「到期未执行」是**待办提示**，不是装饰 —— 用状态色显式标出。 */
function DcaList({ plans }: { plans: DcaPlan[] }) {
  const due = plans.filter((p) => p.due).length
  return (
    <Card title="定投计划" note={plans.length ? `· ${plans.length} 条${due ? ` · ${due} 条到期未执行` : ''}` : ''}>
      {plans.length === 0 ? (
        <div className="text-sm text-fg-3">当前没有定投计划。</div>
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="pb-2 text-left text-[11px] font-normal text-fg-3">基金</th>
              <th className="pb-2 text-right text-[11px] font-normal text-fg-3">每期</th>
              <th className="pb-2 text-right text-[11px] font-normal text-fg-3">已执行</th>
              <th className="pb-2 text-right text-[11px] font-normal text-fg-3">状态</th>
            </tr>
          </thead>
          <tbody>
            {plans.map((p) => (
              <tr key={p.id} className="border-t border-line">
                <td className="py-2 pr-3 text-[13px]">
                  <span className="text-fg">{plainText(p.fund_name)}</span>
                  <span className="mono ml-1.5 text-[11.5px] text-fg-4">{p.fund_code}</span>
                </td>
                <td className="mono py-2 text-right text-[12.5px] text-fg-2">{fmtMoney(p.amount_per_period)}</td>
                <td className="mono py-2 text-right text-[12.5px] text-fg-2">
                  {p.executed_periods} / {p.expected_periods}
                </td>
                <td className="py-2 text-right text-[12.5px]">
                  {p.due ? <span className="text-warn">待执行</span> : <span className="text-fg-3">已跟上</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="mt-2 text-[11px] text-fg-4">
        频率说明：daily = 每个交易日；「已执行 / 应为」按计划起始日算。执行到期期数在旧仪表盘操作。
      </div>
    </Card>
  )
}

/**
 * 持仓入口（计划书 §6）—— 收敛旧前端的 position 视图。
 * 调仓结论 → 持仓明细 + 建仓计划 → 定投执行，按"先看结论、再看构成、最后看执行"排。
 *
 * ⚠️ 本页**只读**：加仓/减仓/对账等写操作仍在旧仪表盘（`/`）完成。
 * 写入口涉及改账本，按项目铁律要先有整库快照与回滚点，单独一批再迁（见计划书 §9）。
 */
export default function Position() {
  const overview = useApi(endpoints.overview)
  const rebalance = useApi(endpoints.rebalance)
  const plan = useApi(endpoints.plan)
  const dca = useApi(endpoints.dca)

  const ov = overview.data
  const busy = overview.loading || rebalance.loading || plan.loading || dca.loading
  const wait = Math.max(overview.warmingWait, rebalance.warmingWait, plan.warmingWait, dca.warmingWait)

  const refreshAll = () => {
    overview.refresh({ fresh: true })
    rebalance.refresh({ fresh: true })
    plan.refresh({ fresh: true })
    dca.refresh({ fresh: true })
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-[17px] font-medium tracking-[.2px]">
          持仓
          <span className="ml-1.5 text-[13px] font-normal text-fg-3">调仓 · 明细 · 计划 · 定投</span>
        </h1>
        <SourceTag
          source={overview.source}
          asof={ov ? ov.curve.dates[ov.curve.dates.length - 1] : null}
          onRefresh={refreshAll}
          busy={busy}
        />
      </div>

      {wait > 0 && busy && (
        <div className="mb-3.5 rounded-md border border-line bg-inset px-3 py-2 text-xs text-fg-2">
          正在计算…还需约 {wait} 秒
        </div>
      )}

      {overview.error && (
        <Card lead title="持仓" note="· 结论">
          <div className="text-sm text-fg-2">读取失败：{overview.error}</div>
        </Card>
      )}

      {!ov && !overview.error && (
        <Card lead title="持仓" note="· 结论">
          <div className="text-sm text-fg-3">加载中…</div>
        </Card>
      )}

      {ov && (
        <>
          <VerdictCard
            rebalance={rebalance.data}
            explain={null}
            loading={rebalance.loading}
            error={rebalance.error}
          />

          <div className="grid items-start gap-3.5 md:grid-cols-[1.35fr_1fr]">
            <HoldingsTable holdings={ov.portfolio.holdings} />
            <PlanProgress plan={plan.data?.plan ?? ov.plan} />
          </div>

          {dca.data ? (
            <DcaList plans={dca.data} />
          ) : (
            <Card title="定投计划">
              <div className={'text-sm ' + (dca.error ? 'text-fg-2' : 'text-fg-3')}>
                {dca.error ? `读取失败：${dca.error}` : '加载中…'}
              </div>
            </Card>
          )}
        </>
      )}
    </>
  )
}
