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
import { AXIS, BENCH, GRID, TICK, ZERO } from '../lib/chartColors'
import { dirClass, dirOf, fmtSignedPct } from '../lib/format'

/**
 * 组合收益曲线的**绘图部分**（唯一 import Recharts 的地方）。
 *
 * ⚠️ 为什么单独拆成文件：Recharts + d3 约 **429KB**，是全站最大的一块依赖。
 * 把它留在这里、由 `CurveChart` **懒加载**，首屏关键路径就能少下 429KB
 * （实测首屏必须字节 672KB → 243KB）。卡片外壳（标题/大数字/图例）仍同步渲染，
 * 所以只有 210px 的绘图区是骨架，**高度完全一致、不会发生布局位移**。
 */
export interface PlotPoint {
  date: string
  combo: number
  bench: number | null
}

interface TipPayload {
  dataKey?: string | number
  value?: number | null
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

export function ChartPlot({
  data,
  comboColor,
  yLo,
  yHi,
  yTicks,
  xInterval,
  lastDate,
  last,
}: {
  data: PlotPoint[]
  comboColor: string
  yLo: number
  yHi: number
  yTicks: number[]
  xInterval: number
  lastDate: string
  last: number
}) {
  return (
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

          {/* 双向网格 —— 垂直网格**必须开**：原来 `vertical={false}`，实测「垂直 0 条、轴线 0 条」，
              折线浮在空底上没有任何参考系（用户直接反馈读不出横坐标位置）。 */}
          <CartesianGrid stroke={GRID} strokeDasharray="2 4" />
          <XAxis
            dataKey="date"
            tick={{ fill: TICK, fontSize: 11 }}
            tickLine={false}
            /* 底部轴线：给图一个下边界（原来 axisLine=false，整张图是"浮"着的） */
            axisLine={{ stroke: AXIS }}
            /* 只显示 MM-DD —— 完整日期（2026-08-26）10 个字符会把一屏挤满，
               而且长标签逼着 `minTickGap` 大幅抽稀，垂直网格也就跟着没法看 */
            tickFormatter={(d: string) => String(d).slice(5)}
            interval={xInterval}
            dy={4}
          />
          {/* 域强制包含 0，且刻度里一定有 0（见 `niceTicks`：Recharts 自动刻度会跳过 0） */}
          <YAxis
            tick={{ fill: TICK, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: AXIS }}
            width={50}
            tickFormatter={(v: number) => `${v.toFixed(1)}%`}
            domain={[yLo, yHi]}
            ticks={yTicks}
          />

          {/* 零轴：比网格更亮、实线、更粗 —— 全图唯一的分界语义（盈/亏） */}
          <ReferenceLine y={0} stroke={ZERO} strokeWidth={1.6} />

          <Tooltip content={<Tip />} cursor={{ stroke: '#33405a', strokeWidth: 1, strokeDasharray: '3 3' }} />

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
            x={lastDate}
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
  )
}
