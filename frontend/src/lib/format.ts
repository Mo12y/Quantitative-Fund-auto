/**
 * 数值与方向的**唯一**格式化入口。
 *
 * ⚠️ 为什么必须集中：旧前端 `app.css` 把涨跌色写成了**欧美惯例**（绿=涨、红=跌），
 * 与国内（同花顺/东方财富）相反 —— 满屏"涨的显示绿"。这种事只能有一个地方定义，
 * 否则每个组件各写一遍必然再错。
 *
 * 两条硬规则：
 *  1. **涨红跌绿**（中国惯例）；
 *  2. 涨跌**不能只靠颜色**（红绿组合影响约 8% 男性）→ 必须同时给 ▲/▼ 符号。
 */
/** 方向：涨 / 跌 / 平（**唯一**定义处，避免各组件各写一遍） */
export type Dir = 'rise' | 'fall' | 'flat'

export function dirOf(v: number | null | undefined): Dir {
  if (v == null || Number.isNaN(v)) return 'flat'
  if (v > 0) return 'rise'
  if (v < 0) return 'fall'
  return 'flat'
}

/** 涨跌符号（色盲硬约束：不靠颜色也要能读） */
export function dirSymbol(d: Dir): string {
  return d === 'rise' ? '▲' : d === 'fall' ? '▼' : ''
}

/** Tailwind 文字色类名 */
export function dirClass(d: Dir): string {
  return d === 'rise' ? 'text-rise' : d === 'fall' ? 'text-fall' : 'text-flat'
}

export function fmtMoney(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v < 0 ? '−' : ''
  return `${sign}¥${Math.abs(v).toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`
}

/** 金额（带正号，用于"盈亏"这类需要显式正负的地方） */
export function fmtSignedMoney(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v > 0 ? '+' : v < 0 ? '−' : ''
  return `${sign}¥${Math.abs(v).toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`
}

/** 百分比（**带 % 号**，不带正号；用于"收益率"） */
export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v < 0 ? '−' : ''
  return `${sign}${Math.abs(v).toFixed(digits)}%`
}

/** 百分比（**带正负号**；用于涨跌幅这类需要显示"+"/"−"的地方） */
export function fmtSignedPct(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v > 0 ? '+' : v < 0 ? '−' : ''
  return `${sign}${Math.abs(v).toFixed(digits)}%`
}

export function fmtInt(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  return Math.round(v).toLocaleString('zh-CN')
}

/**
 * 界面文案清洗（**唯一**入口）。
 * 后端文案里混着两类非正文记号，界面按纯文本呈现，统一在这里剥掉：
 *  · emoji —— `level_desc` 的 "🌤️ 适中"；
 *  · markdown 粗体记号 —— `scope_note` 的 "只算温度**适用**的 A 股权益"。
 */
const EMOJI_RE = /[\u{1F300}-\u{1FAFF}\u{1F000}-\u{1F2FF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}]/gu
export function plainText(s: string | null | undefined): string {
  return String(s ?? '')
    .replace(EMOJI_RE, '')
    .replace(/\*\*/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}