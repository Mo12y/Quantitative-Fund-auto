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
      {/*
        容器宽度：**响应式放宽**（原固定 1080px）。
        实测 1920 屏下 1080 的容器两侧各空 420px（44% 屏幕是空白），卡片看着"小"就是这个原因。
        现在 1280 起 1240、1536 起 1360 —— 数据表能摊开，又不至于行长失控。

        ⚠️ 上限**不是**越宽越好：曾试到 1460，结果 `数据链路` 的「标签 ←→ 数值」被拉到两端，
        1400px 下眼睛跟不住（行长问题）。所以宽度收在 1360，
        同时给这类"读的行"单独限宽（见 `DrillDown` 的 `max-w-[880px]`）。

        块间距：**由父级统一给**（`flex flex-col gap-4`），卡片自己不再带 margin。
        好处：间距只有一处定义，"卡片与卡片的关系"才不会各写各的。
        需要表达从属关系的块（如 今天 的「结论 + KPI」）在内部再包一层小 gap。
      */}
      <main className="mx-auto flex w-full max-w-[1240px] flex-col gap-4 px-5 pb-16 pt-6 2xl:max-w-[1360px]">
        {entry === 'today' && <Today />}
        {entry === 'position' && <Position />}
        {entry === 'research' && <Research />}
        {entry === 'settings' && <Settings />}
      </main>
    </div>
  )
}
