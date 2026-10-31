"""Build chapters 1-2 of the development design document as .docx.

Needs python-docx, which is not in the project lock; run it from a separate
environment. Every number in the text comes from the dated evidence named in
docs/2026-10-18对话决策与接续记录.md sections 30-44. Output:
docs/competition/开发设计文档-第1-2章-20261031.docx
"""
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "docs/competition/assets/design"
OUTPUT = ROOT / "docs/competition/开发设计文档-第1-2章-20261031.docx"
BODY_FONT, HEAD_FONT, LATIN = "宋体", "黑体", "Times New Roman"


def set_fonts(target, east, size=None, bold=None, color=True):
    """Apply Latin and East Asian fonts; drop theme fonts that would override them."""
    font = target.font
    font.name = LATIN
    if size:
        font.size = Pt(size)
    if bold is not None:
        font.bold = bold
    if color:
        font.color.rgb = RGBColor(0, 0, 0)
    rpr = target.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for attribute in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        fonts.attrib.pop(qn(attribute), None)
    for attribute, value in (("w:ascii", LATIN), ("w:hAnsi", LATIN), ("w:eastAsia", east)):
        fonts.set(qn(attribute), value)


def configure(doc):
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.54)
    section.left_margin = section.right_margin = Cm(3.17)
    normal = doc.styles["Normal"]
    set_fonts(normal, BODY_FONT, 12)
    normal.paragraph_format.line_spacing = 1.5
    normal.paragraph_format.first_line_indent = Pt(24)
    normal.paragraph_format.space_after = Pt(0)
    for name, size, before, after in (("Heading 1", 16, 18, 12), ("Heading 2", 14, 12, 6), ("Heading 3", 12, 6, 3)):
        style = doc.styles[name]
        set_fonts(style, HEAD_FONT, size, True)
        style.paragraph_format.first_line_indent = Pt(0)
        style.paragraph_format.space_before, style.paragraph_format.space_after = Pt(before), Pt(after)
        style.paragraph_format.line_spacing = 1.3
        style.paragraph_format.keep_with_next = True
    bullet = doc.styles["List Bullet"]
    set_fonts(bullet, BODY_FONT, 12)
    bullet.paragraph_format.first_line_indent = Pt(0)
    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    header.paragraph_format.first_line_indent = Pt(0)
    set_fonts(header.add_run("政务资产认知与可进化决策智能体 · 开发设计文档"), BODY_FONT, 9)
    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, value in (("w:val", "single"), ("w:sz", "4"), ("w:space", "1"), ("w:color", "808080")):
        bottom.set(qn(key), value)
    border.append(bottom)
    header._p.get_or_add_pPr().append(border)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.paragraph_format.first_line_indent = Pt(0)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "1"
    run.append(text)
    field.append(run)
    footer._p.append(field)


