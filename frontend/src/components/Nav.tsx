import { ENTRIES, ENTRY_LABEL, type Entry } from '../lib/router'

/**
 * 顶栏四入口导航（计划书 §6）。
 *
 * 视觉：**下边框**标位置，不用背景色块 —— 骨架（导航/标题/边框）保持中性，
 * 颜色只留给数据与状态（见 index.css 顶部 token 说明）。
 * 无障碍：当前项带 `aria-current="page"`，不只靠颜色区分。
 */
export function Nav({ entry, onGo }: { entry: Entry; onGo: (e: Entry) => void }) {
  return (
    <nav className="mb-5 flex flex-wrap items-center gap-x-1 gap-y-2 border-b border-line">
      <span className="mr-4 pb-2 text-[13px] font-medium text-fg-2">量化基金 · 个人账本</span>
      {ENTRIES.map((e) => {
        const on = e === entry
        return (
          <button
            key={e}
            type="button"
            onClick={() => onGo(e)}
            aria-current={on ? 'page' : undefined}
            className={
              '-mb-px border-b-2 px-3 pb-2 pt-1 text-[13.5px] transition-colors ' +
              (on ? 'border-accent text-fg' : 'border-transparent text-fg-3 hover:text-fg-2')
            }
          >
            {ENTRY_LABEL[e]}
          </button>
        )
      })}
    </nav>
  )
}
