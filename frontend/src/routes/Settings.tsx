import { endpoints } from '../api/endpoints'
import { Bento, span } from '../components/Bento'
import { Card } from '../components/Card'
import { ConstraintNote } from '../components/ConstraintNote'
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
 * 设置入口（计划书 §6）—— **首版只读**，另含「量化模型」对照（用户 2026-09-30 指定移入）。
 *
 * ⚠️ 为什么不做写操作：本页对应的旧视图含计划增删改、画像编辑、净值更新等
 * **会改账本/画像**的动作。按项目铁律，写 `data/fund_quant.db` 前必须先做整库快照并留回滚点，
 * 这类交互要单独一批迁移（含确认流、失败回滚、并发锁），不能顺手塞进这一轮。
 * 界面如实标注"维护在旧仪表盘"，不假装这里能改。
 *
 * 布局：宽屏走 **Bento 2×2**（计划/模型 7 栏、画像/运维 5 栏），窄屏自动退回单列堆叠。
 */
export default function Settings() {
  const overview = useApi(endpoints.overview)
  const rebalance = useApi(endpoints.rebalance)
  const plan = useApi(endpoints.plan)
  const sectors = useApi(endpoints.sectors)
  const quant = useApi(endpoints.quantModels)

  const ov = overview.data
  const rb = rebalance.data
  const pl = plan.data?.plan ?? ov?.plan ?? null
  const busy = overview.loading || rebalance.loading || plan.loading || sectors.loading || quant.loading
  const wait = Math.max(
    overview.warmingWait,
    rebalance.warmingWait,
    plan.warmingWait,
    sectors.warmingWait,
    quant.warmingWait,
  )

  const refreshAll = () => {
    overview.refresh({ fresh: true })
    rebalance.refresh({ fresh: true })
    plan.refresh({ fresh: true })
    sectors.refresh({ fresh: true })
    quant.refresh({ fresh: true })
  }

  return (
    <>
      <PageHead title="设置" sub="投资计划 · 用户画像 · 量化模型 · 数据源与运维">
        <SourceTag source={overview.source} onRefresh={refreshAll} busy={busy} />
      </PageHead>

      <Warming seconds={busy ? wait : 0} />

      <div className="rounded-[var(--radius-md)] border border-line bg-inset px-3.5 py-2.5 text-[11.5px] leading-relaxed text-fg-3">
        本页<b className="font-semibold text-fg-2">只读</b>。计划维护（增删改）、用户画像编辑、净值/持仓更新等写操作仍在旧仪表盘
        （<span className="mono">/</span>）完成 —— 写账本需要先留整库快照与回滚点，单独一批迁移。
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
                        <span className="ml-1 text-fg-4">{fmtPct(f.target_pct)}</span>
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

        {/* ── 数据源与运维 ─────────────────────────────────────── */}
        <Card className={span(5)} title="数据源与运维">
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

          <table className="w-full border-collapse">
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

          <div className="mt-3 border-t border-line pt-2 text-[11.5px] leading-relaxed text-fg-4">
            净值刷新走 CLI（Web 端只读账本）。盘中行情端点（
            <span className="mono">/api/market/live</span>）在抓不到时
            <b className="font-semibold text-fg-3">如实返回不可用</b>，不会拿旧值冒充实时。
          </div>
        </Card>
      </Bento>
    </>
  )
}
