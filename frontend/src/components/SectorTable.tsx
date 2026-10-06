import { Fragment, useState } from 'react'
import type { SectorsPayload } from '../api/types'
import { dirClass, dirOf, dirSymbol, fmtSignedPct, plainText } from '../lib/format'
import { Card } from './Card'

/** 涨跌幅单元格：**带 ▲▼ 符号**（色盲硬约束 —— 涨跌不可只靠颜色区分） */
function Signed({ v, digits = 1 }: { v: number | null | undefined; digits?: number }) {
  const d = dirOf(v)
  return (
    <span className={'mono ' + dirClass(d)}>
      <span className="mr-0.5 text-[10px]">{dirSymbol(d)}</span>
      {fmtSignedPct(v, digits)}
    </span>
  )
}

/** 无方向语义的数值（波动率/趋势比/评分）：中性灰，不套涨跌色 */
function Plain({ v, digits = 1, suffix = '' }: { v: number | null | undefined; digits?: number; suffix?: string }) {
  if (v == null || Number.isNaN(v)) return <span className="text-fg-4">—</span>
  return (
    <span className="mono text-fg-2">
      {v.toFixed(digits)}
      {suffix}
    </span>
  )
}

const TH = 'field pb-2.5'
/** 默认列出多少个行业（一屏可读），其余用按钮展开 —— 见下方注释 */
const TOP_N = 8

/**
 * 申万一级行业排名（31 个）—— 动量 + 趋势 + 风险。
 *
 * ⚠️ 2026-10-06（瘦身·研究页）：**可视列由 6 收到 3**（行业 / 近1月 / 评分），
 * 「近3月 / 近6月 / 波动」进**逐行展开**（行业名即开关）；默认行数 12 → 8。
 * 理由：这一屏要回答"现在哪个行业在走强"，**近1月 + 综合评分**就够了；
 * 近3月/近6月是"这只还有没有持续性"的追问，波动是风险指标 —— 都是"我要看细节"。
 * 实测行业卡数字读数 88 → 33（占研究页的 68% → 45%）。
 *
 * ⚠️ 表头**保留**：一行有三个数字（近1月/评分），去掉表头就分不清哪个是哪个
 * （数字不带标签就不是信息，DESIGN §5.5）。
 *
 * 窄屏：把「近3月/近6月/波动」三列隐藏（`hidden md:table-cell`），只留 行业/近1月/评分，
 * 不靠横向滚动 —— 横向滚动会让页面出现内部溢出。
 */
export function SectorTable({ data, className = '' }: { data: SectorsPayload; className?: string }) {
  const [all, setAll] = useState(false)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const rows = all ? data.sectors : data.sectors.slice(0, TOP_N)
  const toggle = (name: string) => setOpen((m) => ({ ...m, [name]: !m[name] }))

  return (
    <Card className={className} title="行业板块" note={`· 申万一级 ${data.sectors.length} 个 · 动量+趋势+风险`}>
      {data.momentum_leaders.length > 0 && (
        /* ⚠️ 「近3月」只在标签里说一次，不再每枚 chip 重复挂一遍
           （4 枚 chip 就是 4 个重复的数字读数，纯噪声）。 */
        <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px]">
          <span className="text-fg-4">动量领先 · 近3月</span>
          {data.momentum_leaders.map((m) => (
            <span key={m.name} className="rounded-full border border-line bg-inset px-2 py-px">
              <span className="text-fg-2">{plainText(m.name)}</span>
              <span className="ml-1.5">
                <Signed v={m.ret_3m} />
              </span>
            </span>
          ))}
        </div>
      )}

      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={TH + ' text-left'}>行业</th>
            <th className={TH + ' text-right'}>近1月</th>
            <th className={TH + ' text-right'}>评分</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => {
            const on = !!open[s.name]
            return (
              /* Fragment：展开行是"行内的第二条 tr"，不额外占一列 */
              <Fragment key={s.name}>
                <tr className="row-hover border-t border-line">
                  <td className="py-2 pr-3 text-[13px]">
                    <button
                      type="button"
                      onClick={() => toggle(s.name)}
                      aria-expanded={on}
                      className="text-left text-fg transition-colors hover:text-accent"
                    >
                      <span className="mr-1 text-[10px] text-fg-4">{on ? '⌃' : '⌄'}</span>
                      <span className="mr-1.5 text-[11px] text-fg-4">{s.rank}</span>
                      {plainText(s.name)}
                    </button>
                  </td>
                  <td className="num py-2 text-right text-[12.5px]">
                    <Signed v={s.ret_1m} />
                  </td>
                  <td className="num py-2 text-right text-[12.5px]">
                    <Plain v={s.score} digits={2} />
                  </td>
                </tr>
                {on && (
                  <tr>
                    <td colSpan={3} className="pb-2.5 pl-1 text-[11.5px] leading-relaxed text-fg-3">
                      <span className="mr-4">
                        近3月 <Signed v={s.ret_3m} />
                      </span>
                      <span className="mr-4">
                        近6月 <Signed v={s.ret_6m} />
                      </span>
                      <span className="mr-4">
                        波动 <Plain v={s.volatility} suffix="%" />
                      </span>
                      <span>
                        6月最大回撤 <Plain v={s.max_dd_6m} suffix="%" />
                      </span>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>

      {data.sectors.length > TOP_N && (
        <button
          type="button"
          onClick={() => setAll(!all)}
          className="mt-3 rounded-md border border-line px-3 py-1 text-xs text-fg-3 hover:border-line-strong hover:text-fg-2"
        >
          {all ? `只看前 ${TOP_N} 名` : `展开全部 ${data.sectors.length} 个`}
        </button>
      )}

      {data.cached_at && (
        <div className="mt-2 text-[11px] text-fg-4">
          板块数据缓存于 {data.cached_at}（刷新走 CLI，见「设置」页运维说明）
        </div>
      )}
    </Card>
  )
}
