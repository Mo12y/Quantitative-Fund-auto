import { useState } from 'react'
import { ApiError } from '../api/client'
import { endpoints } from '../api/endpoints'
import type { Holding, HoldingsAction } from '../api/types'
import { Card } from './Card'
import { ConfirmDialog, type ConfirmSpec } from './ConfirmDialog'
import { fmtMoney, plainText } from '../lib/format'

/** 输入框统一样式（新前端至此第一次出现 `<input>` —— 写操作才需要）。 */
const INPUT =
  'w-full rounded-md border border-line bg-inset px-2.5 py-1.5 text-body-sm text-fg ' +
  'outline-none transition-colors focus:border-line-strong disabled:opacity-45'

const LABEL = 'mb-1 block text-caption text-fg-4'

const today = () => new Date().toISOString().slice(0, 10)

type Op = 'buy' | 'sell' | 'update' | 'delete' | 'dividend_policy'

const OPS: { key: Op; label: string }[] = [
  { key: 'buy', label: '记买入' },
  { key: 'sell', label: '记卖出' },
  { key: 'update', label: '改金额/日期' },
  { key: 'dividend_policy', label: '分红方式' },
  { key: 'delete', label: '删除持仓' },
]

/**
 * 账本写操作面板 —— **所有写操作都要过确认卡**（用户 2026-10-05 定案）。
 *
 * 两条设计约束（不是随手加的）：
 *  1. ⚠️ **确认卡必须先于请求**。旧前端用的是原生 `confirm()`（一行纯文本），
 *     而这里要逐行列出"基金 / 日期 / 金额"，并按 T+1 规则说清确认日 —— 用户按回车前
 *     得先看清自己在做什么。写错了改的是**真实资金账本**。
 *  2. ⚠️ **成功后要能撤销**。`/api/holdings` 会回一个 `commit_id`，
 *     用它调 `/api/holdings/rollback` 反向重放（只允许撤销最近 20 条、且不能重复撤销）。
 *
 * 高危操作（定投 `sync`/`backfill`/`run`、`/api/reconcile`）在「设置」页，
 * 它们的确认卡要额外明示"将产生 N 笔真实买入"（见 `Settings` / `DcaPanel`）。
 */
