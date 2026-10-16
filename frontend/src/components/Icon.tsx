import type { ReactNode } from 'react'

/**
 * 内联 SVG 图标（Lucide 风格路径，MIT）。
 *
 * 为什么手写而不是引图标库：本项目铁律「不新增运行时依赖」，
 * 而现状只需要 3~6 个图标 —— 引一个 `lucide-react` 是拿 ~40KB 依赖换几十行代码。
 * 统一用 `stroke: currentColor`，颜色跟随文字，不在图标里写死色值。
 *
 * ⚠️ 2026-10-16 T3：`today/position/research/settings/temp` 五个键已随旧四页删除；
 * 现只保留三个入口图标（decision / ledger / data）。
 */
const PATHS: Record<string, ReactNode> = {
  /** 决策台：指南针（方向由你定，系统只给方位） */
  decision: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="m16.2 7.8-2.1 6.3-6.3 2.1 2.1-6.3z" />
    </>
  ),
  /** 账本：账册 */
  ledger: (
    <>
      <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20" />
    </>
  ),
  /** 数据状态：数据库 */
  data: (
    <>
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M3 5v14a9 3 0 0 0 18 0V5" />
      <path d="M3 12a9 3 0 0 0 18 0" />
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
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {d}
    </svg>
  )
}