class Writer:
    def __init__(self, doc):
        self.doc = doc

    def heading(self, text, level):
        self.doc.add_heading(text, level=level)

    def para(self, *parts, indent=True, align=None, size=None, east=BODY_FONT):
        """parts: plain strings, or (text, bold) pairs."""
        paragraph = self.doc.add_paragraph()
        if not indent:
            paragraph.paragraph_format.first_line_indent = Pt(0)
        if align:
            paragraph.alignment = align
        for part in parts:
            text, bold = (part, False) if isinstance(part, str) else part
            set_fonts(paragraph.add_run(text), east, size, bold)
        return paragraph

    def lead(self, label, text):
        return self.para((label, True), text)

    def bullets(self, items):
        for item in items:
            paragraph = self.doc.add_paragraph(style="List Bullet")
            if isinstance(item, tuple):
                set_fonts(paragraph.add_run(item[0]), BODY_FONT, None, True)
                set_fonts(paragraph.add_run(item[1]), BODY_FONT)
            else:
                set_fonts(paragraph.add_run(item), BODY_FONT)

    def caption(self, text):
        paragraph = self.para(text, indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, size=10.5, east=HEAD_FONT)
        paragraph.paragraph_format.space_before = Pt(3)
        paragraph.paragraph_format.space_after = Pt(6)
        return paragraph

    def table(self, caption, headers, rows, widths):
        self.caption(caption).paragraph_format.keep_with_next = True
        table = self.doc.add_table(rows=1 + len(rows), cols=len(headers))
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        # WPS and LibreOffice size columns from the grid, not from cell widths.
        for column, width in zip(table.columns, widths):
            column.width = Cm(width)
        layout = OxmlElement("w:tblLayout")
        layout.set(qn("w:type"), "fixed")
        table._tbl.tblPr.append(layout)
        for r, values in enumerate([headers] + rows):
            row = table.rows[r]
            if r == 0:
                properties = row._tr.get_or_add_trPr()
                repeat = OxmlElement("w:tblHeader")
                repeat.set(qn("w:val"), "true")
                properties.append(repeat)
            for c, value in enumerate(values):
                cell = row.cells[c]
                cell.width = Cm(widths[c])
                paragraph = cell.paragraphs[0]
                paragraph.paragraph_format.first_line_indent = Pt(0)
                paragraph.paragraph_format.line_spacing = 1.2
                set_fonts(paragraph.add_run(value), HEAD_FONT if r == 0 else BODY_FONT, 10.5, r == 0)
                if r == 0:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    shade = OxmlElement("w:shd")
                    for key, val in (("w:val", "clear"), ("w:color", "auto"), ("w:fill", "D9E2F3")):
                        shade.set(qn(key), val)
                    cell._tc.get_or_add_tcPr().append(shade)
        spacer = self.para("", indent=False)
        spacer.paragraph_format.line_spacing = 1.0

    def figure(self, filename, caption, width=15.5):
        paragraph = self.para("", indent=False, align=WD_ALIGN_PARAGRAPH.CENTER)
        paragraph.paragraph_format.keep_with_next = True
        # Body line spacing would multiply the inline picture's height.
        paragraph.paragraph_format.line_spacing = 1.0
        paragraph.paragraph_format.space_before = Pt(6)
        paragraph.runs[0].add_picture(str(FIGURES / filename), width=Cm(width))
        self.caption(caption)


def title_block(w):
    title = w.para(("基于 Nexent 的政务资产认知与可进化决策智能体", True), indent=False,
                   align=WD_ALIGN_PARAGRAPH.CENTER, size=18, east=HEAD_FONT)
    title.paragraph_format.space_after = Pt(6)
    w.para("开发设计文档（第一、二章）", indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, size=14, east=HEAD_FONT)
    w.para("第十一届华为 ICT 大赛中国创新赛 · 赛题 3", indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, size=10.5)
    w.para("基于华为 ModelEngine Nexent 打造可进化决策智能体", indent=False, align=WD_ALIGN_PARAGRAPH.CENTER, size=10.5)
    note = w.para("稿件日期：2026 年 10 月 31 日。学校、队名、成员等信息按官方模板填写。", indent=False,
                  align=WD_ALIGN_PARAGRAPH.CENTER, size=10.5)
    note.paragraph_format.space_after = Pt(12)


