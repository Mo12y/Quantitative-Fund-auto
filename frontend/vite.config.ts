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
  },
})