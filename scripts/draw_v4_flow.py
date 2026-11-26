"""Draw figure 1-1 for V4.0: the bounded cross-document flow as the Nexent agent runs it.

Writes docs/competition/assets/v4-figures/v4-flow.svg. Layout follows the V3 figure; boxes and loops
follow frozen_qa_flow_v3.py / frozen_qa_flow_server.py at the V4.0 freeze (460a2be).
"""
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parents[1] / "docs/competition/assets/v4-figures/v4-flow.svg"
W, H, BW, BH = 2200, 1420, 300, 140
X = [40, 400, 760, 1120, 1480, 1840]
Y = {1: 190, 2: 440, 3: 680, 4: 910, 5: 1130}
STYLE = {"blue": ("#EAF2FB", "#3A6EA5", "#1F3F66"), "red": ("#FBECEC", "#A63C3C", "#8E2B2B"),
         "orange": ("#FDF3E6", "#B8691F", "#7A4512"), "green": ("#EAF5EC", "#2E7D4A", "#1E5C34"),
         "gold": ("#FFF6E2", "#B98A2A", "#7A5A12")}
parts = []


def box(col, row, kind, title, *lines):
    fill, stroke, ink = STYLE[kind]
    x, y = X[col], Y[row]
    parts.append(f'<rect x="{x}" y="{y}" width="{BW}" height="{BH}" rx="14" fill="{fill}" stroke="{stroke}" stroke-width="3"/>')
    top = y + (52 if lines else 82)
    parts.append(f'<text x="{x + BW / 2}" y="{top}" text-anchor="middle" font-size="34" font-weight="bold" fill="{ink}">{escape(title)}</text>')
    for i, line in enumerate(lines):
        parts.append(f'<text x="{x + BW / 2}" y="{top + 40 + 34 * i}" text-anchor="middle" font-size="25" fill="#333">{escape(line)}</text>')


def path(points, color, label=None, at=None, dashed=False, anchor="start"):
    d = " ".join(f"{px},{py}" for px, py in points)
    dash = ' stroke-dasharray="12 9"' if dashed else ""
    marker = {"#3A6EA5": "b", "#A63C3C": "r", "#B8691F": "o", "#2E7D4A": "g", "#B98A2A": "y"}[color]
    parts.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="3.2"{dash} marker-end="url(#{marker})"/>')
    if label:
        parts.append(f'<text x="{at[0]}" y="{at[1]}" font-size="25" fill="{color}" text-anchor="{anchor}">{escape(label)}</text>')


def mid(col, row, side):
    x, y = X[col], Y[row]
    return {"l": (x, y + BH / 2), "r": (x + BW, y + BH / 2), "t": (x + BW / 2, y), "b": (x + BW / 2, y + BH)}[side]


def bracket(x1, x2, text, color):
    y = 150
    parts.append(f'<polyline points="{x1},{y + 18} {x1},{y} {x2},{y} {x2},{y + 18}" fill="none" stroke="{color}" stroke-width="2.4"/>')
    parts.append(f'<text x="{(x1 + x2) / 2}" y="{y - 12}" text-anchor="middle" font-size="24" fill="{color}" font-weight="bold">{escape(text)}</text>')


B, R, O, G, Yl = "#3A6EA5", "#A63C3C", "#B8691F", "#2E7D4A", "#B98A2A"
parts.append(f'<text x="40" y="64" font-size="40" font-weight="bold" fill="#1F3F66">跨文档问答流程 V4.0：有出处才回答，证据不足就停止</text>')
bracket(X[1], X[2] + BW, "MCP 工具 govkb_flow_open（打开任务）", B)
bracket(X[3], X[3] + BW, "智能体（kimi-k3.1-pro）", "#555")
bracket(X[4], X[4] + BW, "govkb_flow_submit", O)
bracket(X[5], X[5] + BW, "final_answer", G)

box(0, 1, "blue", "用户提问", "群众或工作人员", "自然语言提问")
box(1, 1, "blue", "范围门", "地区·事项·隐私", "现行效力问题")
box(2, 1, "blue", "检索", "593 单元冻结语料", "证据＋作答规则")
box(3, 1, "blue", "模型草稿", "结论＋来源", "＋逐字引文")
box(4, 1, "blue", "逐条核验", "引文·数字·单位", "日期·文号")
box(5, 1, "green", "完整答复", "缺项列为未知项", "附原文与定位")
box(1, 2, "red", "停止：超出范围", "说明缺少的依据")
box(2, 2, "orange", "补查一次", "按模型列出的缺项")
box(4, 2, "orange", "修复一次", "失败码退回模型重写")
box(5, 2, "red", "停止：过渡不明", "问新旧规定适用", "且缺过渡条款")
box(2, 3, "red", "停止", "没有新证据或仍不足")
box(4, 3, "orange", "逐条取舍", "通过的保留", "转述未过改原文摘录")
box(5, 3, "green", "部分答复", "标注部分答复", "展示内容再核验")
box(4, 4, "red", "停止", "没有一条结论通过")

