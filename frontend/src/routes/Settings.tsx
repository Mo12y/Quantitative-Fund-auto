import { endpoints } from '../api/endpoints'
import { Card } from '../components/Card'
import { ConstraintNote } from '../components/ConstraintNote'
import { SourceTag } from '../components/SourceTag'
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

const TH = 'pb-2 text-[11px] font-normal text-fg-3'

/**
 * 设置入口（计划书 §6）—— **首版只读**。
 *
 * ⚠️ 为什么不做写操作：本页对应的旧视图含计划增删改、画像编辑、净值更新等
 * **会改账本/画像**的动作。按项目铁律，写 `data/fund_quant.db` 前必须先做整库快照并留回滚点，
 * 这类交互要单独一批迁移（含确认流、失败回滚、并发锁），不能顺手塞进这一轮。
 * 界面如实标注"维护在旧仪表盘"，不假装这里能改。
 */
export default function Settings() {
  const overview = useApi(endpoints.overview)
  const rebalance = useApi(endpoints.rebalance)
  const plan = useApi(endpoints.plan)
  const sectors = useApi(endpoints.sectors)

  const ov = overview.data
  const rb = rebalance.data
  const pl = plan.data?.plan ?? ov?.plan ?? null
  const busy = overview.loading || rebalance.loading || plan.loading || sectors.loading

  const refreshAll = () => {
    overview.refresh({ fresh: true })
    rebalance.refresh({ fresh: true })
    plan.refresh({ fresh: true })
    sectors.refresh({ fresh: true })
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-[17px] font-medium tracking-[.2px]">
          设置
          <span className="ml-1.5 text-[13px] font-normal text-fg-3">投资计划 · 用户画像 · 数据源与运维</span>
        </h1>
        <SourceTag source={overview.source} onRefresh={refreshAll} busy={busy} />
      </div>

      <div className="mb-3.5 rounded-md border border-line bg-inset px-3 py-2 text-[11.5px] leading-relaxed text-fg-3">
        本页**只读**。计划维护（增删改）、用户画像编辑、净值/持仓更新等写操作仍在旧仪表盘
        （<span className="mono">/</span>）完成 —— 写账本需要先留整库快照与回滚点，单独一批迁移。
      </div>

      {/* ── 投资计划 ─────────────────────────────────────────── */}
      <Card title="投资计划" note={pl ? `· ${plainText(pl.name)}` : ''}>
        {!pl ? (
          <div className="text-sm text-fg-3">{plan.error ? `读取失败：${plan.error}` : '加载中…'}</div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-[12.5px] sm:grid-cols-3">
              <div>
                <div className="text-[11px] text-fg-4">目标</div>
                <div className="text-fg-2">{plainText(pl.goal)}</div>
              </div>
              <div>
                <div className="text-[11px] text-fg-4">期限 / 风险偏好</div>
                <div className="text-fg-2">
                  {plainText(pl.horizon)} · {plainText(pl.risk_pref)}
                </div>
              </div>
              <div>
                <div className="text-[11px] text-fg-4">起始日</div>
                <div className="mono text-fg-2">{plainText(pl.start_date)}</div>
              </div>
              <div>
                <div className="text-[11px] text-fg-4">计划资金</div>
                <div className="mono text-fg-2">{fmtMoney(pl.total_capital)}</div>
              </div>
              <div>
                <div className="text-[11px] text-fg-4">已投</div>
                <div className="mono text-fg-2">{fmtMoney(pl.total_invested)}</div>
              </div>
              <div>
                <div className="text-[11px] text-fg-4">现金弹药</div>
                <div className="mono text-fg-2">{fmtMoney(pl.cash_reserve)}</div>
              </div>
            </div>

            <table className="mt-4 w-full border-collapse">
              <thead>
                <tr>
                  <th className={TH + ' text-left'}>计划基金</th>
                  <th className={TH + ' text-left'}>角色</th>
                  <th className={TH + ' text-right'}>目标</th>
                  <th className={TH + ' text-right'}>已投</th>
                  <th className={TH + ' text-right'}>进度</th>
                </tr>
              </thead>
              <tbody>
                {pl.funds.map((f) => (
                  <tr key={f.code} className="border-t border-line">
                    <td className="py-2 pr-3 text-[13px]">
                      <span className="text-fg">{plainText(f.name)}</span>
                      <span className="mono ml-1.5 text-[11.5px] text-fg-4">{f.code}</span>
                    </td>
                    <td className="py-2 pr-3 text-[12px] text-fg-3">{plainText(f.role)}</td>
                    <td className="mono py-2 text-right text-[12.5px] text-fg-2">
                      {fmtMoney(f.target)}
                      <span className="ml-1 text-fg-4">{fmtPct(f.target_pct)}</span>
                    </td>
                    <td className="mono py-2 text-right text-[12.5px] text-fg-2">{fmtMoney(f.invested)}</td>
                    <td className="mono py-2 text-right text-[12.5px] text-fg-3">{fmtPct(f.progress_pct)}</td>
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
      <Card title="用户画像与生效约束" note="· 本地文件，不入库">
        {rb ? (
          <>
            <div className="mb-2 grid grid-cols-2 gap-x-6 gap-y-1.5 text-[12.5px] sm:grid-cols-3">
              <div>
                <div className="text-[11px] text-fg-4">目标权益仓位</div>
                <div className="mono text-fg-2">
                  {rb.target_equity_pct == null ? '—' : fmtPct(rb.target_equity_pct)}
                  <span className="ml-1.5 font-sans text-[11px] text-fg-4">
                    {rb.target_source === 'user_profile' ? '（我的设定）' : '（温度模型）'}
                  </span>
                </div>
              </div>
              <div>
                <div className="text-[11px] text-fg-4">容忍带</div>
                <div className="mono text-fg-2">{rb.rebalance_pp == null ? '—' : `±${rb.rebalance_pp}pp`}</div>
              </div>
              <div>
                <div className="text-[11px] text-fg-4">当前权益（A 股口径）</div>
                <div className="mono text-fg-2">{fmtPct(rb.current_equity_pct)}</div>
              </div>
            </div>

            <ConstraintNote review={rb.constraint_review} />

            <div className="mt-1 text-[11.5px] leading-relaxed text-fg-4">
              画像文件：<span className="mono text-fg-3">config/user_profile.local.yaml</span>
              （gitignore，个人文件）。约束**只作用于候选筛选与买入候选**，不改变持仓与账本。
            </div>
          </>
        ) : (
          <div className={'text-sm ' + (rebalance.error ? 'text-fg-2' : 'text-fg-3')}>
            {rebalance.error ? `读取失败：${rebalance.error}` : '加载中…'}
          </div>
        )}
      </Card>

      {/* ── 数据源与运维 ─────────────────────────────────────── */}
      <Card title="数据源与运维">
        <div className="mb-3 grid grid-cols-2 gap-x-6 gap-y-1.5 text-[12.5px] sm:grid-cols-3">
          <div>
            <div className="text-[11px] text-fg-4">账本库</div>
            <div className="mono text-fg-2">data/fund_quant.db</div>
          </div>
          <div>
            <div className="text-[11px] text-fg-4">净值最新日</div>
            <div className="mono text-fg-2">
              {ov && ov.curve.dates.length ? ov.curve.dates[ov.curve.dates.length - 1] : '—'}
            </div>
          </div>
          <div>
            <div className="text-[11px] text-fg-4">板块缓存于</div>
            <div className="mono text-fg-2">{sectors.data?.cached_at ?? '—'}</div>
          </div>
        </div>

        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className={TH + ' text-left'}>命令</th>
              <th className={TH + ' text-left'}>用途</th>
            </tr>
          </thead>
          <tbody>
            {CMDS.map((c) => (
              <tr key={c.cmd} className="border-t border-line">
                <td className="mono py-1.5 pr-3 text-[12px] text-fg-2">{c.cmd}</td>
                <td className="py-1.5 text-[12px] text-fg-3">{c.use}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="mt-3 border-t border-line pt-2 text-[11.5px] leading-relaxed text-fg-4">
          净值刷新走 CLI（Web 端只读账本）。盘中行情端点（
          <span className="mono">/api/market/live</span>）在抓不到时**如实返回不可用**，
          不会拿旧值冒充实时。
        </div>
      </Card>
    </>
  )
}
