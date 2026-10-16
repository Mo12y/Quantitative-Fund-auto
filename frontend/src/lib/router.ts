/**
 * 极简 hash 路由 —— 三入口（决策台 / 账本 / 数据状态）之间切换。
 *
 * **为什么不引 react-router**：本项目铁律「不新增运行时依赖」，而三入口是**同级页面**，
 * 无嵌套路由、无路径参数、无 loader —— 一个 `useState` + `hashchange` 就够（30 行）。
 * 引一个路由库只为这点需求，是拿依赖换零收益。
 *
 * **为什么用 hash 而不是 History API**：Flask 的 `/` 只是静态托管（`frontend/dist`），
 * 直接请求 `/ledger` 会 404。`#/ledger` 永远只请求 `/`，刷新、分享、前进后退都天然可用。
 *
 * ⚠️ 2026-10-16 T3：入口由旧四页（today/position/research/settings）改为三页；
 * 旧 hash（含空 hash）**一律回落决策台**，不抛错、不白屏。
 */
import { useEffect, useState } from 'react'

export const ENTRIES = ['decision', 'ledger', 'data'] as const
export type Entry = (typeof ENTRIES)[number]

/** 入口中文名 —— 导航与页面标题**共用这一处**，避免两处写法漂移。 */
export const ENTRY_LABEL: Record<Entry, string> = {
  decision: '决策台',
  ledger: '账本',
  data: '数据状态',
}

/** 入口图标名（对应 `components/Icon.tsx` 的键）。纯粹是导航的视觉锚点，不影响语义。 */
export const ENTRY_ICON: Record<Entry, string> = {
  decision: 'decision',
  ledger: 'ledger',
  data: 'data',
}

/** 解析 hash → 入口；无法识别（含旧入口 / 空 hash）**一律回落 decision**，不抛错、不白屏。 */
function parse(hash: string): Entry {
  const h = hash.replace(/^#\/?/, '').trim()
  return (ENTRIES as readonly string[]).includes(h) ? (h as Entry) : 'decision'
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
