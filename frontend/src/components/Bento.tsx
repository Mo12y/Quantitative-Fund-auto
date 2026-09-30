import type { ReactNode } from 'react'

/**
 * Bento 栅格（12 栏）—— **`xl` 起生效，以下自动退回单列堆叠**。
 *
 * 为什么用它：窄屏没有横向空间，强行分栏只会把每块压瘦；到了宽屏，
 * 把竖着堆的卡片排成**面积不等的格子**，**格子的大小本身就表达重要性**
 * （主表 7 栏、辅表 5 栏），比"每块都占满一整行"的层级清楚得多。
 *
 * 用法：`<Bento>` 里放几张卡，每张卡用 `className={span(7)}` 声明占宽 ——
 * 不额外套容器 div（`Card` 本来就支持透传 `className`）。
 */
export function Bento({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={'flex flex-col gap-4 xl:grid xl:grid-cols-12 xl:items-start ' + className}>
      {children}
    </div>
  )
}

/** Tailwind 的类名必须是**字面量**才能被扫到，所以用查表而不是拼字符串 */
const SPAN: Record<number, string> = {
  3: 'xl:col-span-3',
  4: 'xl:col-span-4',
  5: 'xl:col-span-5',
  6: 'xl:col-span-6',
  7: 'xl:col-span-7',
  8: 'xl:col-span-8',
  9: 'xl:col-span-9',
  12: 'xl:col-span-12',
}

/**
 * 格子占宽（用作 `Card` / 面板组件的 `className`）。
 *
 * ⚠️ `min-w-0` 必须带：栅格子项默认 `min-width: auto`，宽表格会把格子**撑破**
 * （而不是出现横向滚动）。与 `App.tsx` 里 `flex-1` 必须配 `min-w-0` 是同一类坑。
 */
export function span(n: number): string {
  return 'min-w-0 ' + (SPAN[n] ?? SPAN[12])
}
