/**
 * 应用外壳 —— 目前只有「今天」一个入口（四入口 IA 见 docs/前端重构计划书.md §4）。
 *
 * 与旧仪表盘（Flask `/` 上的 app.js）的关系：两者**并存**，新前端挂在 `/v2` 下，
 * 不改旧的、不推倒线上可用界面；对照跑一段时间、确认新前端无回归后再决定退役旧界面。
 */
import Today from './routes/Today'

export default function App() {
  return (
    <div className="mx-auto max-w-[1080px] px-4 pb-12 pt-5">
      <Today />
    </div>
  )
}