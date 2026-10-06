import {
  Area,
  ComposedChart,
  Line,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { BENCH, ZERO } from '../lib/chartColors'
import { dirClass, dirOf, fmtSignedPct } from '../lib/format'

/**
 * 组合收益曲线的**绘图部分**（唯一 import Recharts 的地方）。
 *
 * ⚠️ 为什么单独拆成文件：Recharts + d3 约 **429KB**，是全站最大的一块依赖。
 * 把它留在这里、由 `CurveChart` **懒加载**，首屏关键路径就能少下 429KB
 * （实测首屏必须字节 672KB → 243KB）。卡片外壳（标题/大数字/图例）仍同步渲染，
 * 所以只有 210px 的绘图区是骨架，**高度完全一致、不会发生布局位移**。
 *
 * ══════════════════════════════════════════════════════════════════════════
 * ⚠️ 2026-10-06 改版：**去掉网格与 Y 轴刻度**（DESIGN §5.1.1 第 4 条）
 *
 * 依据是三个独立开源产品的实测（Wealthfolio / Ghostfolio / Rotki，见计划书 §13.3.2）：
 * 它们的曲线**全都是"裸"的** —— 没有网格、没有 Y 轴刻度、没有边框。
 *
 * 原实现有 **8 条水平网格 + 7 条垂直网格 + 两条轴线 + 6 档 Y 刻度**，
 * 在一个 210px 高的绘图区里，等于**每 20px 就有一条线**。两条数据线（本组合 + 沪深300）
 * 被埋在参考线里 —— 这正是用户说"信息面对人类有点变态"的最直观来源。
 *
 * 权衡（如实说明）：去掉 Y 轴刻度后，**不能从图上直接读出数值**，只能看形状。
 * 这是**有意**的取舍：曲线的职责是回答"趋势对不对 / 跑赢没有"，具体数值由
 * ① 上方大数字 ② hover tooltip 承担。想要精确读数的场合本来就不该看曲线。
 * 零轴（`ReferenceLine y=0`）**保留** —— 它是全图唯一的语义分界（盈/亏），
 * 不是装饰性参考线。
 * ══════════════════════════════════════════════════════════════════════════
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
    <div className="rounded-[var(--radius-md)] border border-line-strong bg-card/95 px-3 py-2 text-caption shadow-[0_20px_44px_-20px_rgba(0,0,0,.95)] backdrop-blur">
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
        <ComposedChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 4 }}>
          <defs>
            <linearGradient id="qfa-combo-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={comboColor} stopOpacity={0.20} />
              <stop offset="100%" stopColor={comboColor} stopOpacity={0} />
            </linearGradient>
          </defs>

          {/* ⚠️ 这里**没有** CartesianGrid —— 见文件顶部说明（2026-10-06 去网格）。
              要恢复网格，请先读 DESIGN §5.1.1：三个参照产品都不画。 */}
          <XAxis
            dataKey="date"
            tick={{ fill: 'var(--color-fg-4)', fontSize: 11 }}
            tickLine={false}
            /* 无轴线：底部不再有一条横线（"不画边框"）*/
            axisLine={false}
            /* 只显示 MM-DD —— 完整日期（2026-08-26）10 个字符会把一屏挤满，
               而且长标签逼着 `minTickGap` 大幅抽稀，标签间距也就没法看了 */
            tickFormatter={(d: string) => String(d).slice(5)}
            interval={xInterval}
            dy={4}
          />
          {/* Y 轴只用来**锁定值域**（域必须包含 0，见 CurveChart 的注释），
              整条轴 `hide` —— 不画刻度、不画轴线、不占宽度。
              数值改由 hover tooltip 承担。 */}
          <YAxis hide domain={[yLo, yHi]} ticks={yTicks} />

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
