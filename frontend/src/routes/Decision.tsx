import type { ReactNode } from 'react'
import { endpoints } from '../api/endpoints'
import type {
  Behavior,
  Breakeven,
  CfCase,
  Counterfactual,
  ExclusionPayload,
  PeerFund,
  PeerPayload,
  RiskPayload,
} from '../api/types'
import { Bento, span } from '../components/Bento'
import { Card } from '../components/Card'
import { MarketStrip } from '../components/MarketStrip'
import { PageHead } from '../components/PageHead'
import { QuantPanel } from '../components/QuantPanel'
import { Skeleton } from '../components/Skeleton'
import { SourceTag } from '../components/SourceTag'
import { Warming } from '../components/Warming'
import { dirClass, dirOf, dirSymbol, fmtMoney, fmtPct, fmtSignedPct, plainText } from '../lib/format'
import { useApi } from '../lib/useApi'

/**
 * 决策台（**首屏**）—— 回答"我最近的判断质量如何、证据是什么"。
 *
 * 布局（计划书 §4.1）：
 *   一等卡（全宽）：行为镜 → 反事实 → 费率门槛；二等卡（Bento 2×2）：风险 / 排雷 / 分位 / 研究快照。
 *
 * ⚠️ 全线纪律：**不给"该买什么"的结论** —— 系统只把度量、对照、证据递到手边
 * （契约 §2.3：行为镜+反事实=一等；四信号=二等上下文；用户自己下判断）。
 * ⚠️ 一切"样本不足 / 数据缺失"的声明（`sample.sufficient` / `insufficient`）**原样展示**，
 * 不许把"没算出来"渲染成"没问题"。
 */

/** 小徽标（灰 / 警示 / 红 三档） */
function Badge({ tone, children }: { tone: 'flat' | 'warn' | 'rise'; children: string }) {
  const cls =
    tone === 'warn'
      ? 'text-warn border-warn/35 bg-warn/10'
      : tone === 'rise'
        ? 'text-rise border-rise/30 bg-rise/10'
        : 'text-fg-3 border-line-strong bg-inset'
  return <span className={'shrink-0 rounded-full border px-2.5 py-0.5 text-body-sm ' + cls}>{children}</span>
}

/** KPI 小格：字段名 + 数值 + 说明（说明必须写口径 —— DESIGN §5.5） */
function Kpi({ label, value, sub, cls = '' }: { label: string; value: string; sub?: string; cls?: string }) {
  return (
    <div className="min-w-0">
      <div className="field">{label}</div>
      <div className={'num text-metric font-semibold ' + (cls || 'text-fg')}>{value}</div>
      {sub && <div className="mt-0.5 text-caption leading-relaxed text-fg-4">{sub}</div>}
    </div>
  )
}

/** 分区块的兜底：失败说原因、等待给骨架（都不许白屏）。 */
function fallback(title: string, st: { error: string | null }, className = ''): ReactNode {
  return (
    <Card className={className} title={title}>
      {st.error ? (
        <div className="text-sm text-fg-2">读取失败：{st.error}</div>
      ) : (
        <Skeleton lines={4} />
      )}
    </Card>
  )
}

/* ── 一等卡 ① 行为镜 ──────────────────────────────────────────── */

