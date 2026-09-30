import type { Explain, Rebalance } from '../api/types'
import { fmtMoney, plainText } from '../lib/format'
import { Card } from './Card'
import { DrillDown } from './DrillDown'

/** 指令动作徽标配色：减仓=提示黄、加仓=红（中国惯例里红是买入/走高）、其余中性 */
function actCls(action: string): string {
  if (action.includes('卖')) return 'text-warn border-[#3d3117]'
  if (action.includes('买')) return 'text-rise border-[#4a2620]'
  return 'text-fg-3 border-line-strong'
}

/**
 * 结论卡 —— 整屏最重要的东西放最上面。
 * 结论只来自「温度 + 硬约束」（不是预测，见评估格）；下方数据链路可展开下钻。
 */
export function VerdictCard({
  rebalance,
  explain,
  loading,
}: {
  rebalance: Rebalance | null
  explain: Explain | null
  loading: boolean
}) {
  if (!rebalance) {
    return (
      <Card lead title="今天" note="· 结论">
        <div className="text-sm text-fg-3">{loading ? '正在计算调仓结论…' : '调仓结论暂不可用'}</div>
      </Card>
    )
  }

  const verdict = plainText(rebalance.summary?.verdict) || '—'
  const detail = plainText(rebalance.summary?.detail)
  // 全「持有」时后端对每只持仓都给一条"仓位在合理范围内"——重复文案没有信息差，收成一行；
  // 只要有一条真操作，才逐条列出。
  const allHold =
    rebalance.instructions.length > 0 && rebalance.instructions.every((i) => i.action.includes('持有'))

  return (
    <Card lead title="今天" note="· 结论">
      <div className="mb-1.5 text-[22px] font-medium leading-snug">{verdict}</div>
      {detail && <div className="mb-4 text-[13.5px] text-fg-2">{detail}</div>}

      <div className="grid gap-2">
        {rebalance.instructions.length === 0 ? (
          <div className="rounded-md bg-inset px-3 py-2 text-[13.5px] text-fg-2">无需任何买卖操作。</div>
        ) : allHold ? (
          <div className="rounded-md bg-inset px-3 py-2 text-[13.5px] text-fg-2">
            {rebalance.instructions.length} 只持仓仓位均在合理范围内，无需操作。
          </div>
        ) : (
          rebalance.instructions.map((ins, i) => (
            <div key={`${ins.fund_code}-${i}`} className="flex items-start gap-2.5 rounded-md bg-inset px-3 py-2 text-[13.5px]">
              <span className={'shrink-0 rounded-full border px-2 py-px text-xs ' + actCls(ins.action)}>{ins.action}</span>
              <span className="text-fg-2">
                {ins.fund_name} · {fmtMoney(ins.amount)} · {plainText(ins.reason)}
              </span>
            </div>
          ))
        )}
      </div>

      {rebalance.scope_note && (
        <div className="mt-3 text-[11.5px] leading-relaxed text-fg-4">{plainText(rebalance.scope_note)}</div>
      )}

      {explain && <DrillDown explain={explain} />}
    </Card>
  )
}