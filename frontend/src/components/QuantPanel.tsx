import type { QuantModels } from '../api/types'
import { Card } from './Card'

const TH = 'field pb-1.5'
const TD = 'py-1.5 text-[12.5px]'

function num(v: number | null | undefined, digits = 3): string {
  return v == null || Number.isNaN(v) ? '—' : v.toFixed(digits)
}

/** 小标题（三张表共用样式） */
function Sub({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mb-1.5 mt-3.5 text-[12px] text-fg-2 first:mt-0">
      {title}
      {hint && <span className="ml-1.5 text-[11px] text-fg-4">{hint}</span>}
    </div>
  )
}

/**
 * 量化模型对照（vol 预测 / 回撤预警 / 组合模拟）。
 *
 * ⚠️ 纪律：**只陈列后端给出的原值，不做任何"谁更好"的结论**。
 * 本项目三次实证（见 `docs/基金推荐系统_ML可行性研究.md`）都是负结果，
 * 界面上不能靠高亮某个模型制造"这就是最优解"的印象。
 * 表头用各自的指标名（IC / ICIR / QLIKE / AUC / Brier / F1），不翻译成口语。
 */
export function QuantPanel({ data }: { data: QuantModels }) {
  return (
    <Card title="量化模型" note="· vol 预测 / 回撤预警 / 组合模拟（样本外）">
      <div className="text-[11.5px] leading-relaxed text-fg-4">
        只陈列模型对照原始值，<b className="font-semibold text-fg-3">不给出"哪个更好"的结论</b> —— 本项目样本外验证（51 窗口）
        的结论是「看不出优于等权持有的选基能力」，界面上不制造最优解印象。
      </div>

      <Sub title="波动率预测模型" hint="IC / ICIR 越大越好；QLIKE 越小越好" />
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={TH + ' text-left'}>模型</th>
            <th className={TH + ' text-right'}>IC 均值</th>
            <th className={TH + ' text-right'}>ICIR</th>
            <th className={TH + ' hidden text-right sm:table-cell'}>QLIKE</th>
            <th className={TH + ' hidden text-right md:table-cell'}>MZ β</th>
          </tr>
        </thead>
        <tbody>
          {data.vol.map((r) => (
            <tr key={r.model} className="row-hover border-t border-line">
              <td className={TD + ' text-fg'}>{r.model}</td>
              <td className={TD + ' mono text-right text-fg-2'}>{num(r.ic_mean, 4)}</td>
              <td className={TD + ' mono text-right text-fg-2'}>{num(r.icir, 3)}</td>
              <td className={TD + ' mono hidden text-right text-fg-2 sm:table-cell'}>{num(r.qlike, 4)}</td>
              <td className={TD + ' mono hidden text-right text-fg-2 md:table-cell'}>{num(r.mz_beta, 3)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <Sub title="回撤预警模型" hint="AUC 越大越好；Brier 越小越好" />
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={TH + ' text-left'}>模型</th>
            <th className={TH + ' text-right'}>AUC</th>
            <th className={TH + ' hidden text-right sm:table-cell'}>Brier</th>
            <th className={TH + ' text-right'}>F1</th>
            <th className={TH + ' hidden text-right md:table-cell'}>漏报率</th>
          </tr>
        </thead>
        <tbody>
          {data.drawdown.map((r) => (
            <tr key={r.model} className="row-hover border-t border-line">
              <td className={TD + ' text-fg'}>{r.model}</td>
              <td className={TD + ' mono text-right text-fg-2'}>{num(r.auc, 3)}</td>
              <td className={TD + ' mono hidden text-right text-fg-2 sm:table-cell'}>{num(r.brier, 4)}</td>
              <td className={TD + ' mono text-right text-fg-2'}>{num(r.f1, 2)}</td>
              <td className={TD + ' mono hidden text-right text-fg-2 md:table-cell'}>{num(r.miss_rate, 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <Sub title="组合模拟方案" hint="样本外，等权/风险平价等" />
      <table className="w-full border-collapse">
        <thead>
          <tr>
            <th className={TH + ' text-left'}>方案</th>
            <th className={TH + ' text-right'}>月数</th>
            <th className={TH + ' text-right'}>累计</th>
            <th className={TH + ' text-right'}>年化</th>
            <th className={TH + ' hidden text-right sm:table-cell'}>夏普</th>
            <th className={TH + ' hidden text-right md:table-cell'}>最大回撤</th>
          </tr>
        </thead>
        <tbody>
          {data.portfolio.map((r) => (
            <tr key={r.scheme} className="row-hover border-t border-line">
              <td className={TD + ' text-fg'}>{r.scheme}</td>
              <td className={TD + ' mono text-right text-fg-3'}>{r.months}</td>
              <td className={TD + ' mono text-right text-fg-2'}>{num(r.total_return, 2)}%</td>
              <td className={TD + ' mono text-right text-fg-2'}>{num(r.annual_return, 2)}%</td>
              <td className={TD + ' mono hidden text-right text-fg-2 sm:table-cell'}>{num(r.sharpe, 3)}</td>
              <td className={TD + ' mono hidden text-right text-fg-2 md:table-cell'}>{num(r.max_drawdown, 2)}%</td>
            </tr>
          ))}
        </tbody>
      </table>

      {Object.keys(data.reports).length > 0 && (
        <>
          <Sub title="模型报告" hint="仓库 docs/ 目录" />
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11.5px]">
            {Object.entries(data.reports).map(([name, ok]) => (
              <span key={name} className={ok ? 'text-fg-3' : 'text-fg-4 line-through'}>
                {name}
              </span>
            ))}
          </div>
        </>
      )}
    </Card>
  )
}
