import { useState } from 'react'
import type { BoardPool, PoolFund } from '../api/types'
import { dirClass, dirOf, dirSymbol, fmtSignedPct, plainText } from '../lib/format'
import { Card } from './Card'
import { ConstraintNote } from './ConstraintNote'

const TH = 'field pb-2.5'

/**
 * 约束标注徽标。
 * ⚠️ 只标「落选」——「未评估」在池子里常占大多数（本例 40 只里 37 只），
 * 逐行标会把真信息淹没；未评估的数量进上方汇总行，不逐行重复。
 */
function DropMark({ s }: { s: PoolFund['constraint_status'] }) {
  if (s !== 'dropped') return null
  return (
    <span className="ml-1.5 whitespace-nowrap rounded-full border border-[#3d3117] px-1.5 text-[10px] text-warn">
      落选约束
    </span>
  )
}

function Num({ v, digits = 1 }: { v: number | null | undefined; digits?: number }) {
  const d = dirOf(v)
  return (
    <span className={'mono ' + dirClass(d)}>
      <span className="mr-0.5 text-[10px]">{dirSymbol(d)}</span>
      {fmtSignedPct(v, digits)}
    </span>
  )
}

/**
 * 筛选池总榜 —— **按板块分组**。
 *
 * 「研究」页把「筛选池」与「行业板块」合到一起，靠的就是这个已存在的端点
 * （`/api/funds/board`）：它把质量筛选池按**基金主题板块**分组，每板块取前 N 只。
 *
 * ⚠️ 与上方 `SectorTable` 的板块**不是同一套分类**：那边是申万一级行业（市场行情），
 * 这边是按基金名称关键词归的主题桶。故两者不联动 —— 不给出「点行业就筛基金」的错误暗示。
 * 也说清了池子的性质：**不推荐"买哪只"，只排除有坑的**。
 */
export function PoolBoard({ data }: { data: BoardPool }) {
  const [active, setActive] = useState('')
  const cur = data.boards.find((b) => b.board === active) ?? data.boards[0]
  if (!cur) return null

  return (
    <Card
      title="筛选池 · 按主题板块"
      note={`· 共 ${data.total_funds} 只进池 · 每板块前 ${data.size}`}
    >
      <div className="mb-2.5 text-[11.5px] leading-relaxed text-fg-4">
        性质：<b className="text-fg-3">不推荐"买哪只"，只排除有坑的</b>（存续/规模/费率/申购状态等硬检查）。
        板块按<b className="font-semibold text-fg-3">基金名称关键词</b>归类（近似口径），与上方申万行业不是同一套分类，故两卡不联动。
        质量池以稳健型为主，名称不含任何主题关键词的基金会归入「其他」，因此它通常是最大一桶
        —— 这是归类口径的结果，不是漏筛。
      </div>

      <ConstraintNote review={data.constraint_review} />

      {/* 板块切换：数量多时横向换行，不横向滚动（避免页面内部溢出） */}
      <div className="mb-3 flex flex-wrap gap-1">
        {data.boards.map((b) => {
          const on = b.board === cur.board
          return (
            <button
              key={b.board}
              type="button"
              onClick={() => setActive(b.board)}
              aria-current={on ? 'true' : undefined}
              className={
                'rounded-md border px-2.5 py-1 text-xs transition-colors ' +
                (on
                  ? 'border-accent-dim bg-inset text-fg'
                  : 'border-line text-fg-3 hover:border-line-strong hover:text-fg-2')
              }
            >
              {plainText(b.board)}
              <span className="ml-1.5 text-fg-4">{b.total}</span>
            </button>
          )
        })}
      </div>

      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={TH + ' text-left'}>基金</th>
            <th className={TH + ' hidden text-left md:table-cell'}>类型</th>
            <th className={TH + ' text-left'}>风险</th>
            <th className={TH + ' hidden text-right sm:table-cell'}>夏普</th>
            <th className={TH + ' text-right'}>近3月</th>
            <th className={TH + ' hidden text-right lg:table-cell'}>费率</th>
          </tr>
        </thead>
        <tbody>
          {cur.funds.map((f) => (
            <tr key={f.code} className="row-hover border-t border-line">
              <td className="py-2 pr-3 text-[13px]">
                <span className="text-fg">{plainText(f.name)}</span>
                <span className="mono ml-1.5 text-[11.5px] text-fg-4">{f.code}</span>
                <DropMark s={f.constraint_status} />
              </td>
              <td className="hidden py-2 pr-3 text-[12px] text-fg-3 md:table-cell">{plainText(f.type)}</td>
              <td className="py-2 text-[12px] text-fg-2">{plainText(f.risk)}</td>
              <td className="hidden py-2 text-right text-[12.5px] text-fg-2 sm:table-cell">
                <span className="mono">{f.sharpe == null ? '—' : f.sharpe.toFixed(2)}</span>
              </td>
              <td className="py-2 text-right text-[12.5px]">
                <Num v={f.momentum_3m} />
              </td>
              <td className="mono hidden py-2 text-right text-[12.5px] text-fg-3 lg:table-cell">
                {/* ⚠️ fee 缺失是 null，不是 0 —— 显示 0 会让人以为"这只零费率" */}
                {f.fee == null ? '—' : `${f.fee.toFixed(2)}%`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}
