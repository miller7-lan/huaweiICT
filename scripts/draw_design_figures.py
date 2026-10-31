"""Draw the design-document figures (architecture, query flow, update flow) as PNG.

Needs matplotlib, which is not in the project lock; run it from a separate
environment. Output: docs/competition/assets/design/图*.png at 200 dpi.
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/competition/assets/design"
FONT = "C:/Windows/Fonts/msyh.ttc"
font_manager.fontManager.addfont(FONT)
plt.rcParams["font.family"] = font_manager.FontProperties(fname=FONT).get_name()
INK, LINE = "#1F2937", "#475569"
# Canvases are 7.5 in wide and placed at 14.5-15.5 cm, so text prints at roughly 7.5-8 pt.
SCALE = 1.15
LAYER = {"交互": "#E0F2FE", "编排": "#DCFCE7", "工具": "#FEF3C7", "知识": "#EDE9FE", "数据": "#F1F5F9", "保障": "#FFE4E6"}


def box(ax, x, y, w, h, text, face="#FFFFFF", edge=LINE, size=10, bold=False, radius=0.02):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
                                facecolor=face, edgecolor=edge, linewidth=1))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size * SCALE, color=INK,
            fontweight="bold" if bold else "normal", linespacing=1.35)


def arrow(ax, start, end):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=11, color=LINE, linewidth=1.1))


def note(ax, x, y, text, ha="center", size=8.5):
    ax.text(x, y, text, ha=ha, va="center", fontsize=size * SCALE, color=LINE, linespacing=1.3)


def canvas(width, height):
    fig, ax = plt.subplots(figsize=(width, height))
    # Let the axes fill the canvas so box fractions map to the full printed width.
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    return fig, ax


def architecture():
    fig, ax = canvas(7.5, 5.2)
    layers = [
        ("交互层", "交互", ["Nexent 对话\n与调试界面", "自由提问\n问答智能体", "固定情境\n推理演示", "维护者\n确认入口"]),
        ("编排层\nAgent + Skill", "编排", ["主控 Agent", "检索子 Agent", "核验子 Agent", "整理子 Agent"]),
        ("工具层\nMCP 服务", "工具", ["检索与\n范围检查", "本体依赖\n计划执行", "引文·数值\n·单位核验", "任务状态\n与完成门"]),
        ("知识与\n本体层", "知识", ["本体种子\n7 类 6 关系", "候选与\n人工结构确认", "不可变版本\n与回退", "类型化\n依赖计划"]),
        ("数据\n资产层", "数据", ["HTML 指南\n问答·解读", "Excel\n收费目录", "扫描 PDF\n附件", "原件哈希\n修订·定位"]),
    ]
    top, height, gap = 0.93, 0.155, 0.025
    for index, (title, key, items) in enumerate(layers):
        y = top - (index + 1) * height - index * gap + gap
        ax.add_patch(FancyBboxPatch((0.02, y), 0.76, height, boxstyle="round,pad=0,rounding_size=0.015",
                                    facecolor=LAYER[key], edgecolor="#CBD5E1", linewidth=1))
        ax.text(0.10, y + height / 2, title, ha="center", va="center", fontsize=10 * SCALE, fontweight="bold",
                color=INK, linespacing=1.3)
        for column, item in enumerate(items):
            box(ax, 0.195 + column * 0.145, y + 0.025, 0.133, height - 0.05, item, size=8.8)
        if index < len(layers) - 1:
            arrow(ax, (0.40, y - 0.002), (0.40, y - gap + 0.002))
            arrow(ax, (0.56, y - gap + 0.002), (0.56, y - 0.002))
    ax.add_patch(FancyBboxPatch((0.80, 0.07), 0.18, 0.86, boxstyle="round,pad=0,rounding_size=0.015",
                                facecolor=LAYER["保障"], edgecolor="#CBD5E1", linewidth=1))
    ax.text(0.89, 0.895, "运行保障", ha="center", va="center", fontsize=10 * SCALE, fontweight="bold", color=INK)
    for row, item in enumerate(["主控心跳与\n计划任务恢复", "官方来源观察\n与到期采集", "暂停门与\n人工确认门",
                                "预算预留与\n调用上限", "回执·指纹\n与审计"]):
        box(ax, 0.815, 0.735 - row * 0.155, 0.15, 0.12, item, size=8.8)
    note(ax, 0.40, 0.025, "云端语言模型：DeepSeek（温度 0，非思考模式，经系统代理并保留 HTTPS 校验）；本机 Nexent 与 MCP 服务直连")
    return fig


def query_flow():
    fig, ax = canvas(7.5, 3.5)
    y, h, w = 0.52, 0.22, 0.13
    box(ax, 0.006, y, 0.104, h, "用户提问", size=9)
    box(ax, 0.14, y, 0.12, h, "主控 Agent\n读取 Skill", size=9)
    cx, half = 0.36, 0.075
    ax.add_patch(Polygon([(cx, y + h + 0.03), (cx + half, y + h / 2), (cx, y - 0.03), (cx - half, y + h / 2)],
                         closed=True, facecolor="#FEF3C7", edgecolor=LINE))
    ax.text(cx, y + h / 2, "范围检查", ha="center", va="center", fontsize=9 * SCALE, color=INK)
    box(ax, 0.47, y, w, h, "检索\n前 5 条证据\n来源门检查", size=8.8)
    box(ax, 0.63, y, w, h, "推理\n本体依赖计划\n或规则约束", size=8.8)
    box(ax, 0.79, y, w, h, "核验\n引文逐字\n数字·单位·版本", size=8.8)
    arrow(ax, (0.11, y + h / 2), (0.14, y + h / 2))
    arrow(ax, (0.26, y + h / 2), (cx - half, y + h / 2))
    arrow(ax, (cx + half, y + h / 2), (0.47, y + h / 2))
    note(ax, 0.448, y + h / 2 + 0.06, "范围内", size=8)
    arrow(ax, (0.60, y + h / 2), (0.63, y + h / 2))
    arrow(ax, (0.76, y + h / 2), (0.79, y + h / 2))
    box(ax, 0.72, 0.85, 0.27, 0.13, "通过：带引用答复 + 未知项\n+ 未核定现行效力说明", face="#DCFCE7", size=8.8)
    arrow(ax, (0.855, y + h), (0.855, 0.85))
    box(ax, 0.20, 0.04, 0.42, 0.16, "停止作答：说明停止原因与缺少的依据，\n列出相关归档原文供人工核对", face="#FFE4E6", size=9)
    arrow(ax, (cx, y - 0.03), (cx, 0.20))
    note(ax, cx - 0.012, 0.33, "超范围 / 问现行效力\n/ 含个人信息", ha="right")
    arrow(ax, (0.855, y), (0.62, 0.12))
    note(ax, 0.77, 0.30, "未通过", ha="left")
    note(ax, 0.30, 0.95, "模型负责调度与组织表述；\n检索、计划执行与核验由 MCP 工具确定性完成", size=9)
    return fig


def update_flow():
    fig, ax = canvas(7.5, 3.0)
    y, h, w, step = 0.52, 0.22, 0.128, 0.1425
    steps = ["来源观察\n到期采集", "发现新资料\n原件哈希·修订", "整理子 Agent\n新旧视图差异", "核验子 Agent\n独立复核证据",
             "待审候选\n不继承旧批准", "人工确认\n结构 / 业务", "激活新版本\n不可变·可回退"]
    faces = ["#F1F5F9", "#F1F5F9", "#DCFCE7", "#DCFCE7", "#FEF3C7", "#EDE9FE", "#E0F2FE"]
    xs = [0.005 + i * step for i in range(len(steps))]
    for x, label, face in zip(xs, steps, faces):
        box(ax, x, y, w, h, label, face=face, size=8.4)
    for left, right in zip(xs, xs[1:]):
        arrow(ax, (left + w, y + h / 2), (right, y + h / 2))
    box(ax, 0.30, 0.05, 0.42, 0.17, "来源变化、撤权、暂停或版本不符：\n阻断旧结果，不自动发布", face="#FFE4E6", size=9)
    arrow(ax, (xs[1] + w / 2, y), (0.38, 0.22))
    note(ax, 0.20, 0.36, "来源或版本变化")
    note(ax, 0.5, 0.93, "结构变化（本体类型、关系、执行语义）与内容变化（资料修订、差异候选）分开管理", size=9)
    return fig


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, figure in (("图1-系统总体架构.png", architecture), ("图2-检索推理双驱动查询流程.png", query_flow),
                         ("图3-知识更新与本体演化流程.png", update_flow)):
        fig = figure()
        fig.savefig(OUT / name, dpi=200, bbox_inches="tight", facecolor="white")
        plt.close(fig)


if __name__ == "__main__":
    main()