def chapter_one(w):
    w.heading("1 项目概述", 1)
    w.heading("1.1 背景与痛点", 2)
    w.para("政务服务领域积累了大量公开资料：办事指南、政策问答、政策解读、收费目录表格和扫描版政策附件。"
           "以居民身份证申领、换领、补领为例，同一事项的依据分散在不同部门、不同年份、不同格式的文件中，"
           "长期处于“能下载、难利用”的沉睡状态。群众咨询和窗口复核都需要把这些资料拼起来，并说明每个结论出自哪里。"
           "本项目在真实公开资料上归纳出以下五类痛点。")
    w.lead("（1）多模态异构，资料难以联通。", "收费依据的文号记在 Excel 收费目录的单元格里，收费分支写在财政部通知的网页正文中，"
           "历史便民措施只存在于扫描版 PDF。只做文本检索，难以把三类资料连成一条依据链。")
    w.lead("（2）跨文档口径复杂，容易答错分支。", "首次申领居民身份证的工本费自 2018 年 4 月 1 日起停征（财税〔2018〕37 号），"
           "换领仍按 2007 年通知每证 20 元（财综〔2007〕34 号）。回答“换领收多少钱”必须同时读两份文件，并区分首次申领、"
           "换领、补领等分支。")
    w.lead("（3）知识持续变化，旧依据仍需追溯。", "河南身份证办理时限由 2024 年措施的 25 个自然日调整为 2026 年措施的 15 个工作日。"
           "两者单位不同，不能直接相减；新资料到来时，旧依据也要保留，以便核对历史办件。")
    w.lead("（4）标注稀缺，属于典型低资源场景。", "政务领域缺少成规模的标注数据，而条款中的对象、地区、期限和例外又必须完全准确，"
           "难以依靠大规模标注训练专用模型。")
    w.lead("（5）可追溯要求高。", "面向群众的答复必须给出原文出处；资料不足或现行效力未确认时，系统应当明确停止，而不是猜测。")

    w.heading("1.2 项目目标与定位", 2)
    w.para("本项目基于华为 ModelEngine Nexent 平台，构建政务领域的资产认知与可进化决策智能体。"
           "以河南、南京、北京等地居民身份证业务的公开资料为试点，目标是：")
    w.bullets([
        ("多模态资产统一识别与标识：", "HTML、Excel、扫描 PDF 统一登记原件哈希、修订版本和精确定位，并转为可检索、可引用的资产单元。"),
        ("低资源半自动本体构建：", "从少量原文语法种子生成候选类型、关系和条件，人工确认结构后成为可执行的推理计划，并随资料变化持续演化。"),
        ("检索-推理双驱动执行流：", "借助 Nexent 的主控与子 Agent 编排、MCP 工具的确定性执行，完成跨文档多跳推理，每一步都可溯源。"),
        ("可复用 Skill 工作流模板：", "把验证过的编排模式沉淀为参数化模板，新事项、新地区按规格即可快速部署。"),
    ])
    w.para("系统服务三类用户：咨询群众获得带原文出处的答复；窗口工作人员按固定知识版本复核和交接；"
           "资料维护者登记来源、审核更新候选、管理知识版本。")

    w.heading("1.3 主要创新点", 2)
    w.lead("（1）多模态资产统一标识与跨格式推理。", "每份原件以 SHA-256 标识，修订与定位分开记录：HTML 记到段落和条文，"
           "Excel 记到单元格和行，扫描 PDF 记到 PDF 页码及左右半页。河南试点中，Excel、HTML、扫描 PDF 三种格式的"
           " 17 处定位共同进入同一条四节点推理计划，表格提供规范文号，网页提供收费分支，扫描件提供历史服务条件。")
    w.lead("（2）低资源半自动本体构建与演化。", "不依赖大规模标注：少量原文语法种子从完整条款中抽取候选，人工只需确认结构；"
           "确认后的关系成为带类型、带来源、带依赖的执行计划。版本不可变、可一键回退，来源发生变化时旧结果自动阻断。"
           "北京、南京、河南三地分别完成了新类型或新关系的构建与执行。")
    w.lead("（3）检索-推理双驱动与确定性核验。", "模型只负责任务调度和答复组织；检索、本体计划执行、引文逐字核对、数值与单位核对"
           "都由 MCP 工具完成。任何环节不通过即停止作答，并说明缺少什么依据。")
    w.lead("（4）可复用 Skill 工作流模板与自主更新。", "把“主控—检索—核验”的编排抽象为分层模板，由 profile 规格生成 Skill 和主控协议，"
           "河南链路的 Skill 可由模板逐字复现。主控心跳与官方来源观察发现新资料后，交由整理和核验子 Agent 生成待审更新候选。")

    w.heading("1.4 当前完成情况", 2)
    w.para("截至 2026 年 10 月 30 日，主要能力的实现与验证结果见表 1-1。其中南京、河南两条推理链、"
           "河南自主更新和自由提问问答均在本机 Nexent v2.7.0 上实际运行。")
    w.table("表 1-1 主要能力与验证结果", ["能力", "实现与验证结果"], [
        ["多模态冻结语料", "7 份官方原件（HTML、Excel、扫描 PDF）整理为 581 个检索单元，原件 SHA-256 全部核对一致；"
                     "资料目录累计登记 79 份文档、186 个修订（10 月 18 日快照）"],
        ["跨文档多跳推理（南京）", "本体控制的三步依赖计划在 Nexent 主控与两个子 Agent 中实跑，13 项验收全部通过，"
                         "7 次模型启动，耗时约 14.6 秒"],
        ["三模态归档推理（河南）", "Excel、HTML、扫描 PDF 共 17 处定位进入四节点计划，Nexent 实跑 15 项验收全部通过，"
                         "7 次模型启动，耗时约 13.2 秒"],
        ["自由提问问答", "Nexent 智能体通过检索、核验两个 MCP 工具作答；界面实测跨文档收费问题 4.07 秒给出带两处引用的答复，"
                   "询问“现在的收费”时按规则停止作答"],
        ["本体半自动构建（北京）", "从归档办事指南生成新类型和新关系，人工确认后在本地执行 6 个虚构场景，随后回退"],
        ["自主更新（河南）", "心跳在自然到期时发现新归档资料，整理与核验子 Agent 生成 1 条待审候选，正式版本未被改动"],
        ["运行保障", "主控进程退出后由计划任务约 4 秒恢复监听；官方来源观察完成首次采集并形成待审修订"],
        ["工程交付", "公开仓库以 MIT 许可开源；工程包解压到新目录后可自行重建，包内 392 项离线测试通过"],
    ], [4.8, 9.8])

    w.heading("1.5 应用价值与资产激活路径", 2)
    w.para("本项目把沉睡的公开资料逐步激活为可用的决策依据，路径为：原件登记与定位 → 检索单元与本体候选 → "
           "人工确认的可执行计划 → 带出处的答复与更新候选。在这条路径上，三类用户分别获益：")
    w.bullets([
        ("群众：", "直接得到带原文出处的答复；资料不足时会被明确告知原因和缺少的依据，不会拿到可能错误的结论。"),
        ("工作人员：", "答复绑定资料版本和定位，便于复核与交接；新旧政策的差异以待审候选形式呈现，审核后再生效。"),
        ("维护者：", "新增事项或地区时复用 Skill 模板和 MCP 工具，只需配置资料来源和推理计划，不必从零搭建智能体。"),
    ])

    w.heading("1.6 当前边界", 2)
    w.para("本文所述结果均来自冻结的公开历史资料。系统答复统一标注“历史归档解释、未核定现行效力”，不构成政策发布。"
           "业务效果的独立评测（30 题配对比较）和低资源构建效率对照正在进行，结果将在效果验证部分给出；"
           "扫描件目前使用已有的署名 AI 转录，人工复核正在进行。")


