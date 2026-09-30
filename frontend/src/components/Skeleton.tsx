/**
 * 骨架屏 —— 加载时给"形状"，而不是干巴巴一行「加载中…」。
 *
 * 两个约定：
 *  1. 纯装饰 → `aria-hidden`；真正对读屏播报的是外层的 `sr-only` 文本，
 *     否则读屏会把占位块也念一遍（重复播报）。
 *  2. 只做几行不等宽的条 —— 等宽会像"内容已就绪但全空"，反而更怪。
 */
const WIDTHS = [92, 74, 58, 82, 46]

export function Skeleton({ lines = 3, className = '' }: { lines?: number; className?: string }) {
  return (
    <div className={className}>
      <span className="sr-only">加载中</span>
      <div className="space-y-2.5" aria-hidden="true">
        {Array.from({ length: lines }).map((_, i) => (
          <div key={i} className="skeleton h-3" style={{ width: `${WIDTHS[i % WIDTHS.length]}%` }} />
        ))}
      </div>
    </div>
  )
}
