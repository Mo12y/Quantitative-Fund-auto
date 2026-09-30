import { useState } from 'react'
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

const TH = 'pb-2 text-[11px] font-normal text-fg-3'

/**
 * 申万一级行业排名（31 个）—— 动量 + 趋势 + 风险。
 *
 * 默认只列前 12 名（一屏可读），展开看全部 —— 「研究」页里它是**市场层**的板块视图，
 * 与下方按**基金主题**分组的筛选池是两个不同分类体系（申万行业 vs 基金名称主题），
 * 故不互相联动，各自成卡（避免给出"点了行业就筛基金"的错误暗示）。
 *
 * 窄屏：把「近3月/近6月/波动」三列隐藏（`hidden md:table-cell`），只留 行业/近1月/评分，
 * 不靠横向滚动 —— 横向滚动会让页面出现内部溢出。
 */
export function SectorTable({ data }: { data: SectorsPayload }) {
  const [all, setAll] = useState(false)
  const rows = all ? data.sectors : data.sectors.slice(0, 12)

  return (
    <Card title="行业板块" note={`· 申万一级 ${data.sectors.length} 个 · 动量+趋势+风险`}>
      {data.momentum_leaders.length > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px]">
          <span className="text-fg-4">动量领先</span>
          {data.momentum_leaders.map((m) => (
            <span key={m.name} className="rounded-full border border-line bg-inset px-2 py-px">
              <span className="text-fg-2">{plainText(m.name)}</span>
              <span className="ml-1.5">
                <Signed v={m.ret_3m} />
              </span>
              <span className="ml-1 text-fg-4">近3月</span>
            </span>
          ))}
        </div>
      )}

      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={TH + ' text-left'}>行业</th>
            <th className={TH + ' text-right'}>近1月</th>
            <th className={TH + ' hidden text-right md:table-cell'}>近3月</th>
            <th className={TH + ' hidden text-right md:table-cell'}>近6月</th>
            <th className={TH + ' hidden text-right lg:table-cell'}>波动</th>
            <th className={TH + ' text-right'}>评分</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.name} className="border-t border-line">
              <td className="py-2 pr-3 text-[13px]">
                <span className="mr-1.5 text-[11px] text-fg-4">{s.rank}</span>
                <span className="text-fg">{plainText(s.name)}</span>
              </td>
              <td className="py-2 text-right text-[12.5px]">
                <Signed v={s.ret_1m} />
              </td>
              <td className="hidden py-2 text-right text-[12.5px] md:table-cell">
                <Signed v={s.ret_3m} />
              </td>
              <td className="hidden py-2 text-right text-[12.5px] md:table-cell">
                <Signed v={s.ret_6m} />
              </td>
              <td className="hidden py-2 text-right text-[12.5px] lg:table-cell">
                <Plain v={s.volatility} suffix="%" />
              </td>
              <td className="py-2 text-right text-[12.5px]">
                <Plain v={s.score} digits={2} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {data.sectors.length > 12 && (
        <button
          type="button"
          onClick={() => setAll(!all)}
          className="mt-3 rounded-md border border-line px-3 py-1 text-xs text-fg-3 hover:border-line-strong hover:text-fg-2"
        >
          {all ? '只看前 12 名' : `展开全部 ${data.sectors.length} 个`}
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
