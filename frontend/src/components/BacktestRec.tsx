import { useState } from 'react'
import { endpoints } from '../api/endpoints'
import { Card } from './Card'
import { Skeleton } from './Skeleton'
import { plainText } from '../lib/format'
import { useApi } from '../lib/useApi'

/**
 * 历史回测验证（`/api/recommend`）—— 旧前端 `loadRec()` 的等价物。
 *
 * ⚠️ 命名与文案是**硬约束**（后端 `api_recommend` docstring 原文）：
 *  · 这个端点**不是"推荐"**，是**历史回测验证**；
 *  · 结论是**样本内**的 —— 用已实现的前向收益反筛"赢家"存在同义反复，
 *    **不构成选基能力证据**（批次 4.3 的滚动样本外验证：超额中位仅 +0.11pp、逐窗胜率 51.0%）；
 *  · 主推荐是温度驱动的实时筛选（即本页上方的筛选池）。
 *
 * 所以：标题就写「辅助参考，非推荐」；正文**引用信封层的 `purpose` / `methodology_note`**
 * （后端原文），不自己编 —— 免得两边说法漂移。
 *
 * ⚠️ 加载要 **15~20 秒**：默认折叠，**展开才请求**（避免一进「研究」页就白等），
 * 展开后必须给骨架 + 明确文案，不能白屏。
 */
export function BacktestRec({ className = '' }: { className?: string }) {
  const [open, setOpen] = useState(false)
  return (
    <Card className={className} title="历史回测验证" note="· 辅助参考，非推荐">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <span className="text-[12px] leading-relaxed text-fg-4">
          看「过去哪些基金被反复选中且真的赚了钱」，属样本内统计 —— 不是选基能力证据。
        </span>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="shrink-0 rounded-md border border-line-strong px-2.5 py-1 text-[11.5px] text-fg-2 hover:text-fg"
        >
          {open ? '收起' : '展开（首次约 15~20 秒）'}
        </button>
      </div>
      {open && <RecInner />}
    </Card>
  )
}

function RecInner() {
  const st = useApi(endpoints.recommend)
  const d = st.data

  if (st.error) {
    return (
      <div className="mt-3 border-t border-line pt-3 text-[12.5px] text-fg-2">
        历史回测加载失败 · {plainText(st.error)}
      </div>
    )
  }
  if (!d) {
    return (
      <div className="mt-3 border-t border-line pt-3">
        <div className="mb-2 text-[12px] text-fg-3">正在跑历史回测（首次约 15~20 秒）…</div>
        <Skeleton lines={5} />
      </div>
    )
  }

  const picks = d.current_picks ?? []
  const note = st.envelope?.methodology_note || st.envelope?.purpose

  return (
    <div className="mt-3 border-t border-line pt-3">
      <div className="rounded-[var(--radius-md)] border border-line bg-inset px-3 py-2.5 text-[11.5px] leading-relaxed text-warn">
        {plainText(note) ||
          '样本内结果，不构成选基能力证据；主推荐请看本页上方的温度驱动筛选池。'}
      </div>

      <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[12px] text-fg-3">
        <span>
          通过约束的候选 <span className="num text-fg-2">{picks.length}</span> 只
        </span>
        {d.stats?.date_range ? (
          <span className="text-[11px] text-fg-4">回测区间 {plainText(d.stats.date_range)}</span>
        ) : null}
      </div>

      {picks.length === 0 ? (
        <div className="mt-2 text-[12.5px] text-fg-2">
          没有候选通过全部约束 —— 原因见落选 / 未评估明细
        </div>
      ) : (
        <div className="mt-2 flex flex-col">
          {picks.map((p) => {
            const P = p.percentiles ?? {}
            const ok = !p.insufficient_data && P.sharpe != null
            const comp =
              P.sharpe != null && P.max_drawdown_1y != null
                ? Math.round(0.6 * P.sharpe + 0.4 * P.max_drawdown_1y)
                : null
            return (
              <div
                key={p.code}
                className="row-hover flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-line py-1.5 first:border-t-0"
              >
                <span className="mono text-[11.5px] text-fg-4">{p.code}</span>
                <span className="min-w-0 flex-1 truncate text-[13px] text-fg">
                  {plainText(p.name) || p.code}
                </span>
                <span className="num text-[11.5px] text-fg-4">
                  {ok && comp != null
                    ? `同类 P${comp} · ${plainText(p.group) || '—'}${
                        p.group_n ? ` · ${p.group_n.toLocaleString()} 只对照` : ''
                      }`
                    : `同类 P— · ${plainText(p.reason) || '数据不足'}`}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
