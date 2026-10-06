import { Icon } from './Icon'
import { ENTRIES, ENTRY_ICON, ENTRY_LABEL, type Entry } from '../lib/router'

/**
 * 左侧导航栏（`lg` 及以上；窄屏由 `Nav.tsx` 的顶部条接管）。
 *
 * 为什么只在大屏出现：212px 的侧栏在 1024px 以下会吃掉 20% 的可用宽度，
 * 表格和图表先遭殃 —— 所以断点设在 `lg`，两边各司其职。
 *
 * 导航项的数据（名称/图标）来自 `lib/router.ts`，与顶部条**同一份** ——
 * 只是呈现形式不同，不重复定义。
 *
 * ⚠️ 当前项不只靠颜色区分：药丸底 + 图标变色 + `aria-current="page"`（同涨跌的色盲约定）。
 */
export function Sidebar({ entry, onGo }: { entry: Entry; onGo: (e: Entry) => void }) {
  return (
    <aside className="sticky top-0 hidden h-screen w-[212px] shrink-0 flex-col border-r border-line bg-canvas/55 backdrop-blur-2xl lg:flex">
      {/* 品牌区 */}
      <div className="flex items-center gap-2.5 px-5 pb-4 pt-6">
        <i
          className="grad-brand inline-block h-[18px] w-[18px] shrink-0 rounded-[6px]"
          style={{ boxShadow: '0 0 16px -2px rgba(90,162,255,.75)' }}
          aria-hidden="true"
        />
        <span className="text-body font-semibold tracking-[-.01em] text-fg">量化基金</span>
      </div>

      <nav className="flex flex-col gap-1 px-3" aria-label="主导航">
        {ENTRIES.map((e) => {
          const on = e === entry
          return (
            <button
              key={e}
              type="button"
              onClick={() => onGo(e)}
              aria-current={on ? 'page' : undefined}
              className={
                'flex items-center gap-2.5 rounded-[var(--radius-md)] px-3 py-2 text-body transition-all duration-150 ' +
                (on
                  ? 'bg-card-hi text-fg shadow-[inset_0_1px_0_rgba(255,255,255,.07)]'
                  : 'text-fg-3 hover:bg-card hover:text-fg-2')
              }
            >
              <Icon name={ENTRY_ICON[e]} size={16} className={on ? 'text-accent' : ''} />
              {ENTRY_LABEL[e]}
            </button>
          )
        })}
      </nav>

      {/* 页脚：说清"数据在哪"。旧仪表盘入口已随 B-5 切换移除（它已不在 `/` 上）。 */}
      <div className="mt-auto border-t border-line px-5 py-4">
        <div className="text-micro leading-relaxed text-fg-4">
          本地账本 · 数据只在本机
          <br />
          data/fund_quant.db
        </div>
      </div>
    </aside>
  )
}
