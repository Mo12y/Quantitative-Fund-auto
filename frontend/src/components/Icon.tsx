import type { ReactNode } from 'react'

/**
 * 内联 SVG 图标（Lucide 风格路径，MIT）。
 *
 * 为什么手写而不是引图标库：本项目铁律「不新增运行时依赖」，
 * 而现状只需要 4~6 个图标 —— 引一个 `lucide-react` 是拿 ~40KB 依赖换 20 行代码。
 * 统一用 `stroke: currentColor`，颜色跟随文字，不在图标里写死色值。
 */
const PATHS: Record<string, ReactNode> = {
  /** 今天：日历 + 打勾 */
  today: (
    <>
      <rect x="3" y="4.5" width="18" height="16.5" rx="2.5" />
      <path d="M8 2.5v3.5M16 2.5v3.5M3 9.5h18" />
      <path d="m9.5 15.2 1.8 1.8 3.5-3.7" />
    </>
  ),
  /** 持仓：饼图 */
  position: (
    <>
      <path d="M21.2 15.9A10 10 0 1 1 8 2.83" />
      <path d="M22 12A10 10 0 0 0 12 2v10z" />
    </>
  ),
  /** 研究：放大镜 */
  research: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20.6 20.6-4.3-4.3" />
    </>
  ),
  /** 设置：滑块 */
  settings: (
    <>
      <path d="M20 7h-9M14 17H5" />
      <circle cx="17" cy="17" r="3" />
      <circle cx="7" cy="7" r="3" />
    </>
  ),
  /** 温度：温度计 */
  temp: (
    <>
      <path d="M14 14.8V5a2 2 0 1 0-4 0v9.8a4 4 0 1 0 4 0Z" />
      <path d="M12 9v7" />
    </>
  ),
}

export function Icon({
  name,
  size = 16,
  className,
}: {
  name: keyof typeof PATHS | string
  size?: number
  className?: string
}) {
  const d = PATHS[name]
  if (!d) return null
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      {d}
    </svg>
  )
}
