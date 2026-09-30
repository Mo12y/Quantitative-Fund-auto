import type { Rebalance, Stats } from '../api/types'
import { dirClass, dirOf, dirSymbol, fmtMoney, fmtPct, fmtSignedMoney, fmtSignedPct } from '../lib/format'

interface KpiItem {
  label: string
  value: string
  cls: string
  sym?: string
  sub: string
}

/**
 * 四格 KPI —— 只放四个，其余下沉到明细卡。
 * 「权益占比」用**温度适用的 A 股口径**（rebalance.current_equity_pct），
 * 与 SSOT 的资产类别占比（含 QDII/黄金）不是一回事，所以标签写明口径。
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
      value: rebalance ? fmtPct(rebalance.current_equity_pct) : '—',
      cls: 'text-fg',
      sub: rebalance?.target_equity_pct != null ? `目标 ${fmtPct(rebalance.target_equity_pct)}` : '目标 —',
    },
  ]

  return (
    <div className="mb-3.5 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-4">
      {items.map((it) => (
        <div key={it.label} className="bg-card px-4 py-3.5">
          <div className="mb-1 text-[11.5px] text-fg-3">{it.label}</div>
          <div className={'text-[21px] font-medium tracking-[-.2px] ' + it.cls}>
            {it.sym && <span className="mr-0.5 text-[10px]">{it.sym}</span>}
            {it.value}
          </div>
          <div className="mt-0.5 text-[11.5px] text-fg-3">{it.sub}</div>
        </div>
      ))}
    </div>
  )
}