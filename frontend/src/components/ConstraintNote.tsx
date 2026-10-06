import { useState } from 'react'
import type { ConstraintReview } from '../api/types'
import { plainText } from '../lib/format'

/**
 * 用户约束（B 层）摘要 —— 回答「为什么入选 / 为什么落选 / 缺什么没评估」三问。
 *
 * ⚠️ 两个必须说清的语义，否则会被读错：
 *  1. **池子是「只标注不剔除」**（后端 `mode: "annotate"`）—— 落选/未评估的基金
 *     **仍然在列表里**，只是带标注。文案不能说成"被过滤掉了"。
 *  2. **未评估 ≠ 通过**。算不出来的（缺数据/不在信号覆盖范围）单列一档，
 *     不能并进"通过"，否则是把"不知道"说成"没问题"。
 */
export function ConstraintNote({ review }: { review: ConstraintReview | null | undefined }) {
  const [open, setOpen] = useState(false)
  if (!review) return null

  const c = review.counts
  const applied = review.applied ?? []
  const dropped = review.dropped ?? []
  const skipped = review.skipped ?? []

  return (
    <div className="mb-3.5 rounded-[var(--radius-md)] border border-line bg-inset px-3.5 py-2.5 text-caption leading-relaxed">
      <div className="text-fg-2">
        用户约束（{applied.length} 条生效）：约束前 {c.before} → 通过{' '}
        <b className="num text-accent">{c.kept}</b> · 落选 <b className="num text-warn">{c.dropped}</b> · 未评估{' '}
        <b className="num text-fg-2">{c.skipped}</b>
        <span className="ml-1 text-fg-4">（池子只标注不剔除，落选与未评估的仍列在表中）</span>
      </div>

      {applied.length > 0 && (
        <div className="mt-1 text-fg-3">
          {applied.map((a) => plainText(a.description)).join('；')}
        </div>
      )}

      <div className="mt-1 text-fg-4">
        {[review.profile_note, review.overlap_note].filter(Boolean).map(plainText).join(' · ')}
      </div>

      {(dropped.length > 0 || skipped.length > 0) && (
        <>
          <button
            type="button"
            onClick={() => setOpen(!open)}
            className="mt-1.5 text-fg-3 underline decoration-dotted hover:text-fg-2"
          >
            {open ? '收起明细' : `看落选/未评估明细（${dropped.length + skipped.length} 条）`}
          </button>
          {open && (
            <div className="mt-1.5 space-y-1 border-t border-line pt-1.5">
              {dropped.map((d) => (
                <div key={'d' + d.code} className="text-fg-3">
                  <span className="text-warn">落选</span> {d.code} {plainText(d.name)} ——{' '}
                  {(d.reasons ?? []).map(plainText).join('；')}
                </div>
              ))}
              {skipped.map((s) => (
                <div key={'s' + s.code} className="text-fg-4">
                  <span>未评估</span> {s.code} {plainText(s.name)} ——{' '}
                  {(s.reasons ?? []).map(plainText).join('；')}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
