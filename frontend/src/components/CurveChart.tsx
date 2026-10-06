import { lazy, Suspense, useMemo } from 'react'
import type { PortfolioCurve } from '../api/types'
import { BENCH, FALL, RISE, ZERO } from '../lib/chartColors'
import { dirClass, dirOf, dirSymbol, fmtMoney, fmtSignedPct } from '../lib/format'
import { Card } from './Card'

/**
 * 绘图部分**懒加载** —— Recharts + d3 约 429KB，是全站最大的一块依赖。
 * 卡片外壳（标题 / 大数字 / 图例 / 脚注）同步渲染，只有 210px 的绘图区走骨架，
 * **占位高度与真实图完全一致 → 不产生布局位移**。
 * 实测首屏必须下载的 JS：672KB → 243KB。
 */
const ChartPlot = lazy(() => import('./ChartPlot').then((m) => ({ default: m.ChartPlot })))

/**
 * 生成"好看"的 Y 轴刻度，**并保证 0 一定在刻度里**。
 *
 * 为什么不直接用 Recharts 自动刻度：实测它在 [-3.3%, 2.0%] 上取到 `2.0 / -1.3 / -3.3`，
 * **跳过了 0** —— 零轴虽然画出来了，却没有对应标签，"盈亏分界"就还是读不出来。
 *
 * 步长从「漂亮档位」里挑（1 / 2 / 2.5 / 5 × 10^k），选**刻度条数最接近 want** 的那个：
 * 早先版本用"最小的 ≥ raw 的档位"，在 span≈6 时会直接跳到 step=2，只剩 3 条线 ——
 * 网格太稀等于没有参考系。
 */
function niceTicks(lo: number, hi: number, want = 6): number[] {
  const span = hi - lo
  if (!(span > 0)) return [lo, 0, hi]
  const mag = Math.pow(10, Math.floor(Math.log10(span)))
  let best: number[] | null = null
  for (const m of [0.5, 1, 2, 2.5, 5, 10]) {
    const step = m * mag
    const out: number[] = []
    for (let t = Math.ceil(lo / step) * step; t <= hi + 1e-9; t += step) {
      out.push(Number(t.toFixed(6)))
    }
    if (out.length < 3 || out.length > want + 3) continue
    if (!best || Math.abs(out.length - want) < Math.abs(best.length - want)) best = out
  }
  const ticks = best ?? []
  if (!ticks.some((t) => Math.abs(t) < 1e-9)) ticks.push(0)
  if (!ticks.length) return [lo, 0, hi]
  return ticks.sort((a, b) => a - b)
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
  // X 轴刻度密度：目标 ~7 个标签 —— 它同时也是**垂直网格线的条数**。
  // 标签太多会互相挤压、网格线糊成一片；太少又失去参考系。
  const xInterval = Math.max(0, Math.ceil(n / 7) - 1)

  return (
    <Card title="组合收益" note="· 资金加权 · 过零轴为盈亏分界">
      {/* ⚠️ 2026-10-06（瘦身第 2 条）：本卡**不再放大字号收益率**。
          首屏的大数字只能有一个（总资产，见 `Today.tsx` 的 Hero 块），而这里的终值
          （资金加权累计收益率）与 Hero 参照里的收益率**是同源数字** ——
          放大两遍等于让同一条信息占两个视觉焦点。改为一行小字，**信息一处不减**。 */}
      <div className="mb-2.5 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-body-sm">
        <span className="text-fg-3">
          本组合{' '}
          <b className={'num ' + dirClass(d)}>
            {dirSymbol(d)}
            {fmtSignedPct(last)}
          </b>
        </span>
        <span className="text-fg-3">
          {rel >= 0 ? '跑赢' : '跑输'}沪深300{' '}
          <b className={dirClass(relDir)}>{fmtSignedPct(rel)}</b>
        </span>
      </div>

      <div className="mb-2 flex flex-wrap gap-4 text-caption text-fg-3">
        <span className="inline-flex items-center gap-1.5">
          <i className="inline-block h-[2.5px] w-4 rounded-full align-middle" style={{ background: comboColor }} />
          本组合
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="inline-block w-4 border-t-2 border-dashed align-middle" style={{ borderColor: BENCH }} />
          沪深300
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="inline-block w-4 border-t-2 align-middle" style={{ borderColor: ZERO }} />
          零轴（盈亏分界）
        </span>
      </div>

      <Suspense
        fallback={
          <div className="skeleton h-[210px] w-full rounded-[var(--radius-md)]" aria-label="图表加载中" />
        }
      >
        <ChartPlot
          data={data}
          comboColor={comboColor}
          yLo={yLo}
          yHi={yHi}
          yTicks={yTicks}
          xInterval={xInterval}
          lastDate={curve.dates[n - 1]}
          last={last}
        />
      </Suspense>

      {/* ⚠️ 未入仓披露。曲线只画「已起算」的持仓，于是曲线终值与顶部 KPI 的
          「总资产」天然对不上（本机实测：KPI ¥854.51 vs 曲线 ¥834.60）。
          黑箱验收审计 F-03 专门修过这个「同一屏两个市值互相打架」，修法就是
          **把差额显式说出来**，后端也为此加了 excluded_not_in / excluded_pending /
          excluded_amount 三个字段；旧前端照此显示了。
          但本次前端重写**三个字段一个都没渲染**（types.ts 里有类型，全项目无人使用）
          —— 等于把 F-03 的修复丢了，用户会重新看到两个对不上的数字而无从解释。 */}
      {excluded > 0 && (
        <div className="mt-2 text-caption text-fg-4">
          另有 {excluded} 笔未入仓
          {curve.excluded_amount ? (
            <span className="num">{`（约 ${fmtMoney(curve.excluded_amount)}）`}</span>
          ) : (
            ''
          )}
          未计入
        </div>
      )}

      <div className="mt-1 text-caption text-fg-4">
        {curve.dates[0]} ~ {curve.dates[n - 1]} · {n} 个交易日 · 起点 {fmtSignedPct(data[0].combo)}
        {/* 原为硬编码的「起止 0.00%」：数字写死在文案里会与实际不符，
            且「起止」说的是首尾两个值却只给了首值 —— 措辞与内容不符。
            末值已由上方大字号显示（那是本卡的主结论），故这里只报**起点**，交代曲线基线。 */}
      </div>
    </Card>
  )
}
