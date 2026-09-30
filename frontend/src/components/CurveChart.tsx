import { useMemo } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { PortfolioCurve } from '../api/types'
import { dirClass, dirOf, dirSymbol, fmtMoney, fmtSignedPct } from '../lib/format'
import { Card } from './Card'

/** 图表用色与 index.css 的 @theme 同源（Recharts 不吃 CSS 变量，只能各写一份，改一处要同步） */
const RISE = '#f4614d'
const FALL = '#3fb950'
const BENCH = '#9ba7b4'
const GRID = '#262c36'
const TICK = '#6e7681'

/**
 * 组合收益曲线 —— 回答「我跑赢了吗」。
 * 本组合用**涨红跌绿**（中国惯例，按终值方向取色）；沪深300 是参照，用中性灰虚线不抢主角。
 */
export function CurveChart({ curve }: { curve: PortfolioCurve }) {
  const data = useMemo(
    () =>
      curve.dates.map((d, i) => ({
        date: d,
        combo: curve.return_pct[i] ?? 0,
        bench: curve.benchmark[i] ?? null,
      })),
    [curve],
  )
  if (!curve.dates.length) return null

  const n = curve.dates.length
  const last = curve.return_pct[n - 1] ?? 0
  const benchLast = curve.benchmark[curve.benchmark.length - 1] ?? 0
  const rel = last - benchLast
  const d = dirOf(last)
  const relDir = dirOf(rel)
  const comboColor = last >= 0 ? RISE : FALL
  // 未入仓 = 待确认买入 + 起算日晚于曲线末点的持仓（见后端 curve.excluded_*）
  const excluded = (curve.excluded_not_in ?? 0) + (curve.excluded_pending ?? 0)

  return (
    <Card title="组合收益" note="· 资金加权">
      <div className="mb-2">
        <div className="text-[25px] font-medium tracking-[-.3px]">
          <span className={dirClass(d)}>
            <span className="mr-0.5 text-[10px]">{dirSymbol(d)}</span>
            {fmtSignedPct(last)}
          </span>
          <span className="ml-2.5 text-[12.5px] font-normal text-fg-3">
            {rel >= 0 ? '跑赢' : '跑输'}沪深300 <b className={dirClass(relDir)}>{fmtSignedPct(rel)}</b>
          </span>
        </div>
      </div>

      <div className="mb-1 flex gap-4 text-[11.5px] text-fg-3">
        <span>
          <i className="mr-1 inline-block w-3 border-t-2 align-middle" style={{ borderColor: comboColor }} />
          本组合
        </span>
        <span>
          <i className="mr-1 inline-block w-3 border-t-2 border-dashed align-middle" style={{ borderColor: BENCH }} />
          沪深300
        </span>
      </div>

      <div className="h-[190px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          {/* ⚠️ margin.left 曾是 -16，会把 Y 轴刻度的**负号裁掉**：
              Recharts 的 Y 轴刻度右对齐，"-4.0%" 比 "4.0%" 多占一格，
              -16 的负边距正好吃掉这一格 → 图上 -4% 与 +4% 长得一模一样。
              金融图里这是会误导读数的缺陷，故改为 0（宽度已由 YAxis width 预留）。 */}
          <LineChart data={data} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={GRID} strokeDasharray="3 4" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fill: TICK, fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: GRID }}
              minTickGap={30}
            />
            <YAxis tick={{ fill: TICK, fontSize: 11 }} tickLine={false} axisLine={false} width={46} tickFormatter={(v: number) => `${v.toFixed(1)}%`} />
            <Tooltip
              contentStyle={{ background: '#161b22', border: `1px solid ${GRID}`, borderRadius: 10, fontSize: 12 }}
              labelStyle={{ color: '#9ba7b4' }}
              formatter={(value) => `${Number(value).toFixed(2)}%`}
            />
            <Line type="monotone" dataKey="bench" name="沪深300" stroke={BENCH} strokeWidth={2} strokeDasharray="6 4" dot={false} />
            <Line type="monotone" dataKey="combo" name="本组合" stroke={comboColor} strokeWidth={2.6} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* ⚠️ 未入仓披露。曲线只画「已起算」的持仓，于是曲线终值与顶部 KPI 的
          「总资产」天然对不上（本机实测：KPI ¥854.51 vs 曲线 ¥834.60）。
          黑箱验收审计 F-03 专门修过这个「同一屏两个市值互相打架」，修法就是
          **把差额显式说出来**，后端也为此加了 excluded_not_in / excluded_pending /
          excluded_amount 三个字段；旧前端照此显示了。
          但本次前端重写**三个字段一个都没渲染**（types.ts 里有类型，全项目无人使用）
          —— 等于把 F-03 的修复丢了，用户会重新看到两个对不上的数字而无从解释。 */}
      {excluded > 0 && (
        <div className="mt-1 text-[11.5px] text-fg-4">
          另有 {excluded} 笔未入仓
          {curve.excluded_amount ? `（约 ${fmtMoney(curve.excluded_amount)}）` : ''}
          未计入
        </div>
      )}

      <div className="mt-1 text-[11.5px] text-fg-4">
        {curve.dates[0]} ~ {curve.dates[n - 1]} · {n} 个交易日
        {/* 原为硬编码的「起止 0.00%」。两点问题：
            ① 数字写死在文案里，迟早与实际不符；
            ② 「起止」说的是首尾两个值，却只给了首值 —— 措辞与内容不符。
            末值已由上方大字号显示（那是本卡的主结论），故这里只报**起点**，
            用来交代曲线基线，避免与主结论重复。 */}
        {` · 起点 ${fmtSignedPct(data[0].combo)}`}
      </div>
    </Card>
  )
}