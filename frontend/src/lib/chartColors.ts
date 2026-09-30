/**
 * 图表用色 —— 与 `index.css` 的 `@theme` **同源**，改一处要同步另一处
 * （Recharts 不认 CSS 变量，只能各写一份）。
 *
 * ⚠️ 单独成文件是为了**不把 Recharts 拖进首屏**：`CurveChart`（卡片外壳，首屏要）
 * 与 `ChartPlot`（绘图，懒加载）都要用这些色值；若常量写在 `ChartPlot` 里，
 * `CurveChart` 一 import 就会把整个 Recharts 拉回首屏 chunk，拆包白做。
 */
export const RISE = '#ff6b5e'
export const FALL = '#34c76a'
export const BENCH = '#7c8899'
/** 网格线：**必须看得见**（曾经 #1c2532 几乎不可见，用户反馈"没有参考系"） */
export const GRID = '#202a37'
/** 坐标轴线：比网格略亮，给图一个明确的框 */
export const AXIS = '#2b3644'
/** 刻度文字 */
export const TICK = '#7d8a9d'
/** 零轴（y=0）—— 全图**最强**的一条线（实线 + 最亮 + 最粗） */
export const ZERO = '#7b8fa6'
