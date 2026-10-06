import { useState } from 'react'
import type { Explain, ExplainStage } from '../api/types'
import { plainText } from '../lib/format'

/** 明细行的语气色（tone 来自后端，只在这三个值里取） */
const TONE: Record<string, string> = {
  rise: 'text-rise',
  fall: 'text-fall',
  flat: 'text-fg-2',
}

/** 把明细行均分成两栏（奇数时上栏多一行）；栏数只影响排版，不改变顺序语义。 */
function splitCols<T>(rows: T[]): T[][] {
  if (rows.length < 2) return [rows]
  const half = Math.ceil(rows.length / 2)
  return [rows.slice(0, half), rows.slice(half)]
}

/**
 * 数据链路下钻 —— 对齐「采集 → 清洗 → 特征 → 建模 → 评估」五段。
 * 五格只放 headline；点某格展开该格的明细行与**真实产物**（落库表 / 脚本 / 报告），
 * 让"这条结论怎么算出来的"可以一路指回真实文件，而不是一句装饰性说明。
 *
 * ⚠️ 2026-10-06（瘦身第 3 条）：**默认折叠**（原为 `<details open>`）。
 *   依据 DESIGN §5.1 / §5.3：「审计 / 溯源 / 口径 / 命令表一律默认折叠，且不得出现在第一屏」。
 *   此前它挂在「今天」页的**结论卡内**且默认展开 —— 于是"这条结论怎么算出来的"
 *   占据了整屏第一块的位置，把真正该看的 KPI 挤了下去。现只出现在「设置」（与「研究」）。
 *
 * ⚠️ 同一轮：内部栅格改用**容器查询**（`@container` / `@2xl:`）而不是视口断点。
 *   它现在既能挂在「研究」页的整宽卡（1188px，五段并排），也能挂在「设置」页
 *   5 栏窄列（约 486px，两列堆叠）—— 视口断点看不出"我被放在多宽的容器里"，
 *   在窄列里会硬挤成 5 列（每格 ~66px 文字宽，标题竖成柱）。
 */
export function DrillDown({ explain }: { explain: Explain }) {
  const [active, setActive] = useState<string>(explain.stages[0]?.key ?? '')
  if (!explain.stages.length) return null
  const stage: ExplainStage | undefined = explain.stages.find((s) => s.key === active) ?? explain.stages[0]

  return (
    <details className="@container">
      <summary className="cursor-pointer list-none text-body-sm text-fg-2">
        <span className="mr-1 text-fg-3">⌄</span>
        这条结论是怎么算出来的 · 数据链路
      </summary>

      <div className="mt-2.5 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line @2xl:grid-cols-5">
        {explain.stages.map((s) => {
          const on = s.key === active
          return (
            <button
              key={s.key}
              type="button"
              onClick={() => setActive(s.key)}
              className={'px-3 py-2.5 text-left transition-colors ' + (on ? 'bg-inset' : 'bg-card hover:bg-inset')}
            >
              <b className={'mb-1 block text-xs font-medium ' + (on ? 'text-accent' : 'text-fg-2')}>{s.title}</b>
              <span className="block text-caption leading-relaxed text-fg-3">{plainText(s.headline)}</span>
            </button>
          )
        })}
      </div>

      {stage && (
        /* 明细行是"标签 —— 数值"的对照：
           ⚠️ 早先按 `justify-between` 单列铺满 —— 卡片放宽到 1300px 后两端相距太远、
           眼睛无法跟踪（行长问题），右侧还空出一大块。改为**两栏**：
           每栏 ~600px，既收住行长又把宽度用满；分栏线用 per-column 的 divide-y，
           不会出现 2 栏网格里"右栏第一行多一条上边线"的破绽。 */
        <div className="mt-2 rounded-[var(--radius-md)] border border-line bg-inset px-3.5 py-2.5">
          <div className="mb-1.5 text-xs text-fg-3">{stage.title} · 这个数字从哪来</div>
          <div className="grid gap-x-10 @2xl:grid-cols-2">
            {splitCols(stage.rows).map((col, ci) => (
              <div key={ci} className="divide-y divide-line">
                {col.map((r, i) => (
                  <div
                    key={i}
                    className="flex flex-wrap items-baseline justify-between gap-x-5 gap-y-0.5 py-1.5"
                  >
                    <span className="text-body-sm text-fg-3">{plainText(r.label)}</span>
                    <span className={'text-right text-body-sm ' + (TONE[r.tone ?? 'flat'] ?? 'text-fg-2')}>
                      {plainText(r.value)}
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div className="mt-2 border-t border-line pt-2 text-caption leading-relaxed text-fg-4">
            <div className="mb-0.5">真实产物</div>
            <div className="grid gap-x-10 @2xl:grid-cols-2">
              {stage.artifacts.map((a, i) => (
                <div key={i} className="flex flex-wrap gap-x-3">
                  <span className="mono text-fg-3">{a.name}</span>
                  <span>{plainText(a.detail)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </details>
  )
}