import { useState } from 'react'
import type { Explain, ExplainStage } from '../api/types'
import { plainText } from '../lib/format'

/** 明细行的语气色（tone 来自后端，只在这三个值里取） */
const TONE: Record<string, string> = {
  rise: 'text-rise',
  fall: 'text-fall',
  flat: 'text-fg-2',
}

/**
 * 数据链路下钻 —— 对齐「采集 → 清洗 → 特征 → 建模 → 评估」五段。
 * 五格只放 headline；点某格展开该格的明细行与**真实产物**（落库表 / 脚本 / 报告），
 * 让"这条结论怎么算出来的"可以一路指回真实文件，而不是一句装饰性说明。
 */
export function DrillDown({ explain }: { explain: Explain }) {
  const [active, setActive] = useState<string>(explain.stages[0]?.key ?? '')
  if (!explain.stages.length) return null
  const stage: ExplainStage | undefined = explain.stages.find((s) => s.key === active) ?? explain.stages[0]

  return (
    <details className="mt-3.5 border-t border-line pt-3" open>
      <summary className="cursor-pointer list-none text-[12.5px] text-fg-2">
        <span className="mr-1 text-fg-3">⌄</span>
        这条结论是怎么算出来的 · 数据链路
      </summary>

      <div className="mt-2.5 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line sm:grid-cols-5">
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
              <span className="block text-[11.5px] leading-relaxed text-fg-3">{plainText(s.headline)}</span>
            </button>
          )
        })}
      </div>

      {stage && (
        <div className="mt-2 rounded-md border border-line bg-inset px-3 py-2.5">
          <div className="mb-1.5 text-xs text-fg-3">{stage.title} · 这个数字从哪来</div>
          {stage.rows.map((r, i) => (
            <div
              key={i}
              className="flex flex-wrap items-baseline justify-between gap-x-5 gap-y-0.5 border-t border-line py-1.5 first:border-t-0 first:pt-0"
            >
              <span className="text-[12.5px] text-fg-3">{plainText(r.label)}</span>
              <span className={'text-right text-[12.5px] ' + (TONE[r.tone ?? 'flat'] ?? 'text-fg-2')}>
                {plainText(r.value)}
              </span>
            </div>
          ))}
          <div className="mt-2 border-t border-line pt-2 text-[11.5px] leading-relaxed text-fg-4">
            <div className="mb-0.5">真实产物</div>
            {stage.artifacts.map((a, i) => (
              <div key={i} className="flex flex-wrap gap-x-3">
                <span className="mono text-fg-3">{a.name}</span>
                <span>{plainText(a.detail)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </details>
  )
}