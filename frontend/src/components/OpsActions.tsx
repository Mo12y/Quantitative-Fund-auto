import { useState } from 'react'
import { ApiError } from '../api/client'
import { endpoints } from '../api/endpoints'
import type { DcaPlan, Holding, InvestmentPlan } from '../api/types'
import { Card } from './Card'
import { ConfirmDialog, type ConfirmSpec } from './ConfirmDialog'
import { fmtMoney, plainText } from '../lib/format'

const INPUT =
  'w-full rounded-md border border-line bg-inset px-2.5 py-1.5 text-body-sm text-fg ' +
  'outline-none transition-colors focus:border-line-strong'

const BTN =
  'rounded-md border border-line-strong px-2.5 py-1 text-caption text-fg-2 hover:text-fg disabled:opacity-45'

const BTN_WARN =
  'rounded-md border border-warn/35 px-2.5 py-1 text-caption text-warn hover:bg-warn/[.08] disabled:opacity-45'

/**
 * 设置页的写操作面板（B-4b-3）—— 数据运维 + 定投 + 投资计划，**全部先过确认卡**。
 *
 * ⚠️ 高危操作必须**明示"将产生 N 笔、合计 ¥X"**（用户 2026-10-05 定案）：
 * `/api/reconcile` 会按生效日净值把"待确认买入"结算成真持仓；`/api/dca` 的
 * `sync` / `backfill` / `run` 会**自己记买入**（`auto_executed`）——
 * 不写清笔数金额，用户点一下就落若干笔真账。
 *
 * ⚠️ **撤销范围（如实）**：只有「持仓」页那 5 个 action（买/卖/改/删/分红）写 `ledger_commits`。
 * 本页的操作**不记提交**，所以确认卡里明确写「不提供撤销」—— 不假装能撤。
 */
