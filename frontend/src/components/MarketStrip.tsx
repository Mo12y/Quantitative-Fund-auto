import { endpoints } from '../api/endpoints'
import { dirClass, dirOf, dirSymbol, fmtSignedPct, plainText } from '../lib/format'
import { useApi } from '../lib/useApi'
import { Card } from './Card'

/**
 * 市场小条 —— 指数盘中行情，**一行讲完**（2026-10-06 瘦身第 5 条）。
 *
 * ⚠️ 2026-10-16 T3：**消息面部分已删除**（随旧命题死路由一并移除）；
 * 本组件现只保留指数行情（它服务的是"市场现在什么状态"里的市场半句）。
 *
 * ⚠️ 两条**不能改**的语义（后端 `api_market_live` docstring，原文保留）：
 *  1. 行情**出网失败不是错误**：`ok:true` + `available:false` + `reason` 是正常降级。
 *     界面必须如实说"盘中行情不可用"，既不能报错、也不能拿旧值冒充实时。
 *  2. `lag_note` 是**时滞的量化**（"本地净值落后 N 天"）—— 必须显示。
 *     只说"可能偏旧"等于没说。
 *
 * 涨跌走 `dirOf`/`dirClass`/`dirSymbol`（涨红跌绿 + ▲▼ 符号，色盲可读）。
 */
export function MarketStrip({ className = '' }: { className?: string }) {
  const live = useApi(endpoints.marketLive)
  const d = live.data

  return (
    <Card className={className} title="市场" note="· 指数行情">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-body-sm">
        {/* ── 指数行情 ─────────────────────────────────────────────── */}
        {live.error ? (
          <span className="text-fg-2">行情读取失败：{live.error}</span>
        ) : !d ? (
          <span className="skeleton inline-block h-4 w-40 rounded-full" aria-hidden="true" />
        ) : !d.available ? (
          <span className="text-fg-3">指数盘中行情不可用：{plainText(d.reason) || '未知原因'}</span>
        ) : (
          <>
            <span className="text-fg-4">
              {d.trading ? '盘中' : '收盘'}
              {d.asof ? ` ${d.asof}` : ''}
            </span>
            {d.quotes.map((q) => {
              const dir = dirOf(q.pct)
              const price = q.price.toFixed(2)
              return (
                /* ⚠️ 2026-10-06 A3（用户拍板）：指数**点位**不再占可见数字位 ——
                   它挂 `title`（悬停可见）+ `aria-label`（读屏可读），只留涨跌% 在屏上。
                   理由：小条要回答的是"市场现在什么状态"，**涨跌% 是信号、点位是参考值**。
                   ⚠️ 已知代价：**移动端没有悬停**，点位在手机上等于看不到 ——
                   这是有意的取舍（用户 2026-10-06 明确选择），点位另有「数据状态」页的说明。 */
                <span
                  key={q.code}
                  className="inline-flex max-w-full items-baseline gap-1.5"
                  title={`${plainText(q.name)} ${price}（点位）`}
                  aria-label={`${plainText(q.name)} 收 ${price}`}
                >
                  <span className="truncate text-fg-3">{plainText(q.name)}</span>
                  <b className={'num shrink-0 ' + dirClass(dir)}>
                    {dirSymbol(dir)}
                    {q.pct == null ? '—' : fmtSignedPct(q.pct)}
                  </b>
                </span>
              )
            })}
          </>
        )}

        {/* 时滞量化（lag_note）—— 必须显示（见组件头注释） */}
        {d?.lag_note && <span className="text-caption text-fg-4">{plainText(d.lag_note)}</span>}
      </div>
    </Card>
  )
}
