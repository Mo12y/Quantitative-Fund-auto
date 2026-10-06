import { Icon } from './Icon'
import { ENTRIES, ENTRY_ICON, ENTRY_LABEL, type Entry } from '../lib/router'

/**
 * 顶部导航条 —— **只在 `lg` 以下出现**（`lg` 及以上由 `Sidebar.tsx` 接管）。
 *
 * 为什么保留两条：212px 的侧栏在 1024px 以下会吃掉 20% 可用宽度，表格/图表先遭殃。
 * 断点设在 `lg`，两侧各司其职；导航项数据同源于 `lib/router.ts`，不重复定义。
 *
 * 视觉：吸顶 + 毛玻璃 + 药丸态 + 图标。当前项用「药丸底 + 亮字 + 强调色图标」，
 * 同时带 `aria-current="page"` —— 不只靠颜色区分（同涨跌的色盲约定一致）。
 */
export function Nav({ entry, onGo }: { entry: Entry; onGo: (e: Entry) => void }) {
  return (
    <div className="sticky top-0 z-30 border-b border-line bg-canvas/55 backdrop-blur-2xl lg:hidden">
      {/* 导航底部的极光细线：把"吸顶条"和内容分开，同时给品牌区一点颜色 */}
      <i
        className="pointer-events-none absolute inset-x-0 bottom-0 h-px opacity-60"
        style={{
          background:
            'linear-gradient(90deg, transparent, rgba(90,162,255,.55) 22%, rgba(139,108,255,.55) 62%, transparent)',
        }}
        aria-hidden="true"
      />
      <nav className="relative mx-auto flex max-w-[1080px] flex-wrap items-center gap-x-2 gap-y-1 px-4">
        <span className="mr-3 flex items-center gap-2.5 py-3.5 text-body font-semibold text-fg">
          <i
            className="grad-brand inline-block h-[17px] w-[17px] shrink-0 rounded-[6px]"
            style={{ boxShadow: '0 0 16px -2px rgba(90,162,255,.75)' }}
            aria-hidden="true"
          />
          量化基金
          <span className="text-caption font-normal text-fg-4">个人账本</span>
        </span>

        <div className="flex flex-wrap items-center gap-1 py-2">
          {ENTRIES.map((e) => {
            const on = e === entry
            return (
              <button
                key={e}
                type="button"
                onClick={() => onGo(e)}
                aria-current={on ? 'page' : undefined}
                className={
                  'inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-body transition-all duration-150 ' +
                  (on
                    ? 'bg-card-hi text-fg shadow-[inset_0_1px_0_rgba(255,255,255,.08),0_8px_22px_-14px_rgba(0,0,0,.95)]'
                    : 'text-fg-3 hover:bg-card hover:text-fg-2')
                }
              >
                <Icon name={ENTRY_ICON[e]} size={15} className={on ? 'text-accent' : ''} />
                {ENTRY_LABEL[e]}
              </button>
            )
          })}
        </div>
      </nav>
    </div>
  )
}
