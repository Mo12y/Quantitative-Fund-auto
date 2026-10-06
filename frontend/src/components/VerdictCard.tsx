import type { Rebalance } from '../api/types'
import { fmtMoney, plainText } from '../lib/format'
import { Card } from './Card'

/** 指令动作徽标配色：减仓=提示黄、加仓=涨红（中国惯例里红是买入/走高）、其余中性 */
function actCls(action: string): string {
  if (action.includes('卖')) return 'text-warn border-warn/30 bg-warn/10'
  if (action.includes('买')) return 'text-rise border-rise/30 bg-rise/10'
  return 'text-fg-3 border-line-strong bg-inset'
}

/**
 * 结论卡 —— 整屏最重要的东西放最上面。
 * 结论只来自「温度 + 硬约束」（不是预测，见评估格）；下方列出要动手的指令（若有）。
 *
 * ⚠️ 2026-10-06（瘦身第 3 条）：**不再内嵌数据链路下钻**（`DrillDown`）。
 *   审计内容按 DESIGN §5.1 默认折叠、且不得出现在第一屏 —— 它已整体搬到「设置」页
 *   （另在「研究」页保留一份）。本卡只剩「结论 + 指令」。
 */
export function VerdictCard({
  rebalance,
  loading,
  error,
}: {
  rebalance: Rebalance | null
  loading: boolean
  /**
   * 调仓端点失败时后端给的**中文原因**。
   *
   * ⚠️ 阶段 2 边界态实测（2026-09-30）：只让「今天」页处理 `overview.error` 是不够的 ——
   * `/api/rebalance` 单独失败时这里只剩一句「调仓结论暂不可用」，用户既不知道**为什么**，
   * 也不知道**该不该重试**。而本项目的既定约定是「失败要带后端给的中文原因，
   * 而不是『网络错误』这种无信息量的话」（见 `src/api/client.ts` 顶部）。
   */
  error?: string | null
}) {
  if (!rebalance) {
    return (
      <Card lead title="今天" note="· 结论">
        <div className="text-sm text-fg-3">
          {loading ? '正在计算调仓结论…' : error ? `调仓结论不可用：${error}` : '调仓结论暂不可用'}
        </div>
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
      <div className="mb-1.5 text-[24px] font-semibold leading-snug tracking-[-.01em]">{verdict}</div>
      {detail && <div className="mb-4 text-[13px] text-fg-2">{detail}</div>}

      <div className="grid gap-2">
        {rebalance.instructions.length === 0 ? (
          <div className="rounded-[var(--radius-md)] bg-inset px-3.5 py-2.5 text-[13px] text-fg-2">
            无需任何买卖操作。
          </div>
        ) : allHold ? (
          <div className="rounded-[var(--radius-md)] bg-inset px-3.5 py-2.5 text-[13px] text-fg-2">
            {rebalance.instructions.length} 只持仓仓位均在合理范围内，无需操作。
          </div>
        ) : (
          rebalance.instructions.map((ins, i) => (
            <div
              key={`${ins.fund_code}-${i}`}
              className="flex items-start gap-2.5 rounded-[var(--radius-md)] bg-inset px-3.5 py-2.5 text-[13px]"
            >
              <span className={'shrink-0 rounded-full border px-2 py-px text-[11px] ' + actCls(ins.action)}>
                {ins.action}
              </span>
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
    </Card>
  )
}