def chapter_two(w):
    w.heading("2 整体方案设计", 1)
    w.heading("2.1 设计原则", 2)
    w.bullets([
        ("来源可溯：", "每条结论绑定原件哈希、修订和定位，引文必须逐字出自原文。"),
        ("人工把关：", "新的本体结构和新的资料版本先作为待审候选，经人工确认才生效；结构确认不等于业务批准。"),
        ("确定性优先：", "检索、计划执行、数值与单位核对等可以计算的部分交给工具，模型不自行补全事实。"),
        ("失败即停：", "来源缺失、版本变化、问题超出范围或核验不通过时停止作答，并说明原因。"),
        ("最小权限与隐私：", "工具只接受限定输入，不接收身份证号等个人信息；凭据只保存在本机私有目录。"),
    ])

    w.heading("2.2 总体架构", 2)
    w.para("系统分为五个功能层和一组运行保障机制（图 2-1）。数据自下而上被识别、组织为知识，经工具执行和智能体编排，"
           "最终以带出处的答复呈现给用户；用户问题和更新任务则自上而下逐层下发。")
    w.figure("图1-系统总体架构.png", "图 2-1 系统总体架构", width=14.5)
    w.table("表 2-1 各层组成与实现", ["层次", "主要组成", "实现要点"], [
        ["数据资产层", "官方 HTML、Excel 收费目录、扫描 PDF", "采集前检查 robots 规则，核对原件 SHA-256；按页面选择器提取段落和条文，"
                                           "Excel 只读字面值、不执行公式，扫描页保留页码与左右半页定位"],
        ["知识与本体层", "本体种子、候选、版本库", "种子含 7 类实体（事项、条件、材料、部门、程序、规范条款、资产）和 6 类关系"
                                       "（需要、适用于、办理部门、包含步骤、对齐、证据支持）；候选经人工确认后成为不可变版本，可回退"],
        ["工具层", "MCP 服务", "基于 FastMCP 的 SSE 服务，使用专用 Bearer 令牌、Host 与 Origin 白名单，"
                           "启动时记录代码指纹，代码变更后拒绝继续服务"],
        ["编排层", "Nexent 主控与子 Agent、Skill", "主控按 Skill 规定的顺序调度子 Agent，每个角色只能使用指定工具，"
                                          "并设有步数上限"],
        ["交互层", "Nexent 对话与调试界面", "群众或工作人员用自然语言提问，答复包含结论、逐条引用、未知项和状态说明"],
        ["运行保障", "心跳、计划任务、来源观察", "主控心跳与计划任务恢复，官方来源到期采集，暂停门与人工确认门，"
                                     "调用预算与回执审计"],
    ], [2.6, 4.2, 7.8])

    w.heading("2.3 核心执行流程", 2)
    w.heading("2.3.1 检索-推理双驱动查询流程", 3)
    w.para("查询流程如图 2-2 所示。主控 Agent 读取 Skill 后先做范围检查：问题含个人信息、涉及试点范围以外的地区，"
           "或询问“现在、现行”的规定时，直接停止并说明原因。范围内的问题进入检索，工具取回前 5 条证据；本体推理链还会检查来源是否暂停或已变化。"
           "推理阶段，固定业务情境执行已确认的本体依赖计划；自由提问则由模型在规则约束下组织答复。"
           "最后，核验工具逐条检查引文是否逐字出自检索到的原文、答复中的数字和天数单位是否被引用，不通过即改为停止作答。")
    w.figure("图2-检索推理双驱动查询流程.png", "图 2-2 检索-推理双驱动查询流程")
    w.para("以南京试点为例，系统需要同时读取两份官方问答：周期问答给出省内 20 个工作日、省外户籍增加 5 个工作日，"
           "旧证问答说明“未超期的旧身份证可以继续使用”。本体计划依次执行三步：计算办理周期、取旧证有效区间的交集、比较二者覆盖关系，"
           "在虚构情境（省外户籍、旧证剩余 22 个工作日）下得出“不能确认旧证可覆盖整个办理周期”的结论。"
           "缺少任一份文件时，结论自动降为未知。")
    w.heading("2.3.2 知识更新与本体演化流程", 3)
    w.para("更新流程如图 2-3 所示。主控心跳和官方来源观察按到期时间采集资料，发现新原件后记录哈希与修订；"
           "整理子 Agent 分别读取新旧资料的完整视图并提交结构化差异，核验子 Agent 独立复核证据；结果以待审候选形式保存，"
           "不继承旧批准，也不自动发布。人工确认后，新版本才被激活；版本不可变，可随时回退。"
           "本体结构的变化（类型、关系、执行语义）与资料内容的变化（修订、差异候选）分开管理。")
    w.figure("图3-知识更新与本体演化流程.png", "图 2-3 知识更新与本体演化流程")

    w.heading("2.4 智能体设计", 2)
    w.para("系统在 Nexent 上配置三组智能体（表 2-2）。所有智能体使用 DeepSeek 官方模型 deepseek-flash，温度 0，"
           "关闭思考模式，每步输出不超过 1024 tokens；演示期间均为禁用、未发布的草稿，由受控接口或配置页调试对话运行。")
    w.table("表 2-2 智能体与职责", ["智能体组", "角色", "可用工具", "关键约束"], [
        ["自由提问", "问答助手（主）", "检索、核验", "最多 6 步；只依据检索证据作答；核验不通过即停止"],
        ["本体推理", "主控", "开启任务、读取状态、完成交付", "最多 6 步；只有持久化的核验阶段通过后才能交付"],
        ["本体推理", "检索子 Agent", "取回绑定来源的完整原文", "最多 3 步；每个任务只调用一次"],
        ["本体推理", "核验子 Agent", "执行已确认的本体依赖计划", "最多 3 步；使用工具返回的实际参数和依赖轨迹"],
        ["知识更新", "主控", "读取阶段状态、完成", "先调用整理、后调用核验；完成门通过才返回"],
        ["知识更新", "整理子 Agent", "读取新旧视图、提交差异", "最多 4 步；新旧资料各读一次完整视图"],
        ["知识更新", "核验子 Agent", "读取新旧视图、提交复核", "最多 4 步；独立核对证据与候选"],
    ], [2.4, 3.0, 4.0, 5.2])

    w.heading("2.5 MCP 工具与 Skill 分层编排", 2)
    w.para("工具按职责拆分，每个工具只做一件事（表 2-3）。每次调用都会重新检查服务代码指纹；"
           "本体推理工具还会复查来源门和已确认的本体版本。")
    w.table("表 2-3 MCP 服务与工具", ["服务", "工具", "作用"], [
        ["本体推理服务", "open_query", "按固定情境开启任务，返回任务编号"],
        ["本体推理服务", "retrieve", "取回本体绑定来源的完整原文选段"],
        ["本体推理服务", "verify", "执行已确认的本体依赖计划，记录逐跳依据"],
        ["本体推理服务", "state / finalize", "读取持久阶段并复查来源与版本；重新执行并原样返回持久答复"],
        ["自由提问服务", "frozen_retrieve", "范围检查，返回前 5 条证据和作答规则"],
        ["自由提问服务", "frozen_verify", "核对引文、数字和天数单位，不通过即改为停止"],
        ["知识更新服务", "get / submit / review / state / finish", "读取新旧视图，提交差异与复核，推进阶段并完成"],
    ], [3.0, 5.2, 6.4])
    w.para("Skill 采用分层编排（表 2-4）。编排层和协议层已沉淀为参数化模板：新部署只需填写情境、Agent 名称和证据说明，"
           "由渲染工具生成 Skill 与主控协议；执行层的来源适配和计划算子仍需按事项开发。")
    w.table("表 2-4 Skill 分层与复用方式", ["层次", "内容", "复用方式"], [
        ["平台层", "主控与检索、核验子 Agent，模型参数、步数上限", "统一接线脚本创建，profile 只换名称"],
        ["编排层", "读 Skill → 开任务 → 检索 → 核验 → 读状态 → 完成", "Skill 模板固定"],
        ["协议层", "可执行动作格式、子 Agent 调用示例、禁止重复与重试", "主控协议模板固定"],
        ["工具层", "5 个本体推理工具及其门禁检查", "共用服务，按 profile 分派"],
        ["执行层", "来源适配器、已确认依赖计划、领域算子", "按事项开发"],
    ], [2.4, 7.0, 5.2])

    w.heading("2.6 数据与安全设计", 2)
    w.bullets([
        ("数据范围：", "只使用公开的官方资料，标记为公开数据；演示使用虚构情境，不采集居民证件号或真实个人回答。"),
        ("凭据与网络：", "模型凭据通过环境变量或本机私有目录提供，不写入仓库；MCP 服务使用专用令牌；"
                    "云端请求沿用系统代理并保留 HTTPS 证书校验，本机 Nexent 与 MCP 服务直连。"),
        ("状态门：", "答复保持“未审核暂停、现行效力未知、未正式批准”的状态标注；新候选不自动发布，旧批准不被继承。"),
        ("预算与失败处理：", "调用前预留预算并设上限；模型、工具或来源出错时停止，不重试业务请求，失败记录完整保留。"),
    ])

    w.heading("2.7 部署与运行环境", 2)
    w.table("表 2-5 运行环境", ["组件", "版本与配置"], [
        ["智能体平台", "Nexent v2.7.0（Docker 部署）：配置服务 5010、运行服务 5014、Web 界面 3000"],
        ["语言模型", "DeepSeek 官方 API，deepseek-flash；温度 0，非思考模式"],
        ["MCP 服务", "本体推理 8785、自由提问 8786、知识更新 8784，均仅监听本机，通过 host.docker.internal 供容器访问"],
        ["运行环境", "Windows 11、Docker Desktop（WSL2）、Python 3.12，依赖版本锁定"],
        ["交付方式", "公开仓库（MIT 许可）；打包脚本生成工程包并核验散列，Agent 配置以平台导出 JSON 提供"],
    ], [3.2, 11.4])


def main():
    doc = Document()
    configure(doc)
    writer = Writer(doc)
    title_block(writer)
    chapter_one(writer)
    # No forced chapter break: pagination is redone when pasted into the official template.
    chapter_two(writer)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
