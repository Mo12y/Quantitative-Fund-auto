/**
 * 极简 hash 路由 —— 四入口（今天 / 持仓 / 研究 / 设置）之间切换。
 *
 * **为什么不引 react-router**：本项目铁律「不新增运行时依赖」，而四入口是**同级页面**，
 * 无嵌套路由、无路径参数、无 loader —— 一个 `useState` + `hashchange` 就够（30 行）。
 * 引一个路由库只为这点需求，是拿依赖换零收益。
 *
 * **为什么用 hash 而不是 History API**：Flask 的 `/v2` 只是静态托管（`frontend/dist`），
 * 直接请求 `/v2/research` 会 404。`#/research` 永远只请求 `/v2`，刷新、分享、前进后退都天然可用。
 */
import { useEffect, useState } from 'react'

export const ENTRIES = ['today', 'position', 'research', 'settings'] as const
export type Entry = (typeof ENTRIES)[number]

/** 入口中文名 —— 导航与页面标题**共用这一处**，避免两处写法漂移。 */
export const ENTRY_LABEL: Record<Entry, string> = {
  today: '今天',
  position: '持仓',
  research: '研究',
  settings: '设置',
}

/** 入口图标名（对应 `components/Icon.tsx` 的键）。纯粹是导航的视觉锚点，不影响语义。 */
export const ENTRY_ICON: Record<Entry, string> = {
  today: 'today',
  position: 'position',
  research: 'research',
  settings: 'settings',
}

/** 解析 hash → 入口；无法识别（含空 hash）**一律回落 today**，不抛错、不白屏。 */
function parse(hash: string): Entry {
  const h = hash.replace(/^#\/?/, '').trim()
  return (ENTRIES as readonly string[]).includes(h) ? (h as Entry) : 'today'
}

export function useEntry(): [Entry, (e: Entry) => void] {
  const [entry, setEntry] = useState<Entry>(() => parse(window.location.hash))

  useEffect(() => {
    const onHash = () => setEntry(parse(window.location.hash))
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const go = (e: Entry) => {
    // 已在目标入口时 hashchange 不会触发，直接 setState（避免"点了没反应"）
    if (parse(window.location.hash) === e) setEntry(e)
    else window.location.hash = '/' + e
  }
  return [entry, go]
}
