import type { ReactNode } from 'react'

interface CardProps {
  /** 卡片标题（小号灰字，视觉稿里是 h2） */
  title?: string
  /** 标题右侧附注（如「· 结论」「· 实时 · 东财」），比标题更弱一级 */
  note?: string
  /** 结论卡：加左侧强调条（最高层级，整屏最重要的东西） */
  lead?: boolean
  className?: string
  children: ReactNode
}

export function Card({ title, note, lead = false, className = '', children }: CardProps) {
  return (
    <section
      className={
        'mb-3.5 rounded-lg border border-line bg-card ' +
        (lead ? 'border-l-2 border-l-accent px-[22px] py-5' : 'px-5 py-[18px] ') +
        className
      }
    >
      {title && (
        <h2 className="mb-3 text-xs font-medium tracking-[.06em] text-fg-3">
          {title}
          {note && <span className="ml-1.5 font-normal tracking-normal text-fg-4">{note}</span>}
        </h2>
      )}
      {children}
    </section>
  )
}