import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 开发态：Vite 跑 5173，`/api/*` 反向代理到 Flask（5020）——
// 这样前端用相对路径 `/api/...` 即可，**生产构建后由 Flask 直接托管 dist**，同一份代码不用改地址。
const API_TARGET = process.env.QFA_API_TARGET || 'http://localhost:5020'

export default defineConfig({
  // 生产构建挂在 Flask 的 /v2 下 —— 资源引用必须是 /v2/assets/...（默认 '/' 会 404）
  base: '/v2/',
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // 注意：base=/v2/ 后 dev server 也在 /v2 下服务 → 开发时访问 http://localhost:5173/v2/
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    // 构建产物由 Flask 的 /v2 路由托管（见 src/web/app.py）——
    // 旧仪表盘（/ 上的 app.js）保持可用，两者并存、可对照，不推倒线上可用界面。
    sourcemap: true,
    rollupOptions: {
      output: {
        /**
         * 拆 vendor chunk。
         *
         * ⚠️ 用**函数**而不是对象形式：对象形式 `{ react: ['react','react-dom'] }` 实测
         * 只分出一个 **0.0 KB** 的 `react-*.js`（子路径如 `react-dom/client` 没被命中），
         * React 仍留在 index 里 —— 等于白建一个空文件让浏览器多请求一次。
         * 按 `node_modules` 路径匹配才可靠。
         *
         * ⚠️ **也说实话**：本应用由 Flask 从本机托管、没有网络延迟，
         * 拆 vendor 对"首屏变快"帮助很小。它解决的是：
         *   ① 消掉 Vite 的 "chunk larger than 500 kB" 警告；
         *   ② 业务代码改动后 `react` / `charts` 的 hash 不变 → 浏览器缓存可复用。
         * 真正缩短**首屏关键路径**的是把 Recharts 从首屏剥离（见 `CurveChart` 的懒加载）。
         */
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('recharts') || id.includes('d3-') || id.includes('internmap')) return 'charts'
          if (id.includes('react-dom') || id.includes('/react/') || id.includes('scheduler')) return 'react'
          return 'vendor'
        },
      },
    },
  },
})