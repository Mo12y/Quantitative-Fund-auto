import { ENTRIES, ENTRY_LABEL, type Entry } from '../lib/router'

/**
 * 顶栏四入口导航（计划书 §6）。
 *
 * 视觉：**吸顶 + 毛玻璃 + 药丸态**。当前项用「药丸底 + 亮字」，同时带 `aria-current="page"` ——
 * 不只靠颜色区分（同涨跌的色盲约定一致）。
 * 品牌标用主蓝→次紫渐变小方块，作为整站唯一"装饰性"元素。
 */
export function Nav({ entry, onGo }: { entry: Entry; onGo: (e: Entry) => void }) {
  return (
    <div className="sticky top-0 z-30 border-b border-line bg-canvas/72 backdrop-blur-xl">
      <nav className="mx-auto flex max-w-[1080px] flex-wrap items-center gap-x-2 gap-y-1 px-4">
        <span className="mr-3 flex items-center gap-2 py-3.5 text-[13.5px] font-semibold text-fg">
          <i
            className="inline-block h-[14px] w-[14px] shrink-0 rounded-[5px]"
            style={{ background: 'linear-gradient(135deg, var(--color-accent), var(--color-accent-2))' }}
            aria-hidden="true"
          />
          量化基金
          <span className="text-[11.5px] font-normal text-fg-4">个人账本</span>
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
                  'rounded-full px-3.5 py-1.5 text-[13px] transition-all duration-150 ' +
                  (on
                    ? 'bg-card-hi text-fg shadow-[inset_0_1px_0_rgba(255,255,255,.07),0_6px_18px_-12px_rgba(0,0,0,.9)]'
                    : 'text-fg-3 hover:bg-card hover:text-fg-2')
                }
              >
                {ENTRY_LABEL[e]}
              </button>
            )
          })}
        </div>
      </nav>
    </div>
  )
}
