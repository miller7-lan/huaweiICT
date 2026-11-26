"""Draw appendix figures 2-1 (architecture) and 2-2 (retrieval-reasoning dual-drive flow) for V4.0.

Writes docs/competition/assets/v4-figures/v4-arch.svg and v4-dualdrive.svg. Content follows the
V4.0 freeze (460a2be) and the published V4.0.1 agent; the fixed-scenario chains keep their own models.
"""
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parents[1] / "docs/competition/assets/v4-figures"
FONT = "Microsoft YaHei, Noto Sans CJK SC, SimHei, sans-serif"
C = {"blue": ("#EAF2FB", "#3A6EA5", "#1F3F66"), "green": ("#EAF5EC", "#2E7D4A", "#1E5C34"),
     "gold": ("#FFF6E2", "#B98A2A", "#7A5A12"), "purple": ("#F1EEFA", "#6A5AA8", "#43367A"),
     "grey": ("#F3F5F8", "#7A8899", "#3E4A57"), "red": ("#FBECEC", "#A63C3C", "#8E2B2B"),
     "orange": ("#FDF3E6", "#B8691F", "#7A4512")}


def svg(w, h, parts, markers=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}"><defs>{markers}</defs>'
            f'<rect width="{w}" height="{h}" fill="white"/><g font-family="{FONT}">' + "".join(parts) + "</g></svg>")


def box(parts, x, y, w, h, kind, lines, size=26, bold_first=False, rx=12):
    fill, stroke, ink = C[kind]
    parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="2.6"/>')
    n = len(lines)
    for i, line in enumerate(lines):
        ty = y + h / 2 + (i - (n - 1) / 2) * (size + 8) + size * 0.36
        weight = ' font-weight="bold"' if bold_first and i == 0 else ""
        parts.append(f'<text x="{x + w / 2}" y="{ty}" text-anchor="middle" font-size="{size}" fill="{ink}"{weight}>{escape(line)}</text>')


def arch():
    W, H = 1700, 1080
    parts = [f'<text x="40" y="56" font-size="36" font-weight="bold" fill="#1F3F66">系统总体架构（V4.0）</text>']
    layers = [("交互层", "blue", [["Nexent 对话", "与调试界面"], ["跨文档问答", "智能体 V4.0.1"], ["固定情境", "推理演示"], ["维护者", "确认入口"]]),
              ("编排层", "green", [["主控 Agent", "+ Skill"], ["检索 / 核验", "子 Agent"], ["整理子 Agent", "（更新组）"], ["问答智能体", "只调两个流程工具"]]),
              ("工具层", "gold", [["govkb_flow_open", "范围门·检索·任务"], ["govkb_flow_submit", "逐条核验·修复·补查"], ["本体依赖", "计划执行"], ["任务状态", "预算与完成门"]]),
              ("知识与本体层", "purple", [["本体种子", "7 类 6 关系"], ["候选与", "人工结构确认"], ["不可变版本", "与回退"], ["593 单元", "冻结检索语料"]]),
              ("数据资产层", "grey", [["HTML 指南", "问答·解读"], ["Excel", "收费目录"], ["扫描 PDF", "AI 转录"], ["原件哈希", "修订·定位"]])]
    top, lh, gap = 90, 158, 22
    for i, (name, kind, cells) in enumerate(layers):
        y = top + i * (lh + gap)
        fill, stroke, ink = C[kind]
        parts.append(f'<rect x="40" y="{y}" width="1250" height="{lh}" rx="16" fill="{fill}" stroke="{stroke}" stroke-width="2" opacity="0.9"/>')
        parts.append(f'<text x="150" y="{y + lh / 2 + 11}" text-anchor="middle" font-size="31" font-weight="bold" fill="{ink}">{escape(name)}</text>')
        for j, cell in enumerate(cells):
            box(parts, 280 + j * 252, y + 22, 232, lh - 44, kind, cell, size=21 if cell[0].startswith("govkb") else 24)
        if i < len(layers) - 1:
            parts.append(f'<text x="665" y="{y + lh + 18}" text-anchor="middle" font-size="22" fill="#666">▼ ▲</text>')
    y0, y1 = top, top + 5 * lh + 4 * gap
    parts.append(f'<rect x="1320" y="{y0}" width="340" height="{y1 - y0}" rx="16" fill="#FBECEC" stroke="#A63C3C" stroke-width="2"/>')
    parts.append(f'<text x="1490" y="{y0 + 50}" text-anchor="middle" font-size="31" font-weight="bold" fill="#8E2B2B">运行保障</text>')
    side = [["主控心跳与", "计划任务恢复"], ["官方来源观察", "与到期采集"], ["范围门·暂停门", "人工确认门"], ["每题最多 3 次", "调用，服务端计数"], ["冻结回执·指纹", "异机复现比对"]]
    for k, cell in enumerate(side):
        box(parts, 1345, y0 + 80 + k * 162, 290, 132, "red", cell, size=24)
    notes = ["模型：跨文档问答为百炼 kimi-k3.1-pro（关闭思考，输出上限 2048）；固定情境与历史草稿为 DeepSeek deepseek-flash（温度 0，非思考）。",
             "本机 Nexent v2.7.0 与 MCP 服务直连；云端调用保留 HTTPS 校验。依据冻结提交 460a2be 与已发布的 V4.0.1 智能体绘制。"]
    for k, n in enumerate(notes):
        parts.append(f'<text x="40" y="{H - 52 + 32 * k}" font-size="22" fill="#555">{escape(n)}</text>')
    return svg(W, H, parts)


