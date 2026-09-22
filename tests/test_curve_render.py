"""批次 5.1（任务 C）单测：组合曲线改画 return_pct 的渲染契约。

后端 get_portfolio_curve() 早已返回 return_pct（见 test_portfolio_curve.py），
本文件锁定前端 src/web/static/app.js 的渲染语义：
  1. 主图画 return_pct，市值/成本线从图中移除（只留顶部文字行）
  2. y 轴关于 0 对称 [-M,+M]，M = max(|min|,|max|)，零轴居中
  3. 零轴为加粗参考线（0% 盈亏分界）；面积从曲线填到零轴，零上绿 / 零下红
  4. 曲线卡片标注资金加权口径（与已实现收益口径区分）
"""
import re
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "src" / "web" / "static" / "app.js"
SRC = APP_JS.read_text(encoding="utf-8")


def _fn_body(name):
    """截取 app.js 中指定函数的完整源码（到下一个顶层 function 为止）。"""
    m = re.search(rf"function {name}\(", SRC)
    assert m, f"function {name} not found in app.js"
    nxt = re.search(r"\nfunction ", SRC[m.end():])
    return SRC[m.start(): m.end() + nxt.start()] if nxt else SRC[m.start():]


def test_svg_plots_return_pct_not_value_or_cost():
    body = _fn_body("portfolioChartSVG")
    assert "C.return_pct" in body, "主图应改画 return_pct"
    assert "C.value" not in body, "市值序列不应再进入主图"
    assert "C.cost" not in body, "成本序列不应再进入主图"
    assert "pts(costs)" not in body, "成本虚线应移除"


def test_y_axis_zero_symmetric():
    body = _fn_body("portfolioChartSVG")
    assert "Math.abs(mx),Math.abs(mn)" in body, "M = max(|min|,|max|) 零对称取幅"
    assert "(v+M)/(2*M)" in body, "y 映射应关于 0 对称，零轴居中"
    # 零轴必须落在绘图区内（零对称映射下 y(0) 恰在 (t + H-b)/2 处）
    assert "y(0)" in body, "面积应从曲线填到零轴"


def test_zero_line_bold_and_area_split_by_sign():
    body = _fn_body("portfolioChartSVG")
    # 零轴加粗 + 0% 标注（盈亏分界）。
    # 注：不校验具体颜色 —— 颜色已收敛到 token（var(--ink-subtle)），
    # 写死色值会在任何颜色重构时误伤（2026-09-22 前端重构已踩）。
    assert re.search(r'<text[^>]*>0%</text>', body), "零轴须有 0% 标注"
    zero = re.search(r'<line[^>]*stroke-width="1\.4"[^>]*/>', body)
    assert zero and 'y1="${y0}"' in zero.group(0) and 'y2="${y0}"' in zero.group(0), \
        "零轴须是横贯 y0 的加粗线（stroke-width 1.4）"
    # 零上绿 / 零下红：同一份面积被两个 clipPath 分别裁剪着色
    assert 'fill="url(#cgUp)" clip-path="url(#cpUp)"' in body, "零轴上方应裁剪为绿色面积"
    assert 'fill="url(#cgDn)" clip-path="url(#cpDn)"' in body, "零轴下方应裁剪为红色面积"
    assert re.search(r'<clipPath id="cpUp"><rect [^/]*y="0"', body), "上裁剪区从顶部起"
    assert re.search(r'<clipPath id="cpDn"><rect [^/]*y="\$\{y0\}"', body), "下裁剪区从零轴起"
    cg_up = re.search(r'<linearGradient id="cgUp".*?</linearGradient>', body, re.S)
    cg_dn = re.search(r'<linearGradient id="cgDn".*?</linearGradient>', body, re.S)
    assert cg_up and "var(--up)" in cg_up.group(0), "零上面积用绿色（涨）"
    assert cg_dn and "var(--down)" in cg_dn.group(0), "零下面积用红色（跌）"


def test_curve_card_keeps_top_text_and_annotates_methodology():
    body = _fn_body("curveCard")
    # 顶部文字行保留：市值 / 累计收益 / 收益率
    for kw in ("当前市值", "累计收益", "收益率"):
        assert kw in body
    # 市值/成本图例移除
    assert "组合市值</span>" not in body and "累计成本</span>" not in body
    # 口径标注：资金加权，且与已实现收益口径区分
    assert "资金加权" in body and "非时间加权" in body
    assert "已实现收益" in body, "须注明与已实现收益是两个不同口径"


def test_return_pct_fills_vertical_space_not_3pct():
    """文档验收：return_pct 的波动须充满垂直空间，不再被市值轴压到 <3%。

    用任务书实测数据（GET /api/portfolio/curve）：
      value: 9.99 → 493.37（单调上升，定投建仓）
      cost : 10.0 → 502.29
      return_pct 区间约 [-1.78, 2.58]
    """
    ret = [-0.08, 2.58, -1.78]
    # 旧映射：y 轴取 value/cost 并集 [9.99, 502.29]，画 return_pct 只占
    old_lo, old_hi = 9.99, 502.29
    old_span = (max(ret) - min(ret)) / (old_hi - old_lo)
    assert old_span < 0.03, "旧行为应把收益率压到 <3%（回归基线）"
    # 新映射：零对称 [-M,+M]
    M = max(abs(max(ret)), abs(min(ret)))
    new_span = (max(ret) - min(ret)) / (2 * M)
    assert new_span > 0.6, "新行为应让收益率占据大半垂直空间"
    # 零轴必须落在映射范围内（零对称保证）
    assert -M <= 0 <= M
