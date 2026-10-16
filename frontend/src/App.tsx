/**
 * 应用外壳 —— 三入口（决策台 / 账本 / 数据状态，计划书 §4.1）。
 *
 * 导航**放在内容容器之外**：它是吸顶全宽的毛玻璃条；内容再收进响应式容器（见下方 `main`）。
 * 路由用 hash（见 `lib/router.ts`，零新依赖）。每个入口只在进入时挂载自己的数据请求，不预取。
 *
 * 方案 B（2026-10-04 用户拍板、B-5 切换）：本应用挂在 Flask 的 **`/`**；
 * 旧仪表盘（原 `/` 上的 app.js）已退役，`/v2` 保留 301 重定向到 `/`。
 */
import { lazy, Suspense } from 'react'
import { Card } from './components/Card'
import { Nav } from './components/Nav'
import { Sidebar } from './components/Sidebar'
import { Skeleton } from './components/Skeleton'
import { useEntry } from './lib/router'
import Decision from './routes/Decision'

/**
 * 「决策台」**同步**引入，其余两个入口**按需**引入。
 * 理由：Decision 是默认落地页，把它也懒加载会让首屏多等一跳（Suspense 闪一下）；
 * 而 Ledger / Data 各自带着表格与面板，多数访问不会在同一次会话里全打开。
 *
 * ⚠️ 本应用由 Flask 从**本机**托管、没有网络延迟，所以拆包对"首屏变快"帮助很小 ——
 * 它的实际价值是"消掉 Vite 的 500KB 警告 + 依赖 chunk 的缓存复用"
 * （详见 `vite.config.ts` 里 `manualChunks` 的注释）。
 */
const Ledger = lazy(() => import('./routes/Ledger'))
const Data = lazy(() => import('./routes/Data'))

/** 懒加载入口的兜底：给骨架而不是空白，避免切页时"闪一下白" */
function RouteFallback() {
  return (
    <Card>
      <Skeleton lines={4} />
    </Card>
  )
}

export default function App() {
  const [entry, go] = useEntry()
  return (
    /* 桌面：左栏 + 内容（`lg:flex`）；窄屏：退回单列 + 顶部条（见 `Nav` 的 `lg:hidden`） */
    <div className="min-h-screen lg:flex">
      <Sidebar entry={entry} onGo={go} />

      {/* `min-w-0` 必须给：flex 子项默认 `min-width:auto`，宽表格会把内容区撑破 */}
      <div className="min-w-0 flex-1">
        <Nav entry={entry} onGo={go} />
        {/*
          容器宽度：**响应式**（原固定 1080px 在 1920 屏下两侧各空 420px）。
          块间距：**由父级统一给**（`flex flex-col gap-4`），卡片自己不带 margin。
        */}
        <main className="mx-auto flex w-full max-w-[1240px] flex-col gap-4 px-5 pb-16 pt-6 2xl:max-w-[1360px] lg:pt-9">
          <Suspense fallback={<RouteFallback />}>
            {entry === 'decision' && <Decision />}
            {entry === 'ledger' && <Ledger />}
            {entry === 'data' && <Data />}
          </Suspense>
        </main>
      </div>
    </div>
  )
}