function BehaviorCard({ b }: { b: Behavior }) {
  if (b.error) {
    return (
      <Card lead title="行为镜">
        <div className="text-body text-fg-3">{plainText(b.error)}</div>
      </Card>
    )
  }
  const s = b.sample
  const ch = b.buy.chase_spread
  const loss = b.sell.frac_loss
  const early = b.sell.frac_sold_too_early
  return (
    <Card lead title="行为镜" note={`· ${b.span} · 同期${b.benchmark_name} ${b.benchmark_pct == null ? '—' : fmtSignedPct(b.benchmark_pct)}`}>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
        <Badge tone={s.sufficient ? 'flat' : 'warn'}>{s.sufficient ? '样本可用' : '样本不足'}</Badge>
        <span className="text-metric font-medium leading-snug tracking-[-.01em] text-fg">
          {plainText(b.verdict)}
        </span>
      </div>
      {/* ⚠️ 样本不足的完整声明**原样展示**（不许截断成"样本不足"四个字 —— 要能看清为什么） */}
      {!s.sufficient && (
        <div className="mt-2 rounded-[var(--radius-md)] bg-inset px-3.5 py-2.5 text-body-sm leading-relaxed text-fg-3">
          {plainText(s.warning)}
        </div>
      )}

      <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
        <Kpi
          label="追高对照"
          value={ch == null ? '—' : `${ch > 0 ? '+' : ''}${ch.toFixed(2)} pp`}
          cls={ch != null && ch > 0.5 ? 'text-warn' : ''}
          sub={`买入前 20 日中位 ${b.buy.median_trail20?.toFixed(2) ?? '—'}% vs 同基金平常 ${b.buy.median_trail20_baseline?.toFixed(2) ?? '—'}%；正 = 买得比平常更"涨过"`}
        />
        <Kpi
          label="亏损卖出"
          value={b.sell.n_lots ? `${b.sell.n_loss} / ${b.sell.n_lots}` : '—'}
          sub={`已卖批次中亏着卖的占比 ${loss == null ? '—' : fmtPct(loss * 100)}；实现收益中位 ${b.sell.median_ret_pct?.toFixed(2) ?? '—'}%`}
        />
        <Kpi
          label="持有期中位"
          value={b.frequency.median_held_days == null ? '—' : `${b.frequency.median_held_days.toFixed(0)} 天`}
          sub={`<7 天惩罚期卖出 ${b.frequency.n_under_7d} 笔；${b.frequency.buys_per_month.toFixed(1)} 买/月、${b.frequency.sells_per_month.toFixed(1)} 卖/月`}
        />
        <Kpi
          label="卖后又涨"
          value={
            b.sell.n_with_followup
              ? `${b.sell.n_sold_too_early} / ${b.sell.n_with_followup}`
              : '—'
          }
          sub={`可算批次中 ${early == null ? '—' : fmtPct(early * 100)} 在卖出 20 日后继续上涨（按决策日中位 ${b.sell.median_after20_pct_by_date?.toFixed(2) ?? '—'}%）`}
        />
      </div>

      {/* 读法纪律与明细 —— 默认折叠（DESIGN §5.3；正文已把四个可观测量讲完） */}
      <details className="mt-3.5 border-t border-line pt-2.5">
        <summary className="cursor-pointer list-none text-body-sm text-fg-2">
          <span className="mr-1 text-fg-3">⌄</span>
          读法纪律与逐只明细（默认折叠）
        </summary>
        <div className="mt-2 flex flex-col gap-1.5">
          {b.notes.map((n, i) => (
            <div key={i} className="text-caption leading-relaxed text-fg-4">{plainText(n)}</div>
          ))}
        </div>
        <table className="mt-3 w-full border-collapse">
          <thead>
            <tr>
              <th className="field pb-2 text-left">基金</th>
              <th className="field pb-2 text-right">买入笔数</th>
              <th className="field pb-2 text-right">追高差 (pp)</th>
              <th className="field pb-2 text-right">分位差 (pp)</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(b.buy.per_fund).map(([code, f]) => (
              <tr key={code} className="border-t border-line">
                <td className="mono py-1.5 pr-3 text-body-sm text-fg-2">{code}</td>
                <td className="num py-1.5 text-right text-body-sm text-fg-2">{f.n_buys}</td>
                <td className="num py-1.5 text-right text-body-sm text-fg-2">
                  {f.spread_trail20 == null ? '—' : f.spread_trail20.toFixed(2)}
                </td>
                <td className="num py-1.5 text-right text-body-sm text-fg-2">
                  {f.spread_pct == null ? '—' : f.spread_pct.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </Card>
  )
}

/* ── 一等卡 ② 反事实归因 ──────────────────────────────────────── */

/** 情形四行表：只能比 XIRR（后端 notes 原文），金额并列只为可读性。 */
function CfTable({ cases }: { cases: { actual: CfCase; hold_all: CfCase; dca_only: CfCase; temp_tilt: CfCase } }) {
  const rows: CfCase[] = [cases.actual, cases.hold_all, cases.dca_only, cases.temp_tilt]
  return (
    <table className="w-full border-collapse">
      <thead>
        <tr>
          <th className="field pb-2 text-left">情形</th>
          <th className="field pb-2 text-right">投入</th>
          <th className="field pb-2 text-right">期末市值</th>
          <th className="field pb-2 text-right">XIRR（年化）</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((c, i) => (
          <tr key={i} className={'border-t border-line ' + (i === 0 ? 'bg-inset/60' : '')}>
            <td className="py-2 pr-3 text-body-sm text-fg-2">{plainText(c.label)}</td>
            <td className="num py-2 text-right text-body-sm text-fg-3">{fmtMoney(c.invested)}</td>
            <td className="num py-2 text-right text-body-sm text-fg-3">{fmtMoney(c.end_value)}</td>
            <td className={'num py-2 text-right text-body-sm font-medium ' + dirClass(dirOf(c.xirr_pct))}>
              {dirSymbol(dirOf(c.xirr_pct))}
              {fmtSignedPct(c.xirr_pct)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function CounterfactualCard({ c }: { c: Counterfactual }) {
  const cmp = 'error' in c.compare ? null : c.compare
  if (!cmp) {
    return (
      <Card title="反事实归因">
        <div className="text-body text-fg-3">
          {'error' in c.compare ? plainText(c.compare.error) : '暂不可用'}
        </div>
      </Card>
    )
  }
  const sf = 'error' in c.same_fund ? null : c.same_fund
  const sr = 'error' in c.sell_rules ? null : c.sell_rules
  return (
    <Card
      title="反事实归因"
      note={`· 如果当时不动 · ${cmp.span} · 同期${cmp.benchmark_name} ${cmp.benchmark_pct == null ? '—' : fmtSignedPct(cmp.benchmark_pct)}`}
    >
      <CfTable cases={cmp.cases} />
      <div className="mt-2 text-caption leading-relaxed text-fg-4">
        四个情形投入总额不同，只能比 XIRR，不能比盈亏额。{plainText(cmp.notes[3] ?? '')}
      </div>

      {/* 同标的（锁死基金与金额，只变买入时机）与卖出规则 —— 都是"证据细节"，默认折叠 */}
      <details className="mt-3 border-t border-line pt-2.5">
        <summary className="cursor-pointer list-none text-body-sm text-fg-2">
          <span className="mr-1 text-fg-3">⌄</span>
          同标的换时机（{sf ? sf.summary.n_funds : '—'} 只）· 卖出规则对比（默认折叠）
        </summary>
        {sf && (
          <>
            <table className="mt-2.5 w-full border-collapse">
              <thead>
                <tr>
                  <th className="field pb-2 text-left">基金</th>
                  <th className="field pb-2 text-right">笔数</th>
                  <th className="field pb-2 text-right">实际 XIRR</th>
                  <th className="field pb-2 text-right">一次买</th>
                  <th className="field pb-2 text-right">等额定投</th>
                </tr>
              </thead>
              <tbody>
                {sf.funds.map((f) => (
                  <tr key={f.code} className="border-t border-line">
                    <td className="py-2 pr-3 text-body-sm">
                      <span className="text-fg-2">{plainText(f.name)}</span>
                      <span className="mono ml-1.5 text-caption text-fg-4">{f.code}</span>
                    </td>
                    <td className="num py-2 text-right text-body-sm text-fg-3">{f.n_buys}</td>
                    <td className={'num py-2 text-right text-body-sm ' + dirClass(dirOf(f.actual.xirr_pct))}>
                      {fmtSignedPct(f.actual.xirr_pct)}
                    </td>
                    <td className="num py-2 text-right text-body-sm text-fg-3">
                      {f.vs_lump == null ? '—' : `${f.vs_lump > 0 ? '+' : ''}${f.vs_lump.toFixed(2)}pp`}
                    </td>
                    <td className="num py-2 text-right text-body-sm text-fg-3">
                      {f.vs_even_dca == null ? '—' : `${f.vs_even_dca > 0 ? '+' : ''}${f.vs_even_dca.toFixed(2)}pp`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="mt-2 text-caption leading-relaxed text-fg-4">
              后两列 = 实际 − 对照（pp）：正 = 你的时机比对照好；合计
              vs 一次买 中位 {sf.summary.vs_lump.median == null ? '—' : sf.summary.vs_lump.median.toFixed(2)}pp、
              vs 等额定投 中位 {sf.summary.vs_even_dca.median == null ? '—' : sf.summary.vs_even_dca.median.toFixed(2)}pp
              （n={sf.summary.n_funds}，样本很小，结论不可推广）。
            </div>
          </>
        )}
        {sr && (
          <table className="mt-3 w-full border-collapse">
            <thead>
              <tr>
                <th className="field pb-2 text-left">卖出规则（冻结买入）</th>
                <th className="field pb-2 text-right">卖出笔数</th>
                <th className="field pb-2 text-right">XIRR</th>
              </tr>
            </thead>
            <tbody>
              {sr.results.map((r) => (
                <tr key={r.key} className="border-t border-line">
                  <td className="py-2 pr-3 text-body-sm text-fg-2">{plainText(r.label)}</td>
                  <td className="num py-2 text-right text-body-sm text-fg-3">
                    {r.n_sold} / {r.n_buys}
                  </td>
                  <td className={'num py-2 text-right text-body-sm ' + dirClass(dirOf(r.xirr_pct))}>
                    {fmtSignedPct(r.xirr_pct)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </details>
    </Card>
  )
}

/* ── 一等卡 ③ 费率门槛 ────────────────────────────────────────── */

function BreakevenCard({ b }: { b: Breakeven }) {
  if (b.error) {
    return (
      <Card title="费率门槛">
        <div className="text-body text-fg-3">{plainText(b.error)}</div>
      </Card>
    )
  }
  const nf = b.next_free ?? []
  return (
    <Card title="费率门槛" note="· 每笔要涨多少才不亏">
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
        <Kpi
          label="惩罚期内持仓"
          value={`${b.n_in_penalty} 笔`}
          cls={b.n_in_penalty > 0 ? 'text-warn' : ''}
          sub={b.n_in_penalty > 0 ? `市值 ${fmtMoney(b.penalty_value)} · 现在赎回约 ¥${b.penalty_cost.toFixed(2)} 罚金` : '全部已过 7 天惩罚期'}
        />
        <Kpi label="持仓批次" value={`${b.n_lots} 笔`} sub={`合计市值 ${fmtMoney(b.total_market_value)}`} />
        <Kpi
          label="现在全赎回成本"
          value={`¥${b.total_redeem_cost.toFixed(2)}`}
          sub="按已声明的赎回费档估算（仅 <7 天档计费，中档偏乐观）"
        />
        <Kpi
          label="最近可免费赎回"
          value={nf.length ? `${nf[0].days_to_free} 天后` : '—'}
          sub={nf.length ? `${plainText(nf[0].fund_name)} · ${nf[0].free_date}` : '暂无待解锁批次'}
        />
      </div>

      <details className="mt-3.5 border-t border-line pt-2.5">
        <summary className="cursor-pointer list-none text-body-sm text-fg-2">
          <span className="mr-1 text-fg-3">⌄</span>
          逐笔门槛 · {b.n_lots} 笔（默认折叠）
        </summary>
        <table className="mt-2 w-full border-collapse">
          <thead>
            <tr>
              <th className="field pb-2 text-left">基金</th>
              <th className="field pb-2 text-right">持有</th>
              <th className="field pb-2 text-right">门槛</th>
              <th className="field pb-2 text-right">扣费后</th>
            </tr>
          </thead>
          <tbody>
            {b.lots.map((l, i) => (
              <tr key={i} className="border-t border-line">
                <td className="py-1.5 pr-3 text-body-sm">
                  <span className="text-fg-2">{plainText(l.fund_name)}</span>
                  <span className="mono ml-1.5 text-caption text-fg-4">{l.fund_code}</span>
                  {l.in_penalty && <Badge tone="warn">惩罚期内</Badge>}
                </td>
                <td className="num py-1.5 text-right text-body-sm text-fg-3">{l.held_days} 天</td>
                <td className="num py-1.5 text-right text-body-sm text-fg-3">{l.threshold_pct.toFixed(2)}%</td>
                <td className={'num py-1.5 text-right text-body-sm ' + dirClass(dirOf(l.net_if_sell_pct))}>
                  {l.net_if_sell_pct == null ? '—' : fmtSignedPct(l.net_if_sell_pct)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {b.notes.map((n, i) => (
          <div key={i} className="mt-1.5 text-caption leading-relaxed text-fg-4">{plainText(n)}</div>
        ))}
      </details>
    </Card>
  )
}

/* ── 二等卡 ④ 风险 ────────────────────────────────────────────── */

function RiskCard({ d }: { d: RiskPayload }) {
  if (d.error) return <Card title="风险刻画"><div className="text-body text-fg-3">{plainText(d.error)}</div></Card>
  return (
    <Card title="风险刻画" note="· 近 3 年窗口（点不足的显式声明）">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className="field pb-2 text-left">基金</th>
            <th className="field pb-2 text-right">年化收益</th>
            <th className="field pb-2 text-right">年化波动</th>
            <th className="field pb-2 text-right">近1年回撤</th>
            <th className="field pb-2 text-right">夏普</th>
          </tr>
        </thead>
        <tbody>
          {d.funds.map((f) => (
            <tr key={f.fund_code} className="border-t border-line">
              <td className="py-2 pr-3 text-body-sm">
                <span className="text-fg-2">{plainText(f.fund_name)}</span>
                <span className="mono ml-1.5 text-caption text-fg-4">{f.fund_code}</span>
              </td>
              {f.insufficient || !f.metrics ? (
                <td colSpan={4} className="py-2 text-right text-body-sm text-fg-4">
                  {plainText(f.reason ?? '数据不足')}
                </td>
              ) : (
                <>
                  <td className={'num py-2 text-right text-body-sm ' + dirClass(dirOf(f.metrics.annual_return))}>
                    {fmtSignedPct(f.metrics.annual_return)}
                  </td>
                  <td className="num py-2 text-right text-body-sm text-fg-3">{f.metrics.ann_vol.toFixed(1)}%</td>
                  <td className="num py-2 text-right text-body-sm text-fg-3">{f.metrics.max_drawdown_1y.toFixed(1)}%</td>
                  <td className="num py-2 text-right text-body-sm text-fg-2">{f.metrics.sharpe.toFixed(2)}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 text-caption leading-relaxed text-fg-4">
        窗口 {d.funds[0]?.metrics?.window_points ?? '—'} 个交易日口径；回撤取近 1 年。
        本卡只做风险刻画，不含任何收益预测。
      </div>
    </Card>
  )
}

/* ── 二等卡 ⑤ 排雷 ────────────────────────────────────────────── */

function ExclusionCard({ d }: { d: ExclusionPayload }) {
  if (d.error) return <Card className={span(5)} title="排雷"><div className="text-body text-fg-3">{plainText(d.error)}</div></Card>
  return (
    <Card className={span(5)} title="排雷" note="· 同类夏普垫底 / TER 偏高">
      {d.excluded.length > 0 ? (
        <div className="flex flex-col gap-2">
          {d.excluded.map((f) => (
            <div key={f.fund_code} className="rounded-[var(--radius-md)] bg-inset px-3.5 py-2.5 text-body-sm">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <Badge tone="warn">触雷</Badge>
                <span className="text-fg">{plainText(f.fund_name)}</span>
                <span className="mono text-caption text-fg-4">{f.fund_code}</span>
              </div>
              <div className="mt-1 text-caption leading-relaxed text-fg-3">{f.reasons.join('；')}</div>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-[var(--radius-md)] bg-inset px-3.5 py-2.5 text-body-sm text-fg-2">
          当前持仓中没有触雷的基金。
        </div>
      )}
      <div className="mt-2.5 text-caption leading-relaxed text-fg-4">
        其余 {d.clean.length} 只未触雷{d.unrated.length > 0 && `；${d.unrated.length} 只无法判定（判据数据不可用，不视为通过）`}。
        {d.unrated.length > 0 && (
          <>
            {' '}无法判定：{d.unrated.map((u) => `${u.fund_code}（${plainText(u.reason)}）`).join('、')}。
          </>
        )}
        {d.unrated.length === 0 && '判据：同类夏普分位 ≤10 或 TER 分位 ≥90（原始分位口径）。'}
      </div>
    </Card>
  )
}

/* ── 二等卡 ⑥ 分位 ────────────────────────────────────────────── */

function pctCell(node: PeerFund['sharpe']) {
  if (!node || node.insufficient_data) {
    return <span className="text-fg-4" title={node?.reason ?? ''}>—</span>
  }
  return <>{node.percentile == null ? '—' : node.percentile.toFixed(0)}</>
}

function PeerCard({ d }: { d: PeerPayload }) {
  if (d.error) return <Card title="同类分位"><div className="text-body text-fg-3">{plainText(d.error)}</div></Card>
  return (
    <Card title="同类分位" note="· 原始分位（越大 = 值越大）">
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className="field pb-2 text-left">基金</th>
            <th className="field pb-2 text-left">组</th>
            <th className="field pb-2 text-right">夏普</th>
            <th className="field pb-2 text-right">回撤</th>
            <th className="field pb-2 text-right">TER</th>
          </tr>
        </thead>
        <tbody>
          {d.funds.map((f) => (
            <tr key={f.fund_code} className="border-t border-line">
              <td className="py-2 pr-3 text-body-sm">
                <span className="text-fg-2">{plainText(f.fund_name)}</span>
                <span className="mono ml-1.5 text-caption text-fg-4">{f.fund_code}</span>
              </td>
              <td className="py-2 pr-3 text-caption text-fg-4">{plainText(f.group ?? '未映射')}</td>
              <td className="num py-2 text-right text-body-sm text-fg-2">{pctCell(f.sharpe)}</td>
              <td className="num py-2 text-right text-body-sm text-fg-2">{pctCell(f.max_drawdown_1y)}</td>
              <td className="num py-2 text-right text-body-sm text-fg-2">{pctCell(f.ter)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-2 text-caption leading-relaxed text-fg-4">
        夏普分位越大越好；回撤与 TER 分位越大越差（回撤越大 / 费率越贵 = 分位越高）。
        组样本不足时显示「—」而不是数字（{d.funds[0]?.sharpe?.group_n ? `组内 n≈${d.funds[0].sharpe.group_n}` : ''}）。
      </div>
    </Card>
  )
}

/* ── 页面 ─────────────────────────────────────────────────────── */

export default function Decision() {
  const behavior = useApi(endpoints.behavior)
  const cf = useApi(endpoints.counterfactual)
  const bk = useApi(endpoints.breakeven)
  const risk = useApi(endpoints.risk)
  const peer = useApi(endpoints.peer)
  const excl = useApi(endpoints.exclusion)
  const research = useApi(endpoints.research)

  const busy = behavior.loading || cf.loading || bk.loading || risk.loading
    || peer.loading || excl.loading || research.loading
  const wait = Math.max(
    behavior.warmingWait, cf.warmingWait, bk.warmingWait, risk.warmingWait,
    peer.warmingWait, excl.warmingWait, research.warmingWait,
  )

  const refreshAll = () => {
    behavior.refresh({ fresh: true })
    cf.refresh({ fresh: true })
    bk.refresh({ fresh: true })
    risk.refresh({ fresh: true })
    peer.refresh({ fresh: true })
    excl.refresh({ fresh: true })
    research.refresh({ fresh: true })
  }

  return (
    <>
      <PageHead title="决策台" sub="行为镜 · 反事实 · 费率门槛 —— 系统给证据，你下判断">
        <SourceTag source={behavior.source} onRefresh={refreshAll} busy={busy} />
      </PageHead>

      <Warming seconds={busy ? wait : 0} note="首次计算较慢，之后命中缓存" />

      {/* 一等卡 */}
      {behavior.data ? <BehaviorCard b={behavior.data} /> : fallback('行为镜', behavior)}
      {cf.data ? <CounterfactualCard c={cf.data} /> : fallback('反事实归因', cf)}
      {bk.data ? <BreakevenCard b={bk.data} /> : fallback('费率门槛', bk)}

      {/* 二等卡（Bento 2×2）：格子的面积即层级 */}
      <Bento>
        {risk.data ? <RiskCard d={risk.data} /> : fallback('风险刻画', risk, span(7))}
        {excl.data ? <ExclusionCard d={excl.data} /> : fallback('排雷', excl, span(5))}
        {peer.data ? <PeerCard d={peer.data} /> : fallback('同类分位', peer, span(7))}
        {research.data ? (
          <QuantPanel className={span(5)} data={research.data} />
        ) : (
          fallback('研究快照', research, span(5))
        )}
      </Bento>

      {/* 市场小条（指数行情；消息面已随死路由删除） */}
      <MarketStrip />
    </>
  )
}