export function OpsActions({
  holdings,
  plans,
  plan,
  onDone,
  className = '',
}: {
  holdings: Holding[]
  plans: DcaPlan[]
  plan: InvestmentPlan | null
  onDone: () => void
  className?: string
}) {
  const [pending, setPending] = useState<{ spec: ConfirmSpec; run: () => Promise<string> } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<string | null>(null)

  // 计划表单
  const [goal, setGoal] = useState('')
  const [capital, setCapital] = useState('')
  const [cash, setCash] = useState('')

  // ── 预估值（确认卡要用"将产生 N 笔、合计 ¥X"）──
  const pendingBuys = holdings.filter((h) => h.status === 'pending_confirm')
  const pendingAmt = pendingBuys.reduce((s, h) => s + (Number(h.buy_amount) || 0), 0)
  const duePlans = plans.filter((p) => p.due)
  const dueAmt = duePlans.reduce((s, p) => s + (Number(p.amount_per_period) || 0), 0)

  async function go() {
    if (!pending) return
    setBusy(true)
    setError(null)
    try {
      const msg = await pending.run()
      setResult(msg)
      setPending(null)
      onDone()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  /** 统一的"出确认卡"入口 */
  function ask(spec: ConfirmSpec, run: () => Promise<string>) {
    setError(null)
    setResult(null)
    setPending({ spec, run })
  }

  return (
    <Card className={'min-w-0 ' + className} title="数据运维与写入" note="· 每个操作都会先出确认卡">
      {/* ── 数据运维 ─────────────────────────────────────────── */}
      <div className="text-caption text-fg-4">数据运维</div>
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          type="button"
          className={BTN}
          onClick={() =>
            ask(
              {
                title: '确认更新净值',
                fields: [
                  { label: '范围', value: `${new Set(holdings.map((h) => h.code)).size} 只持仓基金` },
                ],
                note: '会联网逐只抓取最新净值（可能几十秒）。不产生任何买入。不可撤销（但可重复执行，幂等）。',
                confirmText: '确认更新',
              },
              async () => {
                const env = await endpoints.navUpdate()
                return env.message || '净值更新完成'
              },
            )
          }
        >
          更新净值
        </button>

        <button
          type="button"
          className={pendingBuys.length ? BTN_WARN : BTN}
          onClick={() =>
            ask(
              {
                title: '确认对账',
                fields: [
                  { label: '待确认买入', value: `${pendingBuys.length} 笔` },
                  { label: '合计金额', value: fmtMoney(pendingAmt) },
                ],
                warning: pendingBuys.length
                  ? `对账会把这 ${pendingBuys.length} 笔按生效日净值结算成真持仓（合计 ${fmtMoney(pendingAmt)}）。`
                  : '当前没有待确认买入；对账仍会跑一次分红自动落账，可能新增持仓。',
                note: '幂等：重复执行不会重复入账。⚠️ 本页操作不记提交，不提供撤销。',
                confirmText: '确认对账',
              },
              async () => {
                const env = await endpoints.reconcile()
                const d = (env.data ?? {}) as { settled_buys?: number; settled_sells?: number;
                  dividend?: { posted_n?: number } }
                return `对账完成：结算买入 ${d.settled_buys ?? 0} 笔、卖出 ${d.settled_sells ?? 0} 笔、`
                  + `分红入账 ${d.dividend?.posted_n ?? 0} 笔`
              },
            )
          }
        >
          对账（{pendingBuys.length} 笔待确认）
        </button>
      </div>

      {/* ── 定投 ─────────────────────────────────────────────── */}
      <div className="mt-4 border-t border-line pt-3 text-caption text-fg-4">
        定投计划（{plans.length} 条{duePlans.length ? ` · ${duePlans.length} 条到期` : ''}）
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          type="button"
          className={BTN}
          onClick={() =>
            ask(
              {
                title: '确认同步定投期次',
                fields: [
                  { label: '涉及计划', value: `${plans.length} 条` },
                  { label: '待补期数', value: `${plans.reduce((s, p) => s + Math.max(0, p.expected_periods - p.executed_periods), 0)} 期` },
                ],
                warning: '同步只推算应投期数，并自动补录最近到期的 1 期 —— 该期会记一笔真实买入。',
                note: '其余到期期数标记为「待补录」，需再用「补录」逐期入账。不可撤销。',
                confirmText: '确认同步',
              },
              async () => {
                const env = await endpoints.dcaAction({ action: 'sync' })
                return env.message || '定投期次已同步'
              },
            )
          }
        >
          同步期次
        </button>

        <button
          type="button"
          className={BTN_WARN}
          onClick={() =>
            ask(
              {
                title: '确认补录定投',
                fields: [
                  { label: '补录范围', value: '所有「待补录」期次' },
                ],
                warning: '补录会为每一期待补录各记一笔真实买入（走 T+1 确认规则）。',
                note: '请先「同步期次」确定应投期数。不可撤销。',
                confirmText: '确认补录',
              },
              async () => {
                const env = await endpoints.dcaAction({ action: 'backfill' })
                return env.message || '补录完成'
              },
            )
          }
        >
          补录待投期次
        </button>

        <button
          type="button"
          className={duePlans.length ? BTN_WARN : BTN}
          onClick={() =>
            ask(
              {
                title: '确认执行到期定投',
                fields: [
                  { label: '到期计划', value: `${duePlans.length} 条` },
                  { label: '合计每期金额', value: fmtMoney(dueAmt) },
                ],
                warning: duePlans.length
                  ? `执行会为这 ${duePlans.length} 条各记一笔真实买入（合计 ${fmtMoney(dueAmt)}）。`
                  : '当前没有到期的定投计划。',
                note: '不可撤销。',
                confirmText: '确认执行',
                danger: true,
              },
              async () => {
                const env = await endpoints.dcaAction({ action: 'run' })
                return env.message || '定投已执行'
              },
            )
          }
        >
          执行到期定投（{duePlans.length}）
        </button>
      </div>

      {/* 单条计划的 暂停 / 恢复 / 删除 */}
      {plans.length > 0 && (
        <div className="mt-3 flex flex-col gap-1.5">
          {plans.map((p) => (
            <div key={p.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-caption">
              <span className="min-w-0 flex-1 truncate text-fg-3">
                {plainText(p.fund_name)}
                <span className="mono ml-1.5 text-fg-4">{p.fund_code}</span>
                <span className="num ml-1.5 text-fg-4">{fmtMoney(p.amount_per_period)}/{p.frequency}</span>
              </span>
              {(['pause', 'resume', 'delete'] as const).map((a) => (
                <button
                  key={a}
                  type="button"
                  className={a === 'delete' ? BTN_WARN : BTN}
                  onClick={() =>
                    ask(
                      {
                        title: a === 'delete' ? '确认删除定投计划' : a === 'pause' ? '确认暂停定投' : '确认恢复定投',
                        fields: [
                          { label: '计划', value: `ID${p.id} ${plainText(p.fund_name)}` },
                          { label: '每期', value: fmtMoney(p.amount_per_period) },
                        ],
                        note: '只改计划状态，不产生买入。不可撤销。',
                        danger: a === 'delete',
                        confirmText: '确认',
                      },
                      async () => {
                        const env = await endpoints.dcaAction({ action: a, plan_id: p.id })
                        return env.message || '已完成'
                      },
                    )
                  }
                >
                  {a === 'delete' ? '删除' : a === 'pause' ? '暂停' : '恢复'}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* ── 投资计划 ─────────────────────────────────────────── */}
      <div className="mt-4 border-t border-line pt-3 text-caption text-fg-4">
        投资计划{plan ? `（ID${plan.id} ${plainText(plan.name)}）` : '（无生效计划）'}
      </div>
      {plan && (
        <>
          <div className="mt-2 grid gap-2.5 sm:grid-cols-3">
            <div>
              <label className="mb-1 block text-caption text-fg-4" htmlFor="ops-goal">新目标（可留空）</label>
              <input id="ops-goal" className={INPUT} value={goal} onChange={(e) => setGoal(e.target.value)} />
            </div>
            <div>
              <label className="mb-1 block text-caption text-fg-4" htmlFor="ops-cap">计划资金（可留空）</label>
              <input id="ops-cap" className={INPUT} value={capital} inputMode="decimal"
                     onChange={(e) => setCapital(e.target.value)} />
            </div>
            <div>
              <label className="mb-1 block text-caption text-fg-4" htmlFor="ops-cash">现金弹药（可留空）</label>
              <input id="ops-cash" className={INPUT} value={cash} inputMode="decimal"
                     onChange={(e) => setCash(e.target.value)} />
            </div>
          </div>
          <div className="mt-2.5 flex flex-wrap gap-2">
            <button
              type="button"
              className={BTN}
              onClick={() => {
                const body: Record<string, unknown> = { action: 'update' }
                if (goal.trim()) body.goal = goal.trim()
                if (capital.trim()) body.total_capital = Number(capital)
                if (cash.trim()) body.cash_reserve = Number(cash)
                if (Object.keys(body).length === 1) {
                  setError('请至少填一个字段')
                  return
                }
                ask(
                  {
                    title: '确认修改投资计划',
                    fields: [
                      ...(goal.trim() ? [{ label: '目标', value: goal.trim() }] : []),
                      ...(capital.trim() ? [{ label: '计划资金', value: fmtMoney(Number(capital)) }] : []),
                      ...(cash.trim() ? [{ label: '现金弹药', value: fmtMoney(Number(cash)) }] : []),
                    ],
                    note: '只改计划信息，不动账本持仓。不可撤销。',
                    confirmText: '确认修改',
                  },
                  async () => {
                    const env = await endpoints.planAction(body as never)
                    setGoal(''); setCapital(''); setCash('')
                    return env.message || '计划已更新'
                  },
                )
              }}
            >
              修改计划信息
            </button>
            <button
              type="button"
              className={BTN_WARN}
              onClick={() =>
                ask(
                  {
                    title: '确认删除投资计划',
                    fields: [{ label: '计划', value: `ID${plan.id} ${plainText(plan.name)}` }],
                    warning: '删除后该计划及其基金条目全部移除。持仓不受影响（账本在持仓表）。',
                    note: '不可撤销。',
                    danger: true,
                    confirmText: '确认删除',
                  },
                  async () => {
                    const env = await endpoints.planAction({ action: 'delete_plan' })
                    return env.message || '计划已删除'
                  },
                )
              }
            >
              删除计划
            </button>
          </div>
        </>
      )}

      {error && !pending && <div className="mt-3 text-body-sm text-rise">{error}</div>}
      {result && (
        <div className="mt-3 rounded-[var(--radius-md)] bg-inset px-3 py-2.5 text-body-sm text-fg-2">
          {result}
        </div>
      )}

      {pending && (
        <ConfirmDialog
          spec={pending.spec}
          busy={busy}
          error={error}
          onConfirm={() => void go()}
          onCancel={() => {
            setPending(null)
            setError(null)
          }}
        />
      )}
    </Card>
  )
}
