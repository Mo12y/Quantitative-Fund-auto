import type { ReactNode } from 'react'

/**
 * 入口页头 —— 四个入口共用，避免各页各写一份标题层级（漂移风险）。
 * 右侧放 `SourceTag` 之类的元信息。
 *
 * 视觉：标题下方压一团低透明度的极光，让首屏顶端有"光从上面来"的感觉；
 * 纯文字页头在一屏暗色里会显得像文档而不是产品。
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
    <div className="relative mb-6 flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
      <i
        className="pointer-events-none absolute -left-8 -top-14 h-36 w-72 rounded-full opacity-40 blur-2xl"
        style={{ background: 'radial-gradient(closest-side, rgba(90,162,255,.42), transparent)' }}
        aria-hidden="true"
      />
      <div className="relative">
        <h1 className="text-[24px] font-semibold leading-tight tracking-[-.02em] text-fg">{title}</h1>
        {sub && <div className="mt-1 text-[12.5px] text-fg-3">{sub}</div>}
      </div>
      <div className="relative">{children}</div>
    </div>
  )
}
