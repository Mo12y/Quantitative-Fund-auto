import { endpoints } from '../api/endpoints'
import { Bento, span } from '../components/Bento'
import { Card } from '../components/Card'
import { ConstraintNote } from '../components/ConstraintNote'
import { DrillDown } from '../components/DrillDown'
import { OpsActions } from '../components/OpsActions'
import { PageHead } from '../components/PageHead'
import { QuantPanel } from '../components/QuantPanel'
import { Skeleton } from '../components/Skeleton'
import { SourceTag } from '../components/SourceTag'
import { Warming } from '../components/Warming'
import { fmtMoney, fmtPct, plainText } from '../lib/format'
import { useApi } from '../lib/useApi'

/** 运维命令清单 —— 与 `src/main.py` 顶部用法一一对应（不写不存在的命令） */
const CMDS: { cmd: string; use: string }[] = [
  { cmd: 'python src/main.py snapshot', use: '全市场当日净值快照（日常增量主路径）' },
  { cmd: 'python src/main.py index', use: '更新指数估值（PE/PB，温度依赖）' },
  { cmd: 'python src/main.py calendar', use: '刷新交易日历' },
  { cmd: 'python src/main.py sector', use: '重算 31 个行业板块排名' },
  { cmd: 'python src/main.py temp', use: '市场温度 + 仓位建议（CLI 版）' },
  { cmd: 'python src/main.py precompute', use: '预计算快照，让 Web 首屏免冷算' },
  { cmd: 'python src/main.py web', use: '启动 Web 仪表盘（默认 :5020）' },
]

const TH = 'field pb-2.5 text-left'

/**
 * 设置入口（计划书 §6）—— 投资计划 / 用户画像 / 量化模型 / 数据源与运维，
 * 并含**写操作面板**（B-4b-3：计划增删改、定投、净值更新、对账 —— 全部先出确认卡）。
 *
 * 布局：宽屏走 **Bento 2×2**（计划/模型 7 栏、画像/运维 5 栏），窄屏自动退回单列堆叠。
 */
