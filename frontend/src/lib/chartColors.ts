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

// ⚠️ 2026-10-06：曲线按 DESIGN §5.1.1 去掉了网格与 Y 轴刻度（三个参照产品都不画），
// 于是下面三个常量**暂时没有使用者**。保留而不删，是因为它们跟着 @theme 一起维护，
// 删了以后要恢复网格就得重新对一遍色；注释在此说明"闲置原因"，免得后人误以为还在用。
/** 网格线（当前闲置：曲线已去网格） */
export const GRID = '#202a37'
/** 坐标轴线（当前闲置：X 轴已去轴线，Y 轴整条 hide） */
export const AXIS = '#2b3644'
/** 刻度文字（当前闲置：只读 tooltip，不画刻度） */
export const TICK = '#7d8a9d'

/** 零轴（y=0）—— 全图**最强**的一条线（实线 + 最亮 + 最粗）。去网格后它仍在用。 */
export const ZERO = '#7b8fa6'
