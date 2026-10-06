import type { Rebalance } from '../api/types'
import { fmtMoney, fmtPct, fmtSignedPct, plainText } from '../lib/format'
import { Card } from './Card'

/** 指令动作徽标配色：减仓=提示黄、加仓=涨红（中国惯例里红是买入/走高）、其余中性 */
function actCls(action: string): string {
  if (action.includes('卖')) return 'text-warn border-warn/30 bg-warn/10'
  if (action.includes('买')) return 'text-rise border-rise/30 bg-rise/10'
  return 'text-fg-3 border-line-strong bg-inset'
}

/**
 * 结论的**三态**（DESIGN §5.1）：结论必须落到「要动手 / 不用动手 / 待数据」之一，
 * 不能只描述现象。
 *
 * ⚠️ 判定用**指令列表**而不是后端的 `need_rebalance` —— 两者会不一致，且不一致时
 * 用户看的是指令：实测本机 `need_rebalance=true`（权益占比 33.9% 距目标 58.8% 有缺口）
 * 但同时 `instructions=[]`（池子里没有"稳健且温度适用"的买入候选）→ 结论文案写着
 * "无需任何买卖操作"。此时若按 `need_rebalance` 打「要动手」徽标，就会自相矛盾。
 * "今天要不要动手"取决于**今天有没有可执行的动作**，缺口本身由 `detail` 文案交代。
 * 端点没回来（加载中 / 失败）一律是「待数据」，**不许**渲染成「不用动手」。
 */
type VerdictState = 'act' | 'hold' | 'pending'

const STATE: Record<VerdictState, { label: string; cls: string }> = {
  act: { label: '要动手', cls: 'text-warn border-warn/35 bg-warn/10' },
  hold: { label: '不用动手', cls: 'text-accent border-accent-dim bg-accent/10' },
  pending: { label: '待数据', cls: 'text-fg-3 border-line-strong bg-inset' },
}

/** 有没有**非「持有」**的指令（全持有 / 空列表都算"不用动手"）。 */
function hasAction(rb: Rebalance | null): boolean {
  return !!rb && rb.instructions.some((i) => !i.action.includes('持有'))
}

/**
 * 结论卡 —— 回答"今天要不要动手"。
 *
 * ⚠️ 2026-10-06（瘦身第 2、3 条）两处改动：
 *  1. 顶部加**三态徽标**（DESIGN §5.1 要求结论落态，不能只描述现象）；
 *  2. **不再内嵌数据链路下钻**（`DrillDown`）—— 审计按 §5.1 搬到「设置」并默认折叠，
 *     此前它默认展开在结论卡里，把真正该看的结论挤了下去。
 *  3. 卡尾加**支撑行**（权益占比 当前 / 目标 / 差额，共 3 个数字），承接原「今天」页
 *     四格 KPI 里唯一与结论直接相关的那个；另外三格各有去处（总资产→大数字，
 *     累计收益→大数字的参照，已实现→「持仓」页的持仓卡脚注）。
 *
 * `lead` 供「持仓」页沿用（那里它是首块，需要强调）；「今天」页由大数字承担强调。
 */