export default function Settings() {
  const overview = useApi(endpoints.overview)
  const rebalance = useApi(endpoints.rebalance)
  const plan = useApi(endpoints.plan)
  const sectors = useApi(endpoints.sectors)
  const quant = useApi(endpoints.quantModels)
  const dca = useApi(endpoints.dca)
  /** 数据链路下钻（审计与溯源）—— 2026-10-06 从「今天」搬来（瘦身第 3 条），默认折叠 */
  const explain = useApi(endpoints.explain)

  const ov = overview.data
  const rb = rebalance.data
  const pl = plan.data?.plan ?? ov?.plan ?? null
  const busy = overview.loading || rebalance.loading || plan.loading || sectors.loading
    || quant.loading || dca.loading || explain.loading
  const wait = Math.max(
    overview.warmingWait,
    rebalance.warmingWait,
    plan.warmingWait,
    sectors.warmingWait,
    quant.warmingWait,
    dca.warmingWait,
    explain.warmingWait,
  )

  const refreshAll = () => {
    overview.refresh({ fresh: true })
    rebalance.refresh({ fresh: true })
    plan.refresh({ fresh: true })
    sectors.refresh({ fresh: true })
    quant.refresh({ fresh: true })
    dca.refresh({ fresh: true })
    explain.refresh({ fresh: true })
  }

  return (
    <>
      <PageHead title="设置" sub="投资计划 · 用户画像 · 量化模型 · 数据源与运维">
        <SourceTag source={overview.source} onRefresh={refreshAll} busy={busy} />
      </PageHead>

      <Warming seconds={busy ? wait : 0} />

      {/* ⚠️ 2026-10-06（瘦身第 3 条）：首屏说明文字压到一行。
          DESIGN §5.1「首屏零解释性文字」—— 原为 3 行，把真正的配置项挤下去了。
          写操作的安全披露**不在**这里，而在每个操作自己的确认卡里（`OpsActions` 强制）。 */}
      <div className="rounded-[var(--radius-md)] border border-line bg-inset px-3.5 py-2.5 text-[11.5px] leading-relaxed text-fg-3">
        写操作已接进本页，每个操作先出确认卡；会自己产生真实买入的（对账 / 定投）在确认卡里明示笔数与金额。
      </div>

      {/* 宽屏 Bento 2×2：计划与模型是主体（7 栏），画像与运维是辅助（5 栏）——
          **格子的大小即重要性**。窄屏没有横向空间，自动退回单列（见 `Bento`）。 */}
      <Bento>
        {/* ── 投资计划 ─────────────────────────────────────────── */}
        <Card className={span(7)} title="投资计划" note={pl ? `· ${plainText(pl.name)}` : ''}>
          {!pl ? (
            plan.error ? (
              <div className="text-sm text-fg-2">读取失败：{plan.error}</div>
            ) : (
              <Skeleton lines={4} />
            )
          ) : (
            <>
              <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
                <div>
                  <div className="field">目标</div>
                  <div className="text-[13px] text-fg-2">{plainText(pl.goal)}</div>
                </div>
                <div>
                  <div className="field">期限 / 风险偏好</div>
                  <div className="text-[13px] text-fg-2">
                    {plainText(pl.horizon)} · {plainText(pl.risk_pref)}
                  </div>
                </div>
                <div>
                  <div className="field">起始日</div>
                  <div className="mono text-[13px] text-fg-2">{plainText(pl.start_date)}</div>
                </div>
                <div>
                  <div className="field">计划资金</div>
                  <div className="mono text-[16px] font-semibold text-fg">{fmtMoney(pl.total_capital)}</div>
                </div>
                <div>
                  <div className="field">已投</div>
                  <div className="mono text-[16px] font-semibold text-fg">{fmtMoney(pl.total_invested)}</div>
                </div>
                <div>
                  <div className="field">现金弹药</div>
                  <div className="mono text-[16px] font-semibold text-fg">{fmtMoney(pl.cash_reserve)}</div>
                </div>
              </div>

              <table className="mt-5 w-full border-collapse">
                <thead>
                  <tr>
                    <th className={TH}>计划基金</th>
                    <th className={TH}>角色</th>
                    <th className={TH + ' text-right'}>目标</th>
                    <th className={TH + ' text-right'}>已投</th>
                    <th className={TH + ' text-right'}>进度</th>
                  </tr>
                </thead>
                <tbody>
                  {pl.funds.map((f) => (
                    <tr key={f.code} className="row-hover border-t border-line">
                      <td className="py-2.5 pr-3 text-[13px]">
                        <span className="text-fg">{plainText(f.name)}</span>
                        <span className="mono ml-1.5 text-[11.5px] text-fg-4">{f.code}</span>
                      </td>
                      <td className="py-2.5 pr-3 text-[12px] text-fg-3">{plainText(f.role)}</td>
                      <td className="num py-2.5 text-right text-[12.5px] text-fg-2">
                        {fmtMoney(f.target)}
                        <span className="num ml-1 text-fg-4">{fmtPct(f.target_pct)}</span>
                      </td>
                      <td className="num py-2.5 text-right text-[12.5px] text-fg-2">{fmtMoney(f.invested)}</td>
                      <td className="num py-2.5 text-right text-[12.5px] text-fg-3">{fmtPct(f.progress_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {pl.notes && (
                <div className="mt-3 border-t border-line pt-2 text-[11.5px] leading-relaxed text-fg-4">
                  {plainText(pl.notes)}
                </div>
              )}
            </>
          )}
        </Card>

        {/* ── 用户画像与生效约束 ───────────────────────────────── */}
        <Card className={span(5)} title="用户画像与生效约束" note="· 本地文件，不入库">
          {rb ? (
            <>
              <div className="mb-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
                <div>
                  <div className="field">目标权益仓位</div>
                  <div className="text-[16px] font-semibold text-fg">
                    {rb.target_equity_pct == null ? '—' : fmtPct(rb.target_equity_pct)}
                    <span className="ml-1.5 text-[11px] font-normal text-fg-4">
                      {rb.target_source === 'user_profile' ? '（我的设定）' : '（温度模型）'}
                    </span>
                  </div>
                </div>
                <div>
                  <div className="field">容忍带</div>
                  <div className="num text-[13px] text-fg-2">
                    {rb.rebalance_pp == null ? '—' : `±${rb.rebalance_pp}pp`}
                  </div>
                </div>
                <div>
                  <div className="field">当前权益（A 股口径）</div>
                  <div className="num text-[13px] text-fg-2">{fmtPct(rb.current_equity_pct)}</div>
                </div>
              </div>

              <ConstraintNote review={rb.constraint_review} />

              <div className="mt-1 text-[11.5px] leading-relaxed text-fg-4">
                画像文件：<span className="mono text-fg-3">config/user_profile.local.yaml</span>
                （gitignore，个人文件）。约束
                <b className="font-semibold text-fg-3">只作用于候选筛选与买入候选</b>，不改变持仓与账本。
              </div>
            </>
          ) : rebalance.error ? (
            <div className="text-sm text-fg-2">读取失败：{rebalance.error}</div>
          ) : (
            <Skeleton lines={4} />
          )}
        </Card>

        {/* ── 量化模型（用户 2026-09-30 要求从「研究」移入本页）────── */}
        {quant.data ? (
          <QuantPanel className={span(7)} data={quant.data} />
        ) : (
          <Card className={span(7)} title="量化模型">
            {quant.error ? <div className="text-sm text-fg-2">读取失败：{quant.error}</div> : <Skeleton lines={5} />}
          </Card>
        )}

        {/* ── 数据源与运维 + 数据链路（审计与溯源）────────────────────
            ⚠️ 2026-10-06 瘦身第 3 条：数据链路从「今天」搬来，与「数据源与运维」
            在**同一列**里堆叠，而不是单独开一行。理由是本页的真实版式：
            同行左侧的「量化模型」卡高约 1070px，第二行的高度**完全由它决定** ——
            右列里再加一张卡**不增加页高**；若单独开一行（span 12）则 +120px，
            会把本页推出 DESIGN §5.2 的基线上限（2.4 屏）。
            溯源与运维本就同属"我要核查 / 我该怎么修"这一族，同列也是自然的。
            按 DESIGN §5.1「审计 / 溯源 / 口径 / 命令表一律默认折叠」：
            `DrillDown` 自身已默认折叠，命令表也收进折叠区。 */}
        <div className="min-w-0 flex flex-col gap-4 xl:col-span-5">
          <Card className="min-w-0" title="数据源与运维">
            <div className="mb-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
              <div>
                <div className="field">账本库</div>
                <div className="mono text-[13px] text-fg-2">data/fund_quant.db</div>
              </div>
              <div>
                <div className="field">净值最新日</div>
                <div className="mono text-[13px] text-fg-2">
                  {ov && ov.curve.dates.length ? ov.curve.dates[ov.curve.dates.length - 1] : '—'}
                </div>
              </div>
              <div>
                <div className="field">板块缓存于</div>
                <div className="mono text-[13px] text-fg-2">{sectors.data?.cached_at ?? '—'}</div>
              </div>
            </div>

            {/* ⚠️ 2026-10-06（瘦身第 3 条）：命令表**默认折叠**（DESIGN §5.1「命令表一律默认折叠」）。
                它回答的是"我要核查/运维时敲什么"，不是日常要看的 —— 7 条命令铺在卡里，
                等于把一张速查表当正文。标题行自明，需要时一眼找得到、展开即可复制。 */}
            <details className="mt-1">
              <summary className="cursor-pointer list-none text-[12px] text-fg-2">
                <span className="mr-1 text-fg-3">⌄</span>
                运维命令清单 · {CMDS.length} 条（默认折叠）
              </summary>
              <table className="mt-2 w-full border-collapse">
                <thead>
                  <tr>
                    <th className={TH}>命令</th>
                    <th className={TH}>用途</th>
                  </tr>
                </thead>
                <tbody>
                  {CMDS.map((c) => (
                    <tr key={c.cmd} className="row-hover border-t border-line">
                      <td className="mono py-1.5 pr-3 text-[12px] text-fg-2">{c.cmd}</td>
                      <td className="py-1.5 text-[12px] text-fg-3">{c.use}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>

            <div className="mt-3 border-t border-line pt-2 text-[11.5px] leading-relaxed text-fg-4">
              净值刷新也可走 CLI（上方命令表）；盘中行情端点（
              <span className="mono">/api/market/live</span>）在抓不到时
              <b className="font-semibold text-fg-3">如实返回不可用</b>，不会拿旧值冒充实时。
            </div>
          </Card>

          {/* ── 数据链路（审计与溯源）—— 2026-10-06 从「今天」搬来 ──
              DESIGN §5.4 页面职责表把「审计与溯源」明确划归本页；§5.1 要求它默认折叠、
              且不得出现在第一屏。 */}
          <Card
            className="min-w-0"
            title="数据链路"
            note="· 采集 → 清洗 → 特征 → 建模 → 评估（默认折叠）"
          >
            {explain.data ? (
              <DrillDown explain={explain.data} />
            ) : explain.error ? (
              <div className="text-sm text-fg-2">读取失败：{explain.error}</div>
            ) : (
              <Skeleton lines={3} />
            )}
          </Card>
        </div>

        {/* ── 写操作面板（B-4b-3）：计划 / 定投 / 净值 / 对账，全部先出确认卡 ── */}
        <OpsActions
          className={span(12)}
          holdings={ov?.portfolio.holdings ?? []}
          plans={dca.data ?? []}
          plan={pl}
          onDone={refreshAll}
        />
      </Bento>
    </>
  )
}
