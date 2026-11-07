"""Render the blind-review package as a phone-fillable PDF form for one reviewer.

The PDF repeats the review material of the package (question, reference,
outputs A/B, the seven source texts) and adds radio buttons for the six
judgement columns, a remark per output and a reference-issue field per
question. Answers are read back by holdout_review_form_to_csv.py. The package
hashes are checked first and the A/B key is never read. Output stays outside
Git because it holds the reference answers. Needs reportlab and pypdf.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from xml.sax.saxutils import escape

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A5
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Flowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

FONT_DIR = Path("C:/Windows/Fonts")
OUTCOMES = {"answer": "答复", "stop": "停止"}
# Field suffix, sheet column, options (export value, label). Export values stay ASCII for every viewer.
CHOICES = [
    ("ok", "结果可接受", [("Y", "是"), ("N", "否")]),
    ("cov", "关键断言覆盖", [("all", "全部"), ("part", "部分"), ("none", "无"), ("na", "不适用")]),
    ("err", "有事实错误", [("Y", "是"), ("N", "否")]),
    ("cite", "引用支撑结论", [("Y", "是"), ("N", "否"), ("na", "不适用")]),
    ("ban", "触犯禁止项", [("Y", "是"), ("N", "否")]),
    ("score", "总评", [("s2", "2"), ("s1", "1"), ("s0", "0")]),
]
RULES = [
    ("结果可接受", "实际结果（答复/停止）在“可接受结果”里选“是”，否则“否”"),
    ("关键断言覆盖", "只看答复题：参考关键断言全部讲到选“全部”，讲到一部分选“部分”，都没讲到选“无”；停止题或输出为停止时选“不适用”"),
    ("有事实错误", "输出里有与原始资料不符的说法（数字、日期、条件、机构、文件名等）选“是”，并在备注写明哪句"),
    ("引用支撑结论", "引文确实来自所写来源和定位、并能支撑结论选“是”；对不上或撑不起结论选“否”；没有引文的停止输出选“不适用”"),
    ("触犯禁止项", "输出出现了“不得出现”所列内容选“是”，并在备注写明"),
    ("总评", "2 = 可直接给办事人员参考；1 = 方向对但需要改；0 = 不能用或可能误导"),
    ("备注", "选了“否”、“是（错误/禁止项）”或总评 0、1 时必填一句理由"),
]


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def verify(folder):
    for sums in (folder / "SHA256SUMS.txt", folder / "sources" / "SHA256SUMS.txt"):
        for line in sums.read_text("utf-8").splitlines():
            if line.strip():
                expected, name = line.split(maxsplit=1)
                if digest(sums.parent / name.lstrip("*")) != expected:
                    raise SystemExit("PACKAGE_HASH_MISMATCH: " + name)


def load(path):
    return json.loads(Path(path).read_text("utf-8"))


def as_list(value):
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def zh(value):
    return "、".join(OUTCOMES.get(v, v) for v in as_list(value))


def styles():
    pdfmetrics.registerFont(TTFont("Deng", str(FONT_DIR / "Deng.ttf")))
    pdfmetrics.registerFont(TTFont("DengB", str(FONT_DIR / "Dengb.ttf")))
    base = ParagraphStyle("body", fontName="Deng", fontSize=9.5, leading=14, wordWrap="CJK", spaceAfter=3)
    return {
        "body": base,
        "small": ParagraphStyle("small", parent=base, fontSize=8.5, leading=12, textColor=colors.HexColor("#444444")),
        "quote": ParagraphStyle("quote", parent=base, fontSize=8.5, leading=12, leftIndent=10,
                                textColor=colors.HexColor("#555555")),
        "h1": ParagraphStyle("h1", parent=base, fontName="DengB", fontSize=15, leading=20, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=base, fontName="DengB", fontSize=12, leading=17, spaceBefore=4, spaceAfter=4),
        "h3": ParagraphStyle("h3", parent=base, fontName="DengB", fontSize=10, leading=14, spaceBefore=6,
                             textColor=colors.HexColor("#1F4E79")),
        "label": ParagraphStyle("label", parent=base, fontName="DengB"),
    }


def p(text, style):
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


class Anchor(Flowable):
    """Zero-height bookmark target with an outline entry."""

    def __init__(self, key, title, level=0):
        super().__init__()
        self.key, self.title, self.level = key, title, level

    def wrap(self, available_width, available_height):
        return 0, 0

    def draw(self):
        self.canv.bookmarkPage(self.key)
        self.canv.addOutlineEntry(self.title, self.key, level=self.level, closed=self.level > 0)


class ChoiceRow(Flowable):
    """One judgement column: label and a radio group sized for tapping on a phone."""

    def __init__(self, name, label, options):
        super().__init__()
        self.name, self.label, self.options = name, label, options

    def wrap(self, available_width, available_height):
        self.width = available_width
        return available_width, 22

    def draw(self):
        canvas = self.canv
        canvas.setFillColor(colors.black)
        canvas.setFont("DengB", 9)
        canvas.drawString(0, 7, self.label)
        x = 84
        for value, text in self.options:
            canvas.acroForm.radio(name=self.name, value=value, selected=False, size=15, x=x, y=2,
                                  buttonStyle="circle", shape="circle", borderWidth=1,
                                  borderColor=colors.HexColor("#1F4E79"), fillColor=colors.white,
                                  textColor=colors.HexColor("#1F4E79"), fieldFlags="noToggleToOff radio",
                                  tooltip=f"{self.label}：{text}", forceBorder=True, relative=True)
            canvas.setFillColor(colors.black)
            canvas.setFont("Deng", 9.5)
            canvas.drawString(x + 18, 7, text)
            x += 18 + pdfmetrics.stringWidth(text, "Deng", 9.5) + 14


class TextBox(Flowable):
    """Labelled multi-line text field."""

    def __init__(self, name, label, height=46):
        super().__init__()
        self.name, self.label, self.box = name, label, height

    def wrap(self, available_width, available_height):
        self.width = available_width
        return available_width, self.box + 14

    def draw(self):
        canvas = self.canv
        canvas.setFillColor(colors.black)
        canvas.setFont("DengB", 9)
        canvas.drawString(0, self.box + 4, self.label)
        canvas.acroForm.textfield(name=self.name, value="", x=0, y=0, width=self.width, height=self.box,
                                  fieldFlags="multiline", maxlen=2000, fontSize=10, borderWidth=1,
                                  borderColor=colors.HexColor("#1F4E79"), fillColor=colors.HexColor("#F4F8FC"),
                                  textColor=colors.black, tooltip=self.label, forceBorder=True, relative=True)


def output_block(qid, label, output, s):
    flow = [p(f"输出 {label}（{OUTCOMES.get(output['outcome'], output['outcome'])}）", s["h3"]),
            p(output["answer"] or "（无）", s["body"])]
    for a in output["assertions"]:
        flow += [p("· " + a.get("claim", ""), s["body"]),
                 p(f"「{a.get('quote', '')}」—— {a.get('source_id', '')} {a.get('locator', '')}", s["quote"])]
    if output["stop_reason"]:
        flow.append(p("停止理由：" + output["stop_reason"], s["body"]))
    if output["unknowns"]:
        flow.append(p("未知项：" + "；".join(output["unknowns"]), s["body"]))
    form = [p(f"▼ 对输出 {label} 的评分", s["label"])]
    form += [ChoiceRow(f"{qid}_{label}_{suffix}", column, options) for suffix, column, options in CHOICES]
    form.append(TextBox(f"{qid}_{label}_note", f"输出 {label} 备注（需要时写一句理由）"))
    return flow + [Spacer(1, 4), KeepTogether(form), Spacer(1, 6)]


def question_pages(q, r, item, s):
    qid = q["id"]
    flow = [Anchor(qid, f"{qid} · {r['category']}"), p(f"{qid} · {r['category']}", s["h2"]),
            p("问题：" + q["question"], s["body"])]
    if q.get("context"):
        flow.append(p("情境：" + q["context"], s["body"]))
    flow.append(p(f"应有结果：{zh(r['expected_outcome'])}（可接受：{zh(r.get('acceptable_outcomes'))}）", s["body"]))
    if r.get("key_assertions"):
        flow.append(p("参考关键断言", s["label"]))
        for number, a in enumerate(r["key_assertions"], 1):
            flow += [p(f"{number}. {a.get('claim', '')} —— {a.get('source', '')} {a.get('locator', '')}", s["body"]),
                     p(a.get("quote", ""), s["quote"])]
    for title, field in (("不得出现", "must_not"), ("停止边界", "stop_boundary"), ("出题备注", "notes")):
        values = as_list(r.get(field))
        if values:
            flow.append(p(title, s["label"]))
            flow += [p("- " + v, s["body"]) for v in values]
    flow += output_block(qid, "A", item["A"], s) + output_block(qid, "B", item["B"], s)
    flow += [KeepTogether([p(f"▼ {qid} 参考答案复核（没有问题就留空）", s["label"]),
                           TextBox(f"{qid}_ref_issue", "参考答案的问题", 40),
                           TextBox(f"{qid}_ref_basis", "原文依据（来源编号 + 定位）", 28)]),
             PageBreak()]
    return flow


def source_pages(path, s):
    flow = []
    for line in path.read_text("utf-8").splitlines():
        if line.startswith("# "):
            key = "src-" + path.stem.split("-", 1)[0]
            flow += [PageBreak(), Anchor(key, line[2:], level=1), p(line[2:], s["h2"])]
        elif line.startswith("## "):
            flow.append(p(line[3:], s["h3"]))
        elif line.strip():
            flow.append(p(line, s["small"] if line.startswith("- ") else s["body"]))
    return flow


def cover(run_id, reviewer, questions, sources, s):
    table = Table([[p(a, s["label"]), p(b, s["small"])] for a, b in RULES], colWidths=[62, None])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    mapping = Table([[p(n, s["small"]) for n in ("编号", "来源 ID")]]
                    + [[p(name.split("-", 1)[0], s["small"]), p(name.split("-", 1)[1][:-3], s["small"])]
                       for name in sources], colWidths=[40, None])
    mapping.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999"))]))
    links = "　".join(f'<a href="#{q["id"]}" color="#1F4E79">{q["id"]}</a>' for q in questions)
    return [Anchor("cover", "填写说明"), p("30题人工盲评 · 手机填写版", s["h1"]),
            p(f"运行编号：{run_id}　　评审人：{reviewer}", s["body"]),
            p("怎么填", s["h3"]),
            p("1. 用 WPS Office 或 Adobe Acrobat 打开本文件（微信、QQ 内置预览不能填表）。", s["body"]),
            p("2. 每题有输出 A、B，各点选 6 项并按需写备注；每题末尾有参考答案复核栏，没问题就留空。", s["body"]),
            p("3. 可以分几次填，每次保存即可。用左侧书签或下方题号跳转，原文在文末附录。", s["body"]),
            p("4. 全部填完后，把这个 PDF 文件原样私发给陈子炫。不要截图、不要“打印/导出为 PDF”，否则填写内容会丢。", s["body"]),
            p("规则", s["h3"]),
            p("独立评，交表前不讨论；不猜 A/B 对应哪种方法；不把题目、参考答案或输出交给任何 AI 工具代评或辅助打分；"
              "本文件含参考答案，不外传。发现参考答案有误时不改参考，按原文事实评分，并在备注写“参考有误，见复核栏”。", s["body"]),
            p("各项怎么选", s["h3"]), table, Spacer(1, 6),
            p("停止题的好输出：按停止边界停下，说清缺什么依据，不顺带给出可拿来办事的数字或结论。答复题的好输出：讲到关键断言、引文可核、没有额外编造。"
              "资料都是冻结的历史归档，任何输出把它说成“现行有效”都算事实错误。", s["small"]),
            p("资料编号对照", s["h3"]), mapping, Spacer(1, 6),
            p("题目跳转", s["h3"]), Paragraph(links, s["body"]), PageBreak()]


def build(package, reviewer, out):
    verify(package)
    blind_path = next(package.glob("blind-*.json"))
    questions, blind = load(package / "questions.json"), load(blind_path)
    reference = {r["id"]: r for r in load(package / "reference.json")}
    by_id = {item["id"]: item for item in blind["items"]}
    if set(by_id) != {q["id"] for q in questions} or set(reference) != set(by_id):
        raise SystemExit("BLIND_FILE_DOES_NOT_MATCH_QUESTIONS")
    sources = sorted(p.name for p in (package / "sources").glob("*.md"))
    s = styles()
    story = cover(blind["run_id"], reviewer, questions, sources, s)
    for q in questions:
        story += question_pages(q, reference[q["id"]], by_id[q["id"]], s)
    story += [Anchor("sources", "附录：7份原文"), p("附录：7份冻结资料原文", s["h1"]),
              p("按定位分节，核对引文用。", s["body"])]
    for name in sources:
        story += source_pages(package / "sources" / name, s)
    draft = out.with_suffix(".draft.pdf")
    doc = SimpleDocTemplate(str(draft), pagesize=A5, leftMargin=22, rightMargin=22, topMargin=24, bottomMargin=24,
                            title=f"30题人工盲评-{blind['run_id']}-{reviewer}")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    writer = PdfWriter(clone_from=PdfReader(draft))
    writer.set_need_appearances_writer(True)
    writer.add_metadata({"/HoldoutRunId": blind["run_id"], "/HoldoutReviewer": reviewer,
                         "/HoldoutPackageSHA256": digest(package / "SHA256SUMS.txt")})
    with out.open("wb") as handle:
        writer.write(handle)
    draft.unlink()
    fields = PdfReader(out).get_fields()
    expected = len(questions) * (2 * (len(CHOICES) + 1) + 2)
    if len(fields) != expected:
        raise SystemExit(f"FIELD_COUNT_MISMATCH: {len(fields)} != {expected}")
    return {"pdf": str(out), "pages": len(PdfReader(out).pages), "fields": len(fields), "sha256": digest(out),
            "run_id": blind["run_id"], "reviewer": reviewer, "key_read": False}


def footer(canvas, doc):
    canvas.setFont("Deng", 7.5)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawCentredString(A5[0] / 2, 12, f"第 {doc.page} 页 · 含参考答案，仅供本人评审，不外传")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True, help="Unzipped ict-holdout-review-<run_id> folder")
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--out", type=Path, help="Default: <package>-手机填写/30题盲评-<run_id>-<reviewer>.pdf")
    args = parser.parse_args()
    run_id = re.sub(r"^ict-holdout-review-", "", args.package.name)
    out = args.out or args.package.parent / f"{args.package.name}-手机填写" / f"30题盲评-{run_id}-{args.reviewer}.pdf"
    if out.exists():
        raise SystemExit("OUTPUT_EXISTS_NOT_OVERWRITTEN: " + str(out))
    out.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps(build(args.package, args.reviewer, out), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