path([mid(0, 1, "r"), mid(1, 1, "l")], B)
path([mid(1, 1, "r"), mid(2, 1, "l")], B, "通过", (X[1] + BW + 6, Y[1] + 40))
path([mid(2, 1, "r"), mid(3, 1, "l")], B)
path([mid(3, 1, "r"), mid(4, 1, "l")], B)
path([mid(4, 1, "r"), mid(5, 1, "l")], G, "通过", (X[4] + BW + 6, Y[1] + 40))
path([mid(1, 1, "b"), mid(1, 2, "t")], R, "命中", (X[1] + BW / 2 + 12, Y[1] + BH + 60))
dx = X[3] + 70
path([(dx, Y[1] + BH), (dx, Y[2] + BH / 2), mid(2, 2, "r")], O, "缺项待补", (dx + 12, Y[1] + BH + 60), dashed=True)
ux = X[2] + 70
path([(ux, Y[2]), (ux, Y[1] + BH)], O, "有新证据，重新作答", (ux + 12, Y[1] + BH + 60), dashed=True)
path([mid(2, 2, "b"), mid(2, 3, "t")], R, "没有", (X[2] + BW / 2 + 12, Y[2] + BH + 60))
path([mid(4, 1, "b"), mid(4, 2, "t")], O, "未通过", (X[4] + BW / 2 + 12, Y[1] + BH + 60))
rx = X[3] + BW - 60
path([mid(4, 2, "l"), (rx, Y[2] + BH / 2), (rx, Y[1] + BH)], O, "按失败码改写", (rx - 12, Y[2] + BH / 2 - 16), dashed=True, anchor="end")
path([mid(4, 2, "b"), mid(4, 3, "t")], O, "仍未通过", (X[4] + BW / 2 + 12, Y[2] + BH + 60))
path([mid(4, 3, "r"), mid(5, 3, "l")], G, "有通过", (X[4] + BW + 6, Y[3] + 40))
path([mid(4, 3, "b"), mid(4, 4, "t")], R, "没有", (X[4] + BW / 2 + 12, Y[3] + BH + 60))
path([mid(5, 1, "b"), mid(5, 2, "t")], R, "过渡规则", (X[5] + BW / 2 + 12, Y[1] + BH + 60))

parts.append(f'<line x1="30" y1="{Y[5] - 50}" x2="{W - 30}" y2="{Y[5] - 50}" stroke="#CCC" stroke-width="2"/>')
parts.append(f'<text x="40" y="{Y[5] - 14}" font-size="28" font-weight="bold" fill="{Yl}">持续更新与人工把关</text>')
box(0, 5, "gold", "发现新资料", "主控心跳与", "官方来源观察")
box(1, 5, "gold", "生成待审候选", "整理与核验", "子智能体")
box(2, 5, "gold", "人工确认", "确认前不发布")
box(3, 5, "gold", "新版本生效", "供检索使用", "历史版本可回退")
box(5, 5, "gold", "工作人员复核", "按原文定位", "与版本核对")
for c in range(3):
    path([mid(c, 5, "r"), mid(c + 1, 5, "l")], Yl)
path([mid(5, 3, "b"), mid(5, 5, "t")], Yl, "答复均附定位", (X[5] + BW / 2 + 12, Y[3] + BH + 60))
path([(X[5] + BW, Y[1] + BH / 2 + 30), (W - 24, Y[1] + BH / 2 + 30), (W - 24, Y[5] + BH / 2), (X[5] + BW + 4, Y[5] + BH / 2)], Yl)

notes = ["每题最多 3 次模型草稿，计数在服务端；智能体只调用两个 MCP 工具，最终答复只用核验后的 final 对象。",
         "实线为主路径，虚线为回路。依据冻结提交 460a2be 的 frozen_qa_flow_v3.py 与 frozen_qa_flow_server.py 绘制。"]
for i, n in enumerate(notes):
    parts.append(f'<text x="40" y="{H - 62 + 36 * i}" font-size="24" fill="#555">{escape(n)}</text>')

marks = "".join(f'<marker id="{k}" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
                for k, c in {"b": B, "r": R, "o": O, "g": G, "y": Yl}.items())
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}"><defs>{marks}</defs>'
       f'<rect width="{W}" height="{H}" fill="white"/><g font-family="Microsoft YaHei, Noto Sans CJK SC, SimHei, sans-serif">'
       + "".join(parts) + "</g></svg>")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(svg, "utf-8")
print(OUT)
