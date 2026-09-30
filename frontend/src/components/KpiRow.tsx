import type { ReactNode } from 'react'
import type { Rebalance, Stats } from '../api/types'
import { dirClass, dirOf, dirSymbol, fmtMoney, fmtPct, fmtSignedMoney, fmtSignedPct } from '../lib/format'

interface KpiItem {
  label: string
  value: string
  cls: string
  sym?: string
  /** 副行。允许 ReactNode，因为「权益占比」需要分两行说清目标 + 差额 + 基数 */
  sub: ReactNode
}

/**
 * 四格 KPI —— 只放四个，其余下沉到明细卡。
 *
 * 视觉：**字段名弱、数字强**（金融数据的层级惯例）。数字 26/30px 半粗 + 负字距，
 * 副行用 11px 弱色。四格之间用 1px 分隔线（`gap-px + bg-line` 的经典做法）。
 *
 * 「权益占比」用**温度适用的 A 股口径**（rebalance.current_equity_pct），
 * 与 SSOT 的资产类别占比（含 QDII/黄金）不是一回事，所以标签写明口径。
 *
 * ⚠️ 权益格的金额口径（2026-09-30 对账后定案）：
 *   百分比的基数是 `total_capital = 持仓市值 + 现金弹药`（rebalance_advisor.py:83），
 *   **不是**总资产、不是累计投入、更不是计划总资金。所以差额金额一律取
 *   后端给的 `gap_amount`（与百分比同基数），**绝不自己乘**。
 *   旧前端就是自己乘的（app.js:650 用 portfolio.total_invested），
 *   结果同屏的「36.9%」与「权益 ≈ ¥314.72」互相矛盾（按正确基数应为 ¥418.63，差 33%）。
 */
export function KpiRow({
  stats,
  rebalance,
  holdingFunds,
}: {
  stats: Stats
  rebalance: Rebalance | null
  /** 在持**基金**数。注意 `stats.holding_count` 是**批次**口径（29 批次 ≠ 7 只基金），不能直接用 */
  holdingFunds: number
}) {
  const pnlDir = dirOf(stats.total_pnl)
  const rzDir = dirOf(stats.realized_pnl)

  const rb = rebalance
  const targetPct = rb?.target_equity_pct ?? null
  const gapPct = rb?.gap_pct ?? null
  const gapAmt = rb?.gap_amount ?? null
  const band = rb?.rebalance_pp ?? null
  const base = rb?.total_capital ?? null
  const targetSrc = rb?.target_source === 'user_profile' ? '我的设定' : '温度模型'

  const equitySub: ReactNode = rb ? (
    <>
      <div>
        <span className="text-fg-4">目标</span>{' '}
        <span className="mono text-fg-2">{targetPct != null ? fmtPct(targetPct) : '—'}</span>
        <span className="text-fg-4">（{targetSrc}）</span>
      </div>
      {gapPct != null && gapAmt != null && (
        <div>
          {rb.need_rebalance ? (
            <>
              {gapPct > 0 ? '待补 ' : '待减 '}
              <b className={gapPct > 0 ? 'text-rise' : 'text-fall'}>
                {fmtMoney(Math.abs(gapAmt))}
              </b>
              {`（${fmtSignedPct(gapPct)}）`}
            </>
          ) : (
            <>差 {fmtSignedPct(gapPct)} · 在容忍带内{band != null ? `（±${band}pp）` : ''}</>
          )}
        </div>
      )}
      {base != null && <div className="text-fg-4">基数 {fmtMoney(base)}（持仓市值+现金）</div>}
    </>
  ) : (
    '—'
  )

  const items: KpiItem[] = [
    {
      label: '总资产',
      value: fmtMoney(stats.total_assets),
      cls: 'text-fg',
      sub: `在持 ${holdingFunds} 只${stats.pending_amount ? ` · 在途 ${fmtMoney(stats.pending_amount)}` : ''}`,
    },
    {
      label: '累计收益（未实现）',
      value: fmtSignedMoney(stats.total_pnl),
      cls: dirClass(pnlDir),
      sym: dirSymbol(pnlDir),
      sub: fmtSignedPct(stats.total_return_pct),
    },
    {
      label: '已实现',
      value: fmtSignedMoney(stats.realized_pnl),
      cls: dirClass(rzDir),
      sym: dirSymbol(rzDir),
      sub: `${stats.realized_count} 笔 · 含赎回费 ${fmtMoney(stats.realized_fee)}`,
    },
    {
      label: '权益占比（A 股）',
      value: rb ? fmtPct(rb.current_equity_pct) : '—',
      cls: 'text-fg',
      sub: equitySub,
    },
  ]

  return (
    <div className="rise-in mb-4 grid grid-cols-2 gap-px overflow-hidden rounded-[var(--radius-lg)] border border-line bg-line shadow-[inset_0_1px_0_rgba(255,255,255,.04),0_18px_40px_-28px_rgba(0,0,0,.95)] sm:grid-cols-4">
      {items.map((it) => (
        <div key={it.label} className="bg-card px-4 py-4">
          <div className="field mb-1.5">{it.label}</div>
          <div className={'num text-[26px] font-semibold leading-none tracking-[-.02em] sm:text-[30px] ' + it.cls}>
            {it.sym && <span className="mr-1 align-top text-[12px] opacity-90">{it.sym}</span>}
            {it.value}
          </div>
          <div className="mt-2 space-y-0.5 text-[11px] leading-relaxed text-fg-3">{it.sub}</div>
        </div>
      ))}
    </div>
  )
}
