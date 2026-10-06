import type { ReactNode } from 'react'

export interface ConfirmField {
  label: string
  value: string
  /** 数值方向（涨/跌/平）—— 决定颜色，用户一眼能看出正负 */
  tone?: 'rise' | 'fall' | 'flat'
}

export interface ConfirmSpec {
  title: string
  /** 操作摘要（日期 / 基金 / 金额 / 预估份额 …）—— 逐行列出，别塞成一句话 */
  fields?: ConfirmField[]
  /** 常规提示（T+1 确认 / 15:00 后按下一交易日 …） */
  note?: string
  /** ⚠️ 高危提示：会**自己产生真实买入**的操作必须用它明示"将产生 N 笔、合计 ¥X" */
  warning?: string
  confirmText?: string
  /** 不可逆 / 影响面大的操作用危险色按钮 */
  danger?: boolean
}

function toneCls(tone?: ConfirmField['tone']): string {
  if (tone === 'rise') return 'text-rise'
  if (tone === 'fall') return 'text-fall'
  if (tone === 'flat') return 'text-flat'
  return 'text-fg'
}

/**
 * 写操作确认卡 —— **所有写账本的操作都要先过它**（用户 2026-10-05 定案）。
 *
 * 为什么不用原生 `confirm()`（旧前端就是那样）：原生只能给一行纯文本，
 * 而这里必须逐行展示"日期 / 基金 / 金额 / 预估份额"，高危操作还要单独一块警示
 * （"将产生 N 笔真实买入"）—— 原生对话框做不到，只能靠**没信息量的一句话**，
 * 用户按回车就过去了。
 *
 * ⚠️ 本卡只负责"展示 + 两个回调"，**不做请求** —— 请求放调用方（便于复用到多处）。
 */
export function ConfirmDialog({
  spec,
  busy = false,
  error,
  onConfirm,
  onCancel,
}: {
  spec: ConfirmSpec
  busy?: boolean
  error?: string | null
  onConfirm: () => void
  onCancel: () => void
}): ReactNode {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-canvas/75 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label={spec.title}
    >
      <div className="surface w-full max-w-[440px] rounded-[var(--radius-lg)] px-5 py-[18px]">
        {/* ⚠️ 用 div + ARIA 而不是 <h2>：`<h2>` 在本项目里是**卡片标题**（12px），
            模态标题要比它大（13.5px）—— 若也用 h2，就等于同一个标题层级有**两个字号**，
            破坏"层级单调"（B-0 判据的层级诊断会点出来）。语义靠 role 保留，无障碍不受影响。 */}
        <div role="heading" aria-level={2} className="text-body font-semibold text-fg">
          {spec.title}
        </div>

        {spec.fields && spec.fields.length > 0 && (
          <dl className="mt-3 flex flex-col gap-1.5">
            {spec.fields.map((f, i) => (
              <div key={i} className="flex items-baseline justify-between gap-3 text-body-sm">
                <dt className="shrink-0 text-fg-4">{f.label}</dt>
                <dd className={'num min-w-0 text-right ' + toneCls(f.tone)}>{f.value}</dd>
              </div>
            ))}
          </dl>
        )}

        {spec.warning && (
          <div className="mt-3 rounded-[var(--radius-md)] border border-warn/30 bg-warn/[.07] px-3 py-2 text-body-sm leading-relaxed text-warn">
            {spec.warning}
          </div>
        )}

        {spec.note && (
          <div className="mt-2 text-caption leading-relaxed text-fg-4">{spec.note}</div>
        )}

        {error && (
          <div className="mt-3 rounded-[var(--radius-md)] bg-inset px-3 py-2 text-body-sm leading-relaxed text-rise">
            {error}
          </div>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-md border border-line-strong px-3 py-1.5 text-body-sm text-fg-2 hover:text-fg disabled:opacity-45"
          >
            取消
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={
              'rounded-md px-3 py-1.5 text-body-sm font-semibold disabled:opacity-45 ' +
              (spec.danger
                ? 'bg-rise/15 text-rise hover:bg-rise/25'
                : 'bg-accent/15 text-accent hover:bg-accent/25')
            }
          >
            {busy ? '执行中…' : spec.confirmText || '确认执行'}
          </button>
        </div>
      </div>
    </div>
  )
}
