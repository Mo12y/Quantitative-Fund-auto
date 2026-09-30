import { useMemo } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { PortfolioCurve } from '../api/types'
import { dirClass, dirOf, dirSymbol, fmtMoney, fmtSignedPct } from '../lib/format'
import { Card } from './Card'

/* 图表用色与 index.css 的 @theme 同源（Recharts 不吃 CSS 变量，只能各写一份，改一处要同步） */
const RISE = '#ff6b5e'
const FALL = '#34c76a'
const BENCH = '#7c8899'
/** 网格线：压得很暗，好让零轴跳出来 */
const GRID = '#1c2532'
const TICK = '#6b7889'
/**
 * ⚠️ 零轴（y=0）—— 必须与普通网格线**明显区分**：
 *   更亮（#5f7488 vs 网格 #1c2532）+ 实线（网格是虚线）+ 更粗（1.6 vs 1）。
 *   零轴是这张图唯一的分界语义（盈/亏），混进网格里就读不出来了。
 */
const ZERO = '#5f7488'

interface TipPayload {
  dataKey?: string | number
  value?: number | null
}

/**
 * 生成"好看"的 Y 轴刻度，**并保证 0 一定在刻度里**。
 *
 * 为什么不直接用 Recharts 自动刻度：实测它在 [-3.3%, 2.0%] 上取到 `2.0 / -1.3 / -3.3`，
 * **跳过了 0** —— 零轴虽然画出来了，却没有对应标签，"盈亏分界"就还是读不出来。
 * （这就是"零线不明显"的根因之一，不是线不够亮。）
 */
function niceTicks(lo: number, hi: number, want = 5): number[] {
  const span = hi - lo
  if (!(span > 0)) return [lo, 0, hi]
  const raw = span / want
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const norm = raw / mag
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag
  const out: number[] = []
  for (let t = Math.ceil(lo / step) * step; t <= hi + 1e-9; t += step) out.push(Number(t.toFixed(4)))
  if (!out.some((t) => Math.abs(t) < 1e-9)) out.push(0)
  return out.sort((a, b) => a - b)
}