export function LedgerActions({
  holdings,
  onDone,
}: {
  holdings: Holding[]
  /** 写成功 / 撤销成功后的回调（调用方刷新数据） */
  onDone: () => void
}) {
  const [op, setOp] = useState<Op>('buy')

  // ── 表单状态 ──
  const [code, setCode] = useState('')
  const [date, setDate] = useState(today())
  const [amount, setAmount] = useState('')
  const [after, setAfter] = useState(false)
  const [hid, setHid] = useState<string>('')
  const [policy, setPolicy] = useState<'reinvest' | 'cash'>('reinvest')

  // ── 确认流 / 结果 ──
  const [pending, setPending] = useState<{ spec: ConfirmSpec; body: HoldingsAction } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ text: string; commitId: number | null } | null>(null)

  const selected = holdings.find((h) => String(h.id) === hid) ?? null
  const amt = Number(amount)

  /** 校验 + 生成确认卡。返回 false 表示校验没过（错误已 set）。 */
  function prepare(): boolean {
    setError(null)
    setResult(null)
    if (op === 'buy') {
      if (!code.trim()) return fail('请填基金代码')
      if (!(amt > 0)) return fail('买入金额需大于 0')
      setPending({
        body: { action: 'buy', code: code.trim(), date, amount: amt, after_cutoff: after },
        spec: {
          title: '确认记买入',
          fields: [
            { label: '基金代码', value: code.trim() },
            { label: '买入日期', value: date },
            { label: '金额', value: fmtMoney(amt) },
          ],
          note:
            '公募 T+1 规则：15:00 前下单按当日净值成交、T+1 确认份额' +
            (after ? '；你勾了「15:00 后提交」，将按下一交易日确认。' : '。') +
            ' 确认日之前份额未到账，先计为「待确认」。',
        },
      })
      return true
    }
    if (!selected) return fail('请先选择一笔持仓')
    const base = `持仓 ID=${selected.id} ${plainText(selected.name)}(${selected.code})`
    if (op === 'sell') {
      if (!(amt > 0)) return fail('卖出金额需大于 0')
      setPending({
        body: { action: 'sell', id: selected.id, date, amount: amt },
        spec: {
          title: '确认记卖出',
          fields: [
            { label: '持仓', value: base },
            { label: '卖出日期', value: date },
            { label: '金额', value: fmtMoney(amt) },
          ],
          note: '按申请日（T日）净值成交，T+1 确认份额、T+2 到账；确认前状态为「待卖出」。',
        },
      })
      return true
    }
    if (op === 'update') {
      if (!amount.trim() && !date) return fail('请至少填金额或日期')
      setPending({
        body: { action: 'update', id: selected.id, date,
                amount: amount.trim() ? amt : undefined },
        spec: {
          title: '确认修改持仓',
          fields: [
            { label: '持仓', value: base },
            ...(amount.trim() ? [{ label: '新金额', value: fmtMoney(amt) }] : []),
            ...(date ? [{ label: '新日期', value: date }] : []),
          ],
          note: '改金额会按记录的买入净值重算份额，并同步对应的买入流水（复式记账口径一致）。',
        },
      })
      return true
    }
    if (op === 'dividend_policy') {
      const label = policy === 'reinvest' ? '红利再投' : '现金分红'
      setPending({
        body: { action: 'dividend_policy', code: selected.code, policy },
        spec: {
          title: `确认改为「${label}」`,
          fields: [
            { label: '基金', value: `${plainText(selected.name)}(${selected.code})` },
            { label: '范围', value: '该基金的全部持仓' },
            { label: '新方式', value: label },
          ],
          note: '只影响之后检测到的除息日；已入账的历史分红不重算、不回填。',
        },
      })
      return true
    }
    // delete
    setPending({
      body: { action: 'delete', id: selected.id },
      spec: {
        title: '确认删除持仓',
        fields: [
          { label: '持仓', value: base },
          { label: '金额', value: fmtMoney(selected.buy_amount) },
        ],
        warning: '删除会连带删掉这笔持仓的全部流水（两张表都备份到 data/backups/）。',
        note: '用于更正重复录入。删掉后可用「撤销」还原。',
        danger: true,
        confirmText: '确认删除',
      },
    })
    return true
  }

  function fail(msg: string): false {
    setError(msg)
    return false
  }

  async function doConfirm() {
    if (!pending) return
    setBusy(true)
    setError(null)
    try {
      const env = await endpoints.holdings(pending.body)
      setResult({ text: env.message || '已执行', commitId: env.commit_id ?? null })
      setPending(null)
      setAmount('')
      onDone()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function undo() {
    if (result?.commitId == null) return
    setBusy(true)
    setError(null)
    try {
      const env = await endpoints.rollback(result.commitId)
      setResult({ text: env.message || '已撤销', commitId: null })
      onDone()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="记一笔" note="· 写操作会先出确认卡">
      {/* 操作切换 */}
      <div className="mb-3 flex flex-wrap gap-1.5">
        {OPS.map((o) => (
          <button
            key={o.key}
            type="button"
            onClick={() => {
              setOp(o.key)
              setError(null)
              setResult(null)
            }}
            className={
              'rounded-full border px-2.5 py-1 text-caption transition-colors ' +
              (op === o.key
                ? 'border-accent/40 bg-accent/15 text-accent'
                : 'border-line text-fg-3 hover:text-fg')
            }
          >
            {o.label}
          </button>
        ))}
      </div>

      <div className="grid gap-2.5 sm:grid-cols-2">
        {op === 'buy' ? (
          <>
            <div>
              <label className={LABEL} htmlFor="led-code">基金代码</label>
              <input id="led-code" className={INPUT} value={code} placeholder="如 000001"
                     onChange={(e) => setCode(e.target.value)} />
            </div>
            <div>
              <label className={LABEL} htmlFor="led-amount">金额（元）</label>
              <input id="led-amount" className={INPUT} value={amount} inputMode="decimal"
                     onChange={(e) => setAmount(e.target.value)} />
            </div>
          </>
        ) : (
          <>
            <div>
              <label className={LABEL} htmlFor="led-holding">选择持仓</label>
              <select id="led-holding" className={INPUT} value={hid}
                      onChange={(e) => setHid(e.target.value)}>
                <option value="">— 请选择 —</option>
                {holdings.map((h) => (
                  <option key={h.id} value={String(h.id)}>
                    ID{h.id} {plainText(h.name)}({h.code})
                  </option>
                ))}
              </select>
            </div>
            {op === 'sell' || op === 'update' ? (
              <div>
                <label className={LABEL} htmlFor="led-amount2">
                  {op === 'sell' ? '卖出金额（元）' : '新金额（元，可留空）'}
                </label>
                <input id="led-amount2" className={INPUT} value={amount} inputMode="decimal"
                       onChange={(e) => setAmount(e.target.value)} />
              </div>
            ) : op === 'dividend_policy' ? (
              <div>
                <label className={LABEL} htmlFor="led-policy">新方式</label>
                <select id="led-policy" className={INPUT} value={policy}
                        onChange={(e) => setPolicy(e.target.value as 'reinvest' | 'cash')}>
                  <option value="reinvest">红利再投（默认）</option>
                  <option value="cash">现金分红</option>
                </select>
              </div>
            ) : null}
          </>
        )}

        {(op === 'buy' || op === 'sell' || op === 'update') && (
          <div>
            <label className={LABEL} htmlFor="led-date">
              {op === 'buy' ? '买入日期' : op === 'sell' ? '卖出日期' : '新日期（可留空）'}
            </label>
            <input id="led-date" type="date" className={INPUT} value={date}
                   onChange={(e) => setDate(e.target.value)} />
          </div>
        )}

        {op === 'buy' && (
          <label className="flex items-center gap-2 text-body-sm text-fg-3 sm:col-span-2">
            <input type="checkbox" checked={after} onChange={(e) => setAfter(e.target.checked)} />
            15:00 后提交（按下一交易日确认）
          </label>
        )}
      </div>

      {error && !pending && (
        <div className="mt-2.5 text-body-sm text-rise">{error}</div>
      )}

      {result && (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[var(--radius-md)] bg-inset px-3 py-2.5">
          <span className="text-body-sm text-fg-2">{result.text}</span>
          {result.commitId != null && (
            <button
              type="button"
              onClick={() => void undo()}
              disabled={busy}
              className="rounded-md border border-line-strong px-2.5 py-1 text-caption text-fg-2 hover:text-fg disabled:opacity-45"
            >
              撤销
            </button>
          )}
        </div>
      )}

      <div className="mt-3 flex items-center gap-2.5">
        <button
          type="button"
          onClick={() => prepare()}
          className="rounded-md bg-accent/15 px-3 py-1.5 text-body-sm font-semibold text-accent hover:bg-accent/25"
        >
          下一步（出确认卡）
        </button>
        <span className="text-caption text-fg-4">
          写入真实账本；成功后可用「撤销」还原（保留最近 20 次）
        </span>
      </div>

      {pending && (
        <ConfirmDialog
          spec={pending.spec}
          busy={busy}
          error={error}
          onConfirm={() => void doConfirm()}
          onCancel={() => {
            setPending(null)
            setError(null)
          }}
        />
      )}
    </Card>
  )
}