export function VerdictCard({
  rebalance,
  loading,
  error,
  lead = false,
  support = false,
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
  /** 作为页面首块时强调（左强调条 + 极光）。 */
  lead?: boolean
  /**
   * 是否显示**支撑行**（权益占比 当前 / 目标 / 差额）。
   *
   * ⚠️ 只有「今天」页传 true。它是"今天要不要动手"这个结论的依据，属首页首屏的
   * 关键数字（DESIGN §5.1）。「持仓」页不传 —— 那里同样是这张卡，但权益目标已经在
   * 「设置」的「用户画像与生效约束」里讲清楚了，重复一遍只会抬高该页密度
   * （2026-10-06 实测：多这一行会让「持仓」越过本页的密度基线上限）。
   */
  support?: boolean
}) {
  const state: VerdictState = !rebalance ? 'pending' : hasAction(rebalance) ? 'act' : 'hold'
  const badge = STATE[state]

  if (!rebalance) {
    return (
      <Card lead={lead} title="今天" note="· 结论">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
          <span className={'shrink-0 rounded-full border px-2.5 py-0.5 text-[12px] ' + badge.cls}>
            {badge.label}
          </span>
          <span className="text-[13.5px] text-fg-3">
            {loading ? '正在计算调仓结论…' : error ? `调仓结论不可用：${error}` : '调仓结论暂不可用'}
          </span>
        </div>
      </Card>
    )
  }

  const rb = rebalance
  const verdict = plainText(rb.summary?.verdict) || '—'
  const detail = plainText(rb.summary?.detail)
  // 全「持有」时后端对每只持仓都给一条"仓位在合理范围内"——重复文案没有信息差，收成一行；
  // 只要有一条真操作，才逐条列出。
  const allHold = rb.instructions.length > 0 && rb.instructions.every((i) => i.action.includes('持有'))
  const gapUp = (rb.gap_pct ?? 0) > 0

  return (
    <Card lead={lead} title="今天" note="· 结论">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
        <span className={'shrink-0 rounded-full border px-2.5 py-0.5 text-[12px] ' + badge.cls}>
          {badge.label}
        </span>
        <span className="text-[17px] font-medium leading-snug tracking-[-.01em]">{verdict}</span>
      </div>
      {detail && <div className="mt-1.5 text-[12.5px] text-fg-2">{detail}</div>}

      <div className="mt-3 grid gap-2">
        {rb.instructions.length === 0 ? (
          <div className="rounded-[var(--radius-md)] bg-inset px-3.5 py-2.5 text-[13px] text-fg-2">
            无需任何买卖操作。
          </div>
        ) : allHold ? (
          <div className="rounded-[var(--radius-md)] bg-inset px-3.5 py-2.5 text-[13px] text-fg-2">
            {rb.instructions.length} 只持仓仓位均在合理范围内，无需操作。
          </div>
        ) : (
          rb.instructions.map((ins, i) => (
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

      {/* 支撑数字（≤3）：权益占比 当前 / 目标 / 差额 —— 结论的依据，不是并列的第二组结论。
          ⚠️ 口径：百分比基数是 `total_capital = 持仓市值 + 现金弹药`（后端给），
          **不是**总资产、不是累计投入；所以这里只显示后端给的 `gap_pct`，不自己算。
          ⚠️ 只在传 `support` 的页面显示（见该 prop 的注释）。 */}
      {support && rb.target_equity_pct != null && (
        <div className="mt-3 flex flex-wrap items-baseline gap-x-4 gap-y-1 border-t border-line pt-2 text-[11.5px] text-fg-4">
          <span>
            权益占比 <b className="num font-medium text-fg-2">{fmtPct(rb.current_equity_pct)}</b>
          </span>
          <span>
            目标 <span className="num text-fg-2">{fmtPct(rb.target_equity_pct)}</span>
            <span className="ml-1">（{rb.target_source === 'user_profile' ? '我的设定' : '温度模型'}）</span>
          </span>
          {rb.gap_pct != null && (
            /* ⚠️ 缺口用**文字**表达（待补 / 待减 / 差），**不给涨跌色、不加 ▲▼** ——
               它是"仓位离目标多远"，不是"赚了还是亏了"；套上涨红跌绿会被读成收益。 */
            <span>
              {rb.need_rebalance ? (gapUp ? '待补' : '待减') : '偏离'}
              <b className="num ml-1 font-medium text-fg-2">{fmtSignedPct(rb.gap_pct)}</b>
            </span>
          )}
        </div>
      )}

      {rb.scope_note && (
        <div className="mt-3 text-[11.5px] leading-relaxed text-fg-4">{plainText(rb.scope_note)}</div>
      )}
    </Card>
  )
}
