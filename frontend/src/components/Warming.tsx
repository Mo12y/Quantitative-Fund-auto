/**
 * 预热提示条 —— 后端慢端点冷算时返回 `status:"warming"`，前端轮询期间显示。
 *
 * ⚠️ 它存在的意义是**把"还在算"和"没有数据"分开说**（本项目的老坑：
 * 把 `data:null` 当"算出来是空的"，于是用户以为功能坏了）。
 * 所以这里文案必须带**预计秒数**，不能只写"加载中"。
 */
export function Warming({ seconds, note }: { seconds: number; note?: string }) {
  if (seconds <= 0) return null
  return (
    <div className="rise-in mb-4 flex items-center gap-2.5 rounded-[var(--radius-md)] border border-accent/25 bg-accent/[.07] px-3.5 py-2.5 text-body-sm text-fg-2">
      <span className="relative flex h-2 w-2 shrink-0">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
      </span>
      正在计算…还需约 {seconds} 秒{note ? `（${note}）` : ''}
    </div>
  )
}