def dualdrive():
    W, H = 2000, 940
    m = "".join(f'<marker id="{k}" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{c}"/></marker>'
                for k, c in {"a": "#4A5568", "r": "#A63C3C", "g": "#2E7D4A", "o": "#B8691F"}.items())
    parts = [f'<text x="40" y="56" font-size="36" font-weight="bold" fill="#1F3F66">检索-推理双驱动查询流程（V4.0）</text>',
             f'<text x="40" y="100" font-size="24" fill="#555">模型负责组织表述；范围判断、检索、计划执行与核验都由 MCP 工具确定性完成。</text>']

    def arrow(points, color="#4A5568", mark="a", label=None, at=None, dashed=False, anchor="start"):
        d = " ".join(f"{x},{y}" for x, y in points)
        dash = ' stroke-dasharray="11 8"' if dashed else ""
        parts.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="3"{dash} marker-end="url(#{mark})"/>')
        if label:
            parts.append(f'<text x="{at[0]}" y="{at[1]}" font-size="23" fill="{color}" text-anchor="{anchor}">{escape(label)}</text>')

    box(parts, 40, 330, 200, 120, "blue", ["用户提问"], size=28, bold_first=True)
    box(parts, 300, 315, 240, 150, "gold", ["范围门", "地区·事项·隐私", "现行效力问题"], size=24, bold_first=True)
    arrow([(240, 390), (296, 390)])
    # upper lane: fixed scenario
    parts.append('<text x="620" y="168" font-size="25" font-weight="bold" fill="#43367A">固定情境（本体驱动）</text>')
    box(parts, 610, 190, 270, 120, "purple", ["主控读取 Skill", "调度子 Agent"], size=24)
    box(parts, 940, 190, 300, 120, "purple", ["本体依赖计划", "逐节点绑定证据"], size=24)
    box(parts, 1300, 190, 270, 120, "purple", ["核验与完成门", "引文·数字·版本"], size=24)
    arrow([(540, 360), (575, 360), (575, 250), (606, 250)], label="已确认情境", at=(470, 236))
    arrow([(880, 250), (936, 250)]); arrow([(1240, 250), (1296, 250)])
    # lower lane: free question
    parts.append('<text x="620" y="508" font-size="25" font-weight="bold" fill="#1F3F66">自由提问（检索驱动，V4.0.1 智能体）</text>')
    box(parts, 610, 530, 270, 120, "blue", ["govkb_flow_open", "检索 593 单元证据"], size=24)
    box(parts, 940, 530, 300, 120, "blue", ["模型草稿", "结论＋来源＋逐字引文"], size=24)
    box(parts, 1300, 530, 270, 120, "blue", ["govkb_flow_submit", "逐条核验"], size=24)
    arrow([(540, 420), (575, 420), (575, 590), (606, 590)])
    arrow([(880, 590), (936, 590)]); arrow([(1240, 590), (1296, 590)])
    box(parts, 1300, 720, 270, 110, "orange", ["修复一次 / 补查一次", "仍未过则逐条取舍"], size=23)
    arrow([(1435, 650), (1435, 716)], "#B8691F", "o", "未通过", (1447, 694))
    arrow([(1300, 775), (1090, 775), (1090, 654)], "#B8691F", "o", "改写或补证据后重交", (1080, 812), dashed=True, anchor="end")
    # outcomes
    box(parts, 1660, 190, 300, 120, "green", ["带引用的结论", "＋未知项"], size=25)
    box(parts, 1660, 470, 300, 150, "green", ["完整答复 / 留缺口", "/ 部分答复", "附原文与定位"], size=24)
    box(parts, 1660, 690, 300, 140, "red", ["停止作答", "说明缺少的依据"], size=25)
    arrow([(1570, 250), (1656, 250)], "#2E7D4A", "g", "通过", (1580, 236))
    arrow([(1570, 575), (1656, 560)], "#2E7D4A", "g", "通过", (1580, 552))
    arrow([(1570, 800), (1656, 770)], "#A63C3C", "r", "无结论通过，或问新旧规定适用且缺过渡条款", (1790, 866), anchor="end")
    arrow([(420, 465), (420, 895), (1810, 895), (1810, 834)], "#A63C3C", "r", "超范围 / 问现行效力 / 含个人信息", (440, 883))
    parts.append(f'<text x="40" y="{H - 8}" font-size="21" fill="#555">{escape("实线为主路径，虚线为回路；固定情境沿用 10 月验收的主控—检索—核验三角色，自由提问为 V4.0 冻结流程（460a2be）。")}</text>')
    return svg(W, H + 30, parts, m)


OUT.mkdir(parents=True, exist_ok=True)
(OUT / "v4-arch.svg").write_text(arch(), "utf-8")
(OUT / "v4-dualdrive.svg").write_text(dualdrive(), "utf-8")
print(OUT)
