# DESIGN.md — 量化基金仪表盘设计规范

> 本文件是前端视觉的**单一事实来源**。任何 UI 改动都从这里取 token，不要临时造颜色/字号/圆角。
> 设计语言来源：Linear 设计系统（`awesome-design-md/design-md/linear.app`）为基座，
> 叠加 taste-skill / minimalist-skill 的“去 AI 味”硬约束，流程遵循 impeccable 的
> “先审计 → 定向修复，不推翻重写”。

---

## 0. 硬约束（禁止项，来自 taste-skill / minimalist-skill）

- **禁止用 emoji 当图标**：标题、按钮、状态一律用文字或内联 SVG。数据里自带的 emoji 标签在展示层剥离。
- **禁止渐变、霓虹色、玻璃拟态**（导航栏可保留 <12px 的轻微 blur）。
- **禁止重阴影**：阴影要么没有，要么极低不透明度（< 0.05）且大范围扩散。
- **大容器/卡片/主按钮禁止胶囊圆角**（`rounded-full` 只允许用于小徽标、状态点）。
- **禁止纯黑背景 `#000000`**：用带蓝调的黑（见 `--canvas`）。
- **禁止廉价元标签**：不写 “SECTION 01 / PART 2 / QUESTION 05”。
- **数据一律等宽数字**：金额/百分比/净值使用 `font-variant-numeric: tabular-nums`。
- **文案直白**：不用 “赋能/无缝/颠覆/一站式” 之类空话。
- **字体**：不用 Inter/Roboto/Open Sans 作为唯一字体；用系统 UI 字体栈（SF Pro / Segoe UI / PingFang）+
  等宽字体做数字；大标题负字距（-0.02em ~ -0.04em），小标签正字距（+0.04em）。

---

## 1. 颜色

单一色相强调（Linear 原则）：强调色**只**出现在主操作、焦点环、图表主线，不做装饰。

| Token | 值 | 用途 |
|---|---|---|
| `--canvas` | `#010102` | 页面底色（带蓝调的黑） |
| `--surface-1` | `#0f1011` | 卡片/面板 |
| `--surface-2` | `#141516` | 卡内嵌块、输入框、表格斑马 |
| `--hairline` | `#23252a` | 1px 描边（取代阴影划分层次） |
| `--hairline-strong` | `#34343a` | 强调描边、输入框 focus 前置 |
| `--ink` | `#f7f8f8` | 主文本 |
| `--ink-muted` | `#d0d6e0` | 次级文本 |
| `--ink-subtle` | `#8a8f98` | 说明、标签 |
| `--ink-tertiary` | `#62666d` | 极弱提示 |
| `--primary` | `#5e6ad2` | 唯一强调色 |
| `--primary-hover` | `#828fff` | 悬停 |
| `--up` | `#27a644` | 涨/正收益（语义） |
| `--down` | `#e5484d` | 跌/负收益（语义） |
| `--warn` | `#d9a441` | 注意/待确认（语义） |

语义色只用于**语义**：涨跌、风险等级、待确认状态；不能当装饰背景大面积使用。

## 2. 字体与字阶

字体栈：
```
--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
--font-mono: ui-monospace, SFMono-Regular, "SF Mono", Consolas, "Liberation Mono", monospace;
```
数字（金额/百分比/净值/份额）统一 `--font-mono` 或 `tabular-nums`。

| 角色 | 字号/字重/字距 | 用途 |
|---|---|---|
| display | 28px / 600 / -0.6px | 面板大标题 |
| title | 20px / 600 / -0.4px | 卡片标题 |
| body | 14px / 400 / 0 | 正文 |
| body-sm | 13px / 400 | 表格、次级信息 |
| caption | 12px / 400 | 说明 |
| eyebrow | 11px / 600 / +0.08em 大写 | 分组小标签 |
| mono | 13px / 400 | 数字、代码 |
| metric | 22px / 600 / -0.4px | KPI 数字 |

## 3. 间距 / 圆角 / 描边

- 间距阶：4 · 8 · 12 · 16 · 24 · 32（页面水平内边距 24，卡片内边距 20，卡片间距 16）。
- 圆角：`xs 4` · `sm 6` · `md 8`（按钮/输入） · `lg 12`（卡片）。（大容器不用 pill）
- 层次靠 **1px 描边 + 表面色阶**，不靠阴影。

## 4. 组件规范

| 组件 | 规格 |
|---|---|
| 顶部栏 | 高 56px，`--canvas` 底 + 底部 1px `--hairline`；标题 14/600 |
| 侧边栏 | 宽 232px，`--surface-1` 底，右侧 1px 描边；项 13px，选中用 `--surface-2` + 左侧 2px `--primary` |
| 卡片 | `--surface-1` + 1px `--hairline` + 圆角 12 + 内边距 20；标题 caption/大写/eyebrow |
| 主按钮 | `--primary` 底 + 白字 + 圆角 8 + padding 8/14 + 14/500 |
| 次按钮 | `--surface-1` 底 + `--ink` + 1px 描边 |
| 危险按钮 | 透明底 + `--down` 文字 + `--down` 30% 描边 |
| 输入框 | `--surface-2` 底 + 1px 描边 + 圆角 8 + padding 8/12 + 聚焦 `--primary` 环 |
| 状态徽标 | `--surface-2` 底 + `--ink-subtle` 字 + 圆角 999 + 11px |
| 表格/列表行 | 行高 32，分隔线 `rgba(255,255,255,.05)`，数字右对齐等宽 |
| 图表 | 主线 `--primary` 或语义色；网格 `#20242e` 1px；不用渐变填充以外的装饰 |

## 5. 信息密度与布局

- KPI 行：每格 `label(caption) + metric + sub(caption)`，数值右对齐等宽。
- 一屏内优先“结论在前，明细可折叠”（`<details>`）。
- 网格不留空单元格；卡片数量克制（一个区域 3–5 个）。
- 金额统一两位小数：`¥1,234.56`。

## 6. 状态与空态

每个数据区块必须有三态：**加载中**（细 spinner + 文案）、**空**（说明 + 下一步动作）、**失败**（原因 + 重试）。
“待确认/未起算”属于正常状态，用 `--warn` 明确标注，不能显示成 0 或空白误导用户。

## 7. 变更流程（impeccable 的 audit → fix）

1. 审计现状 → 列出与本文档不符之处；
2. 只做定向修复，不动业务逻辑与接口；
3. 改完在浏览器里核对（对比度、对齐、数字等宽、三态）。
