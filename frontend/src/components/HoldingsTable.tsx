import { useMemo, useState } from 'react'
import type { Holding } from '../api/types'
import { dirClass, dirOf, dirSymbol, fmtMoney, fmtSignedMoney, fmtSignedPct } from '../lib/format'
import { Card } from './Card'

type Filter = 'all' | 'win' | 'loss'
type SortKey = 'mv' | 'ret'

interface FundRow {
  code: string
  name: string
  value: number
  pnl: number
  pct: number
}

/**
 * 批次行（每笔定投一条）→ **基金级**聚合。
 * 后端 holdings 按批次返回（一只基金可能有 20+ 个小额定投批次），
 * 直接逐行显示会把"一只基金"读成"多只"。
 */
function aggregate(holdings: Holding[]): FundRow[] {
  const map = new Map<string, FundRow>()
  for (const h of holdings) {
    if (h.status !== 'holding') continue
    const cur = map.get(h.code) ?? { code: h.code, name: h.name || h.code, value: 0, pnl: 0, pct: 0 }
    cur.value += h.current_value || 0
    cur.pnl += h.pnl || 0
    if (h.name) cur.name = h.name
    map.set(h.code, cur)
  }
  return [...map.values()].map((r) => {
    const cost = r.value - r.pnl
    return { ...r, pct: cost > 0 ? (r.pnl / cost) * 100 : 0 }
  })
}

function Seg({
  options,
  value,
  onChange,
}: {
  options: { v: string; label: string }[]
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className="flex gap-0.5 rounded-md bg-inset p-0.5">
      {options.map((o) => (
        <button
          key={o.v}
          type="button"
          onClick={() => onChange(o.v)}
          className={
            'rounded-sm px-2.5 py-1 text-xs transition-colors ' +
            (o.v === value ? 'bg-card text-fg' : 'text-fg-3 hover:text-fg-2')
          }
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** 持仓表：筛选（全部/盈利/亏损）+ 排序（市值/收益率，重复点击换方向） */
export function HoldingsTable({ holdings }: { holdings: Holding[] }) {
  const [filter, setFilter] = useState<Filter>('all')
  const [sortKey, setSortKey] = useState<SortKey>('mv')
  const [asc, setAsc] = useState(false)

  const rows = useMemo(() => aggregate(holdings), [holdings])
  const shown = useMemo(() => {
    const f = rows.filter((r) => (filter === 'all' ? true : filter === 'win' ? r.pct > 0 : r.pct < 0))
    f.sort((a, b) => {
      const d = sortKey === 'mv' ? a.value - b.value : a.pct - b.pct
      return asc ? d : -d
    })
    return f
  }, [rows, filter, sortKey, asc])

  const arrow = (k: SortKey) => (sortKey === k ? (asc ? ' ↑' : ' ↓') : '')

  return (
    <Card title="持仓" note={`· 显示 ${shown.length} / 共 ${rows.length} 只`}>
      <div className="mb-2.5 flex flex-wrap justify-between gap-2.5">
        <Seg
          options={[
            { v: 'all', label: '全部' },
            { v: 'win', label: '盈利' },
            { v: 'loss', label: '亏损' },
          ]}
          value={filter}
          onChange={(v) => setFilter(v as Filter)}
        />
        <Seg
          options={[
            { v: 'mv', label: `按市值${arrow('mv')}` },
            { v: 'ret', label: `按收益率${arrow('ret')}` },
          ]}
          value={sortKey}
          onChange={(v) => {
            if (v === sortKey) setAsc(!asc)
            else {
              setSortKey(v as SortKey)
              setAsc(false)
            }
          }}
        />
      </div>

      {/* ⚠️ 空态必须说话。原实现直接渲染 <table>：`shown` 为空时页面上只剩表头
          （基金/市值/收益/收益率）而表体全空，标题却写着「显示 0 / 共 0 只」——
          读起来像界面坏了，而不是「确实没有」。两种空态要分开讲：
            ① 真的没有在持基金（rows 为空）；
            ② 有持仓、但被「盈利/亏损」筛选滤空了（这是用户自己能切回来的）。
          2026-09-30 阶段 2 边界态实测（Playwright 注入 holdings=[] 复现）后补。 */}
      {shown.length === 0 ? (
        <div className="rounded-md bg-inset px-3 py-6 text-center text-[12.5px] leading-relaxed text-fg-3">
          {rows.length === 0
            ? '当前没有在持基金。'
            : `没有符合「${filter === 'win' ? '盈利' : '亏损'}」的持仓 —— 切回「全部」可见 ${rows.length} 只。`}
        </div>
      ) : (
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="pb-2 text-left text-[11px] font-normal text-fg-3">基金</th>
              <th className="pb-2 text-right text-[11px] font-normal text-fg-3">市值</th>
              <th className="pb-2 text-right text-[11px] font-normal text-fg-3">收益</th>
              <th className="pb-2 text-right text-[11px] font-normal text-fg-3">收益率</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const d = dirOf(r.pct)
              return (
                <tr key={r.code} className="border-t border-line">
                  <td className="py-2.5 pr-3 text-[13.5px]">
                    <span className="text-fg">{r.name}</span>
                    <span className="mono ml-1.5 text-[11.5px] text-fg-4">{r.code}</span>
                  </td>
                  <td className="py-2.5 text-right text-[13.5px] text-fg-2">{fmtMoney(r.value)}</td>
                  <td className={'py-2.5 text-right text-[13.5px] ' + dirClass(d)}>{fmtSignedMoney(r.pnl)}</td>
                  <td className={'py-2.5 text-right text-[13.5px] ' + dirClass(d)}>
                    <span className="mr-0.5 text-[10px]">{dirSymbol(d)}</span>
                    {fmtSignedPct(r.pct)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Card>
  )
}