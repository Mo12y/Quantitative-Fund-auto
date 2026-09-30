import type { ReactNode } from 'react'

/**
 * 入口页头 —— 四个入口共用，避免各页各写一份标题层级（漂移风险）。
 * 右侧放 `SourceTag` 之类的元信息。
 */
export function PageHead({
  title,
  sub,
  children,
}: {
  title: string
  sub?: string
  children?: ReactNode
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
      <div>
        <h1 className="text-[20px] font-semibold tracking-[-.015em] text-fg">{title}</h1>
        {sub && <div className="mt-0.5 text-[12.5px] text-fg-3">{sub}</div>}
      </div>
      {children}
    </div>
  )
}
