import { SOURCE_LABEL } from '../api/client'

interface SourceTagProps {
  /** 数据来源（cache/snapshot/fresh），界面上必须如实标注 */
  source: string | null
  /** 数据截至（如净值日），有就显示 */
  asof?: string | null
  /** 重新计算（对应后端 ?fresh=1） */
  onRefresh?: () => void
  busy?: boolean
}

const CHIP =
  'inline-flex items-center gap-1.5 rounded-full border border-line bg-inset/70 px-2.5 py-[3px] text-[11px] text-fg-3'

/** 来源新鲜度的小圆点：刚重算=强调色，命中缓存=中性 */
function dot(source: string | null): string {
  return source === 'fresh' || source === 'live' ? 'bg-accent' : 'bg-fg-4'
}

export function SourceTag({ source, asof, onRefresh, busy }: SourceTagProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px]">
      {asof && (
        <span className={CHIP}>
          <span className="text-fg-4">数据截至</span>
          <span className="mono text-fg-2">{asof}</span>
        </span>
      )}
      {source && (
        <span className={CHIP}>
          <i className={'inline-block h-[6px] w-[6px] rounded-full ' + dot(source)} aria-hidden="true" />
          {SOURCE_LABEL[source] ?? source}
        </span>
      )}
      {onRefresh && (
        <button
          type="button"
          onClick={onRefresh}
          disabled={busy}
          className="rounded-full border border-line bg-inset/70 px-3 py-[3px] text-[11px] text-fg-3 transition-all duration-150 hover:border-line-strong hover:bg-card-hi hover:text-fg disabled:cursor-not-allowed disabled:opacity-45"
        >
          {busy ? '计算中…' : '重算'}
        </button>
      )}
    </div>
  )
}
