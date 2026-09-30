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
 * 四格 KPI —— **统计条**（不是独立卡片）。
 *
 * ⚠️ 形态说明（2026-09-30 版式调整）：它刻意用**更轻**的表面（内嵌色 + 细边、无投影），
 * 因为它在「今天」页里与结论卡**同属一个概览区**（父级只给 10px 间距）。
 * 如果它也做成一张等重的卡，读者会把"结论"和"结论的支撑数字"看成两个并列的东西 ——
 * 这正是用户反馈的"卡片与卡片之间的关系有问题"。
 *
 * 视觉：**字段名弱、数字强**（金融数据的层级惯例）。数字 26/30px 半粗 + 负字距。
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

  /**
   * 分格线：手写而不是用 `gap-px + bg-line` 的老技巧 ——
   * 那个技巧要求容器铺一层"线色"底，于是格子必须**不透明**才不会被染白，
   * 而这里想让整块跟卡片一样是磨砂玻璃（半透明 + blur）。
   * 移动端 2 列 / sm 起 4 列，分格线规则不同，故按索引算。
   */
  const sep = (i: number): string => {
    const p = ['border-line']
    if (i % 2 === 1) p.push('border-l') // 移动端：每行第 2 格画左边线
    if (i >= 2) p.push('border-t') // 移动端：第二行起画上边线
    if (i > 0) p.push('sm:border-l') // 桌面端：除首格外都画左边线
    p.push('sm:border-t-0') // 桌面端单行，不要上边线
    return p.join(' ')
  }

  return (
    <div className="rise-in grid grid-cols-2 overflow-hidden rounded-[var(--radius-lg)] border border-line bg-inset/35 backdrop-blur-md sm:grid-cols-4">
      {items.map((it, i) => (
        <div key={it.label} className={'tint-aurora px-4 py-4 ' + sep(i)}>
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
