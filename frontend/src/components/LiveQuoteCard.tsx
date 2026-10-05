import { endpoints } from '../api/endpoints'
import { Card } from './Card'
import { Skeleton } from './Skeleton'
import { dirClass, dirOf, dirSymbol, fmtSignedPct, plainText } from '../lib/format'
import { useApi } from '../lib/useApi'

/**
 * 指数盘中快照 + 本地净值时滞（`/api/market/live`）—— 旧前端 `loadLiveQuote()` 的等价物。
 *
 * ⚠️ 两条**不能改**的语义（后端 `api_market_live` docstring）：
 *  1. **出网失败不是错误**：后端返回 `ok:true` + `available:false` + `reason`。
 *     界面必须如实说"盘中行情不可用"，既不能报错、也不能拿旧值冒充实时。
 *  2. `lag_note` 是**时滞的量化**（"本地净值落后 N 天"）—— 必须显示。
 *     只说"可能偏旧"等于没说（这正是数据源扩展计划书 阶段 5 要解决的问题）。
 *
 * 涨跌走 `dirOf`/`dirClass`/`dirSymbol`（涨红跌绿 + ▲▼ 符号，色盲可读）。
 */
export function LiveQuoteCard({ className = '' }: { className?: string }) {
  const st = useApi(endpoints.marketLive)
  const d = st.data

  return (
    <Card className={className} title="指数行情" note="· 盘中快照">
      {st.error ? (
        <div className="text-[12.5px] text-fg-2">读取失败：{st.error}</div>
      ) : !d ? (
        <Skeleton lines={3} />
      ) : !d.available ? (
        /* 降级路径：**不是错误**，如实说不可用 + 给原因 + 给时滞 */
        <div className="rounded-[var(--radius-md)] bg-inset px-3 py-2.5">
          <div className="text-[12.5px] leading-relaxed text-fg-3">
            指数盘中行情不可用：{plainText(d.reason) || '未知原因'}
          </div>
          {d.lag_note ? (
            <div className="mt-1.5 text-[11px] leading-relaxed text-fg-4">{plainText(d.lag_note)}</div>
          ) : null}
        </div>
      ) : (
        <>
          <div className="text-[11.5px] text-fg-4">
            {d.trading ? '盘中' : '非交易时段（为最近收盘）'}
            {d.asof ? ` · 截至 ${d.asof}` : ''}
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {d.quotes.map((q) => {
              const dir = dirOf(q.pct)
              return (
                <span
                  key={q.code}
                  /* ⚠️ `max-w-full` + 名称 `truncate`：行情 chip 是 `inline-flex`（内部不换行），
                     窄屏下长指数名会把卡片撑破（375 视口实测溢出 2px）。 */
                  className="inline-flex max-w-full items-baseline gap-1.5 rounded-full border border-line bg-inset/70 px-2.5 py-1 text-[11.5px]"
                >
                  <span className="truncate text-fg-3">{plainText(q.name)}</span>
                  <b className={'num shrink-0 ' + dirClass(dir)}>
                    {dirSymbol(dir)}
                    {q.pct == null ? '—' : fmtSignedPct(q.pct)}
                  </b>
                  <span className="num shrink-0 text-fg-2">{q.price.toFixed(2)}</span>
                </span>
              )
            })}
          </div>
          {d.lag_note ? (
            <div className="mt-2 text-[11px] leading-relaxed text-fg-4">{plainText(d.lag_note)}</div>
          ) : null}
        </>
      )}
    </Card>
  )
}
