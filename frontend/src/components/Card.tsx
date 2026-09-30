import type { ReactNode } from 'react'

interface CardProps {
  /** 卡片标题（小号、微字距、弱色 —— 金融界面的"字段名"惯例） */
  title?: string
  /** 标题右侧附注（比标题更弱一级） */
  note?: string
  /** 结论卡：左侧强调条 + 极光渐变（整屏最高层级） */
  lead?: boolean
  /** 标题行右侧的操作区（按钮 / 标签） */
  action?: ReactNode
  className?: string
  children: ReactNode
}

/**
 * 卡片容器 —— 全站统一表面。
 *
 * 表面处理收在 `index.css` 的 `.surface` / `.surface-lead` 里（顶部内高光 + 柔阴影 +
 * 极窄边框），组件只负责内边距与标题层级。这样换皮肤只改一处。
 *
 * ⚠️ 不加 `overflow-hidden` —— 图表 tooltip / 下拉会溢出卡面被裁掉。
 */
export function Card({ title, note, lead = false, action, className = '', children }: CardProps) {
  return (
    <section
      className={
        'rise-in mb-4 rounded-[var(--radius-lg)] ' +
        (lead ? 'surface-lead px-6 py-5' : 'surface px-5 py-[18px]') +
        (className ? ' ' + className : '')
      }
    >
      {(title || action) && (
        <header className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          {title ? (
            <h2 className="text-[12px] font-semibold tracking-[.06em] text-fg-3">
              {title}
              {note && <span className="ml-2 font-normal tracking-normal text-fg-4">{note}</span>}
            </h2>
          ) : (
            <span />
          )}
          {action}
        </header>
      )}
      {children}
    </section>
  )
}