/** 自定义 tooltip：跟随主题，不用 Recharts 默认的白底 */
function Tip({ active, payload, label }: { active?: boolean; payload?: TipPayload[]; label?: string }) {
  if (!active || !payload || !payload.length) return null
  const combo = payload.find((p) => p.dataKey === 'combo')
  const bench = payload.find((p) => p.dataKey === 'bench')
  return (
    <div className="rounded-[var(--radius-md)] border border-line-strong bg-card/95 px-3 py-2 text-[11.5px] shadow-[0_20px_44px_-20px_rgba(0,0,0,.95)] backdrop-blur">
      <div className="mb-1.5 text-fg-4">{label}</div>
      {combo && (
        <div className="flex items-center gap-4">
          <span className="text-fg-3">本组合</span>
          <span className={'mono ml-auto ' + dirClass(dirOf(combo.value ?? 0))}>
            {fmtSignedPct(combo.value ?? null)}
          </span>
        </div>
      )}
      {bench && bench.value != null && (
        <div className="flex items-center gap-4">
          <span className="text-fg-3">沪深300</span>
          <span className="mono ml-auto text-fg-2">{fmtSignedPct(bench.value)}</span>
        </div>
      )}
    </div>
  )
}

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

  // ⚠️ Y 轴范围**必须显式给数**（不能用函数式 domain）：
  //    实测函数式 domain + `baseValue={0}` 时，面积填充的基线会退化成数据最小值
  //    （负收益区被当成"正面积"填出来，视觉上与零轴矛盾）。
  //    范围同时**强制包含 0**，否则全正/全负时零轴跑到画布外、ReferenceLine 不渲染。
  const nums = [...curve.return_pct, ...curve.benchmark].filter(
    (v): v is number => typeof v === 'number' && !Number.isNaN(v),
  )
  const lo0 = nums.length ? Math.min(0, ...nums) : 0
  const hi0 = nums.length ? Math.max(0, ...nums) : 0
  const pad = (hi0 - lo0) * 0.08 || 0.5
  const yLo = lo0 - pad
  const yHi = hi0 + pad
  const yTicks = niceTicks(yLo, yHi)

  return (
    <Card title="组合收益" note="· 资金加权 · 过零轴为盈亏分界">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <div className={'num text-[32px] font-semibold leading-none tracking-[-.02em] ' + dirClass(d)}>
          <span className="mr-1 align-top text-[13px]">{dirSymbol(d)}</span>
          {fmtSignedPct(last)}
        </div>
        <div className="text-[12.5px] text-fg-3">
          {rel >= 0 ? '跑赢' : '跑输'}沪深300{' '}
          <b className={dirClass(relDir)}>{fmtSignedPct(rel)}</b>
        </div>
      </div>

      <div className="mb-2 flex flex-wrap gap-4 text-[11px] text-fg-3">
        <span className="inline-flex items-center gap-1.5">
          <i className="inline-block h-[2.5px] w-4 rounded-full align-middle" style={{ background: comboColor }} />
          本组合
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i
            className="inline-block w-4 border-t-2 border-dashed align-middle"
            style={{ borderColor: BENCH }}
          />
          沪深300
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="inline-block w-4 border-t-2 align-middle" style={{ borderColor: ZERO }} />
          零轴（盈亏分界）
        </span>
      </div>

      <div className="h-[210px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          {/* ⚠️ margin.left 曾是 -16，会把 Y 轴刻度的**负号裁掉**：
              Recharts 的 Y 轴刻度右对齐，"-4.0%" 比 "4.0%" 多占一格，
              -16 的负边距正好吃掉这一格 → 图上 -4% 与 +4% 长得一模一样。
              金融图里这是会误导读数的缺陷，故改为 0（宽度已由 YAxis width 预留）。 */}
          <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="qfa-combo-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={comboColor} stopOpacity={0.20} />
                <stop offset="100%" stopColor={comboColor} stopOpacity={0} />
              </linearGradient>
            </defs>

            <CartesianGrid stroke={GRID} strokeDasharray="2 6" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fill: TICK, fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              minTickGap={36}
              dy={4}
            />
            {/* 域强制包含 0，且刻度里一定有 0（见 niceTicks 注释：自动刻度会跳过 0） */}
            <YAxis
              tick={{ fill: TICK, fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={50}
              tickFormatter={(v: number) => `${v.toFixed(1)}%`}
              domain={[yLo, yHi]}
              ticks={yTicks}
            />

            {/* 零轴：比网格更亮、实线、更粗 */}
            <ReferenceLine y={0} stroke={ZERO} strokeWidth={1.6} />

            <Tooltip
              content={<Tip />}
              cursor={{ stroke: '#33405a', strokeWidth: 1, strokeDasharray: '3 3' }}
            />

            <Area
              type="monotone"
              dataKey="combo"
              name="本组合"
              stroke={comboColor}
              strokeWidth={2.6}
              fill="url(#qfa-combo-fill)"
              baseValue={0}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 0 }}
              /* 绘制动效：本组合先画，沪深300 稍晚跟上 —— 一起画会看不出两条线的关系 */
              animationDuration={950}
              animationEasing="ease-out"
            />
            <Line
              type="monotone"
              dataKey="bench"
              name="沪深300"
              stroke={BENCH}
              strokeWidth={1.8}
              strokeDasharray="6 4"
              dot={false}
              animationBegin={260}
              animationDuration={950}
              animationEasing="ease-out"
            />
            {/* 末点强调：一眼看到"现在在哪" */}
            <ReferenceDot
              x={curve.dates[n - 1]}
              y={last}
              r={3.5}
              fill={comboColor}
              stroke="var(--color-card)"
              strokeWidth={2}
              isFront
            />
          </ComposedChart>
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
        <div className="mt-2 text-[11px] text-fg-4">
          另有 {excluded} 笔未入仓
          {curve.excluded_amount ? `（约 ${fmtMoney(curve.excluded_amount)}）` : ''}
          未计入
        </div>
      )}

      <div className="mt-1 text-[11px] text-fg-4">
        {curve.dates[0]} ~ {curve.dates[n - 1]} · {n} 个交易日 · 起点 {fmtSignedPct(data[0].combo)}
        {/* 原为硬编码的「起止 0.00%」：数字写死在文案里会与实际不符，
            且「起止」说的是首尾两个值却只给了首值 —— 措辞与内容不符。
            末值已由上方大字号显示（那是本卡的主结论），故这里只报**起点**，交代曲线基线。 */}
      </div>
    </Card>
  )
}
