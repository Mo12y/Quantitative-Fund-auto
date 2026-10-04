import type { ReactNode } from 'react'
import { endpoints } from '../api/endpoints'
import type { GetOptions } from '../api/client'
import { Bento, span } from '../components/Bento'
import { Card } from '../components/Card'
import { DrillDown } from '../components/DrillDown'
import { PageHead } from '../components/PageHead'
import { PoolBoard } from '../components/PoolBoard'
import { SectorTable } from '../components/SectorTable'
import { Skeleton } from '../components/Skeleton'
import { SourceTag } from '../components/SourceTag'
import { Warming } from '../components/Warming'
import { useApi } from '../lib/useApi'

/**
 * 加载器必须在**模块级**定义（引用稳定）。
 * `useApi` 的 effect 依赖 `run`，而 `run` 依赖 `loader` —— 若写成内联箭头函数，
 * 每次渲染都是新引用 → effect 每渲染都重跑 → **无限请求循环**。
 */
const loadBoard = (o?: GetOptions) => endpoints.fundsBoard(8, 300, o)

/** 分区块的兜底：失败说原因、等待给骨架（都不许白屏）。`className` 用于 Bento 占宽。 */
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

/**
 * 研究入口（计划书 §6）—— 收敛旧前端的 pool / sector 两个视图。
 *
 * ⚠️ 这里做的**不是**把两块内容堆在一页，而是按用户明确要求把
 * **「基金筛选池」与「行业板块」合到一起**：同一页内先看市场层的行业板块，
 * 再看按基金主题分组的质量池。
 *
 * 两套板块**刻意不联动**：上方是申万一级行业（市场行情口径），
 * 下方是按基金名称关键词归的主题桶 —— 联动会给出"点行业就筛出该行业基金"的错误暗示，
 * 而这在数据上并不成立（两者分类体系不同）。
 *
 * 量化模型已按用户要求**移入「设置」页**（见 `routes/Settings.tsx`）。
 */
export default function Research() {
  const board = useApi(loadBoard)
  const sectors = useApi(endpoints.sectors)
  const explain = useApi(endpoints.explain)

  const busy = board.loading || sectors.loading || explain.loading
  const wait = Math.max(board.warmingWait, sectors.warmingWait, explain.warmingWait)

  const refreshAll = () => {
    board.refresh({ fresh: true })
    sectors.refresh({ fresh: true })
    explain.refresh({ fresh: true })
  }

  return (
    <>
      <PageHead title="研究" sub="行业板块 · 基金筛选池">
        <SourceTag source={board.source} onRefresh={refreshAll} busy={busy} />
      </PageHead>

      <Warming seconds={busy ? wait : 0} note="筛选池首次计算较慢，之后命中缓存" />

      {/* 宽屏并排：池子是主体（7 栏），板块是参照（5 栏）—— 面积即重要性 */}
      <Bento>
        {sectors.data ? (
          <SectorTable className={span(5)} data={sectors.data} />
        ) : (
          fallback('行业板块', sectors, span(5))
        )}
        {board.data ? (
          <PoolBoard className={span(7)} data={board.data} />
        ) : (
          fallback('筛选池', board, span(7))
        )}
      </Bento>

      {/* 数据链路下钻 —— **复用**「今天」页那一个 `DrillDown` 组件（不是复制一份），
          所以只有一份实现、两处消费，不存在"两处维护、两处漂移"。
          （计划书 §6 原本就写了要接到更多卡片；§9 当初搁置它的理由是"避免两处维护"，
            而那个顾虑针对的是**复制标记**，复用同一组件即已解决。） */}
      <Card title="数据链路" note="· 采集 → 清洗 → 特征 → 建模 → 评估">
        {explain.data ? (
          <DrillDown explain={explain.data} />
        ) : explain.error ? (
          <div className="text-sm text-fg-2">读取失败：{explain.error}</div>
        ) : (
          <Skeleton lines={4} />
        )}
      </Card>
    </>
  )
}
