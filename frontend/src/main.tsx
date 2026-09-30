import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

const root = document.getElementById('root')
if (!root) throw new Error('缺少 #root 节点（index.html 被改动？）')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)