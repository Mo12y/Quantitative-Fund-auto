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

const TAG = 'rounded-full border border-line bg-inset px-2 py-px text-[11.5px] text-fg-2'

export function SourceTag({ source, asof, onRefresh, busy }: SourceTagProps) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-fg-3">
      {asof && <span className={TAG}>数据截至 {asof}</span>}
      {source && <span className={TAG}>{SOURCE_LABEL[source] ?? source}</span>}
      {onRefresh && (
        <button
          type="button"
          onClick={onRefresh}
          disabled={busy}
          className="rounded-md border border-line bg-inset px-2.5 py-0.5 text-[11.5px] text-fg-2 transition-colors hover:border-line-strong hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? '计算中…' : '重算'}
        </button>
      )}
    </div>
  )
}