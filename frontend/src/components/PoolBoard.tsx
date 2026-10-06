import { Fragment, useState } from 'react'
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
    <span className="ml-1.5 whitespace-nowrap rounded-full border border-[#3d3117] px-1.5 text-micro text-warn">
      落选约束
    </span>
  )
}

function Num({ v, digits = 1 }: { v: number | null | undefined; digits?: number }) {
  const d = dirOf(v)
  return (
    <span className={'mono ' + dirClass(d)}>
      <span className="mr-0.5 text-micro">{dirSymbol(d)}</span>
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
 *
 * ⚠️ 2026-10-06（瘦身第 6 条）：**可视列由 6 列收到 4 列**（代码 / 名称 / 夏普 / 近3月），
 * 「类型 / 风险 / 费率」收进**逐行展开**。理由：这一屏要回答的是"池子里哪几只有像样的
 * 动量、夏普不为负"，而不是"这只基金的完整档案"；后者是"我要看细节"时才需要的
 * （DESIGN §5.3 渐进披露）。涨跌色规则不变（涨红跌绿 + ▲▼）。
 *
 * ⚠️ 表头**保留**（不是 DESIGN §5.1.1 第 8 条的"列表不带表头"）：那条针对的是首屏的
 * 小列表（名称 + 一个右对齐数字，语义自明）；这里一行有三个数字（夏普 / 近3月），
 * 去掉表头就分不清哪个是哪个 —— **数字不带标签就不是信息**（§5.5）。
 */
export function PoolBoard({ data, className = '' }: { data: BoardPool; className?: string }) {
  const [active, setActive] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const cur = data.boards.find((b) => b.board === active) ?? data.boards[0]
  if (!cur) return null

  const toggle = (code: string) => setOpen((m) => ({ ...m, [code]: !m[code] }))

  return (
    <Card
      className={className}
      title="筛选池 · 按主题板块"
      note={`· 共 ${data.total_funds} 只进池 · 每板块前 ${data.size}`}
    >
      <div className="mb-2.5 text-caption leading-relaxed text-fg-4">
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
            <th className={TH + ' text-left'}>代码</th>
            <th className={TH + ' text-left'}>名称</th>
            <th className={TH + ' text-right'}>夏普</th>
            <th className={TH + ' text-right'}>近3月</th>
          </tr>
        </thead>
        <tbody>
          {cur.funds.map((f) => {
            const on = !!open[f.code]
            return (
              /* Fragment：展开行是"行内的第二条 tr"，不额外占一列 —— 否则可视列又变成 5 个 */
              <Fragment key={f.code}>
                <tr className="row-hover border-t border-line">
                  <td className="mono py-2 pr-3 align-baseline text-caption text-fg-4">{f.code}</td>
                  <td className="py-2 pr-3 align-baseline text-body">
                    {/* 名称即展开开关：不新增"详情"列，可视列数保持 4 */}
                    <button
                      type="button"
                      onClick={() => toggle(f.code)}
                      aria-expanded={on}
                      className="text-left text-fg transition-colors hover:text-accent"
                    >
                      <span className="mr-1 text-fg-4">{on ? '⌃' : '⌄'}</span>
                      {plainText(f.name)}
                    </button>
                    <DropMark s={f.constraint_status} />
                  </td>
                  <td className="num py-2 align-baseline text-right text-body-sm text-fg-2">
                    {f.sharpe == null ? '—' : f.sharpe.toFixed(2)}
                  </td>
                  <td className="py-2 align-baseline text-right text-body-sm">
                    <Num v={f.momentum_3m} />
                  </td>
                </tr>
                {on && (
                  <tr>
                    <td colSpan={4} className="pb-2.5 pl-1 text-caption leading-relaxed text-fg-3">
                      <span className="mr-4">
                        类型 <span className="text-fg-2">{plainText(f.type) || '—'}</span>
                      </span>
                      <span className="mr-4">
                        风险 <span className="text-fg-2">{plainText(f.risk) || '—'}</span>
                      </span>
                      <span className="mr-4">
                         {/* ⚠️ fee 缺失是 null，不是 0 —— 显示 0 会让人以为"这只零费率" */}
                        费率{' '}
                        <span className="num text-fg-2">
                          {f.fee == null ? '—' : `${f.fee.toFixed(2)}%`}
                        </span>
                      </span>
                      <span>
                        申购 <span className="text-fg-2">{plainText(f.purchase_status) || '—'}</span>
                      </span>
                      {f.constraint_reasons?.length ? (
                        <div className="mt-1">
                          约束 <span className="text-fg-2">{f.constraint_reasons.join('；')}</span>
                        </div>
                      ) : null}
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </Card>
  )
}
