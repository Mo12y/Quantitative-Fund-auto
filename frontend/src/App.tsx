/**
 * 应用外壳 —— 四入口（今天 / 持仓 / 研究 / 设置，计划书 §6）。
 *
 * 导航**放在内容容器之外**：它是吸顶全宽的毛玻璃条；内容再收进 `max-w-[1080px]` 居中。
 * 路由用 hash（见 `lib/router.ts`，零新依赖）。每个入口只在进入时挂载自己的数据请求，不预取。
 *
 * 与旧仪表盘（Flask `/` 上的 app.js）的关系：两者**并存**，新前端挂在 `/v2` 下，
 * 不改旧的、不推倒线上可用界面；对照跑一段时间、确认无新回归后再决定退役旧界面。
 */
import { Nav } from './components/Nav'
import { useEntry } from './lib/router'
import Position from './routes/Position'
import Research from './routes/Research'
import Settings from './routes/Settings'
import Today from './routes/Today'

export default function App() {
  const [entry, go] = useEntry()
  return (
    <div className="min-h-screen">
      <Nav entry={entry} onGo={go} />
      <main className="mx-auto max-w-[1080px] px-4 pb-16 pt-6">
        {entry === 'today' && <Today />}
        {entry === 'position' && <Position />}
        {entry === 'research' && <Research />}
        {entry === 'settings' && <Settings />}
      </main>
    </div>
  )
}
