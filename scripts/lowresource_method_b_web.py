"""Phone-friendly web form for the low-resource method-B review (standard library only to serve).

pack   (local): check the stored candidate file against its receipt hash, resolve every
       unit's frozen source text by locator (text hashes checked), optionally render the
       scan pages for U6 (needs pypdfium2), and write method-b-data.json plus tokens.json
       with one secret link per reviewer. Each reviewer only sees the method-B units that
       units.json assigns to them.
serve  (server): answer /b/<token>/ pages. Answers autosave to answers/<hash>.json with a
       server-time log of which fields changed (names only), so first and last edit times
       are kept beside the self-reported minutes. Submitting locks the sheet. With
       --holdout-folder the finished 30-question questionnaire keeps answering its
       /r/<token>/ links from the same port.
export (local): turn a copied answers folder into one Markdown record per reviewer and a
       counts-only summary for appendix table 8-2.
The candidates stay private until both method-B reviews are in; keep the folder private.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import holdout_review_web as holdout  # noqa: E402

RECEIPT_SHA256 = "165614c05993f8a87de048ba4f8096238ab6d8994a0790928908c90322583438"
SCAN_SOURCE = "HN-MEASURES-2021-PDF"
SCAN_SHA256 = "db257f4c918d95d6212c92262309ddd9a10ef33ec5354e0bbbf954df1ac52eff"
SCAN_PAGES = [5, 6]
KINDS = {"entity": "实体", "relation": "关系", "condition": "条件", "material": "材料", "step": "步骤",
         "value": "数值", "version_change": "版本变化", "exception": "例外"}
DECISIONS = [["adopt", "直接采纳"], ["modify", "修改后采纳"], ["reject", "拒绝"]]
NEEDS_NOTE = {"modify", "reject"}
MINUTES = [["m_read", "阅读原文"], ["m_review", "逐条复核"], ["m_missing", "补写漏项"]]
TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
MIN_RE = re.compile(r"\d{1,3}")
TEXT_LIMIT = 3000
EVENT_LIMIT = 5000
RULES = [
    ["直接采纳", "主体、关系、客体、限定和引文都与原文一致，可以原样进入本体"],
    ["修改后采纳", "方向对但有错或不完整：在说明栏写出改后的完整内容"],
    ["拒绝", "原文不支持、超出单元目标、与其他候选重复或会误导：在说明栏写理由"],
    ["漏项", "原文里有、单元目标要求、但所有候选都没覆盖的内容：每行一条，写原文定位和漏掉的内容；没有就写“无”"],
    ["计时", "开始复核前点“填入现在”记开始时刻，做完再记结束时刻；休息或处理别的事时暂停，并写明暂停时段。主动分钟按阅读原文、逐条复核、补写漏项三段填写"],
]


# ---------- pack (runs locally) ----------

def digest_bytes(raw):
    return sha256(raw).hexdigest()


def resolve(unit, corpus):
    """Exact corpus texts for every locator of the unit; stop when a text hash no longer matches."""
    texts = []
    for source in unit["sources"]:
        mine = [item for item in corpus if item["source_id"] == source["source_id"]]
        for locator in source["locators"]:
            exact = [item for item in mine if item["locator"] == locator["locator"]]
            if source["source_id"] == SCAN_SOURCE and not exact:
                exact = mine  # the scan unit spans both attributed page transcriptions
            if not exact:
                raise SystemExit(f"UNIT_LOCATOR_NOT_IN_CORPUS: {unit['id']} {locator['locator']}")
            if locator.get("text_sha256") and all(item["text_sha256"] != locator["text_sha256"] for item in exact):
                raise SystemExit(f"UNIT_TEXT_CHANGED: {unit['id']} {locator['locator']}")
            for item in exact:
                if item not in texts:
                    texts.append(item)
    return texts


def render_scan(pdf, out):
    raw = pdf.read_bytes()
    if digest_bytes(raw) != SCAN_SHA256:
        raise SystemExit("SCAN_PDF_HASH_MISMATCH")
    import pypdfium2  # only needed locally for pack
    doc = pypdfium2.PdfDocument(raw)
    names = []
    for page in SCAN_PAGES:
        name = f"scan-p{page}.jpg"
        doc[page - 1].render(scale=2.2).to_pil().convert("RGB").save(out / name, quality=82)
        names.append(name)
    return names


def pack(candidates_path, units_path, corpus_path, reviewers, out, scan_pdf=None):
    raw = candidates_path.read_bytes()
    if digest_bytes(raw) != RECEIPT_SHA256:
        raise SystemExit("CANDIDATES_DO_NOT_MATCH_RECEIPT")
    candidates = json.loads(raw)["units"]
    plan = json.loads(units_path.read_text("utf-8"))
    corpus = json.loads(corpus_path.read_text("utf-8"))["units"]
    assignment = {name: plan["assignment"][name]["method_b"] for name in reviewers}
    out.mkdir(parents=True, exist_ok=True)
    (out / "images").mkdir(exist_ok=True)
    units = {}
    for unit in plan["units"]:
        uid = unit["id"]
        if not any(uid in ids for ids in assignment.values()):
            continue
        texts = resolve(unit, corpus)
        images = render_scan(scan_pdf, out / "images") if scan_pdf and any(
            s["source_id"] == SCAN_SOURCE for s in unit["sources"]) else []
        units[uid] = {
            "id": uid, "kind": unit["kind"], "target": unit["target"],
            "seen_in_development": bool(unit.get("seen_in_development")),
            "sources": [{"source_id": s["source_id"], "url": s.get("url", ""), "original_sha256": s.get("original_sha256", ""),
                         "locators": [loc["locator"] for loc in s["locators"]]} for s in unit["sources"]],
            "texts": [{"source_id": t["source_id"], "locator": t["locator"], "text": t["text"],
                       "text_sha256": t["text_sha256"], "transcription": t["source_id"] == SCAN_SOURCE} for t in texts],
            "images": images,
            "candidates": [{"kind": c["kind"], "subject": c["subject"], "predicate": c["predicate"], "object": c["object"],
                            "qualifiers": c.get("qualifiers") or [], "quote": c["quote"]} for c in candidates[uid]["candidates"]],
        }
    data = {"candidates_sha256": RECEIPT_SHA256, "units_sha256": digest_bytes(units_path.read_bytes()),
            "kinds": KINDS, "decisions": DECISIONS, "minutes": MINUTES, "rules": RULES,
            "assignment": assignment, "units": units}
    (out / "method-b-data.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    tokens_path = out / "tokens.json"
    tokens = json.loads(tokens_path.read_text("utf-8")) if tokens_path.exists() else {}
    for name in reviewers:
        if name not in tokens.values():
            tokens[secrets.token_urlsafe(18)] = name
    tokens_path.write_text(json.dumps(tokens, ensure_ascii=False, indent=1), "utf-8")
    return {"folder": str(out), "reviewers": {n: {u: len(units[u]["candidates"]) for u in ids} for n, ids in assignment.items()},
            "candidates_sha256": RECEIPT_SHA256, "images": sorted(p.name for p in (out / "images").iterdir())}


# ---------- answers, checks and export ----------

def field_names(data, reviewer):
    """Allowed field -> allowed codes (None = free text) for one reviewer."""
    allowed = {"exposure": None, "ai_use": None}
    for uid in data["assignment"][reviewer]:
        unit = data["units"][uid]
        for n in range(1, len(unit["candidates"]) + 1):
            allowed[f"{uid}_c{n}"] = {code for code, _ in DECISIONS}
            allowed[f"{uid}_c{n}_note"] = None
        for name in ("start", "end", "pause", "missing") + tuple(m for m, _ in MINUTES):
            allowed[f"{uid}_{name}"] = None
    return allowed


def problems(data, reviewer, values):
    """Human-readable list of what still blocks submission."""
    out = []
    for name, label in (("exposure", "既往接触"), ("ai_use", "AI使用情况")):
        if not values.get(name, "").strip():
            out.append(f"说明页：{label}未填")
    for uid in data["assignment"][reviewer]:
        unit = data["units"][uid]
        undecided = [n for n in range(1, len(unit["candidates"]) + 1) if not values.get(f"{uid}_c{n}")]
        if undecided:
            out.append(f"{uid}：还有 {len(undecided)} 条候选未处理")
        notes = [n for n in range(1, len(unit["candidates"]) + 1)
                 if values.get(f"{uid}_c{n}") in NEEDS_NOTE and not values.get(f"{uid}_c{n}_note", "").strip()]
        if notes:
            out.append(f"{uid}：第 {'、'.join(map(str, notes))} 条需写修改内容或拒绝理由")
        if not values.get(f"{uid}_missing", "").strip():
            out.append(f"{uid}：漏项未填（没有就写“无”）")
        start, end = values.get(f"{uid}_start", ""), values.get(f"{uid}_end", "")
        if not TIME_RE.fullmatch(start) or not TIME_RE.fullmatch(end):
            out.append(f"{uid}：开始或结束时刻未填（格式 2026-11-13 14:05）")
        elif end < start:
            out.append(f"{uid}：结束时刻早于开始时刻")
        if not values.get(f"{uid}_pause", "").strip():
            out.append(f"{uid}：暂停情况未填（没有就写“无”）")
        if any(not MIN_RE.fullmatch(values.get(f"{uid}_{m}", "")) for m, _ in MINUTES):
            out.append(f"{uid}：三段主动分钟未填全（整数）")
    return out


def missing_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip() and line.strip() not in ("无", "没有", "无漏项")]


def unit_edit_window(record, uid):
    """First and last server times at which a field of this unit was saved."""
    times = [ts for ts, fields in record.get("events", []) if any(f.startswith(uid + "_") for f in fields)]
    return (times[0], times[-1]) if times else (None, None)


def summarize(data, record):
    values, rows = record.get("values", {}), {}
    for uid in data["assignment"][record["reviewer"]]:
        n = len(data["units"][uid]["candidates"])
        codes = [values.get(f"{uid}_c{i}") for i in range(1, n + 1)]
        minutes = {m: int(values[f"{uid}_{m}"]) if MIN_RE.fullmatch(values.get(f"{uid}_{m}", "")) else None for m, _ in MINUTES}
        first, last = unit_edit_window(record, uid)
        adopt, modify, reject = (codes.count(c) for c in ("adopt", "modify", "reject"))
        rows[uid] = {"candidates": n, "adopt": adopt, "modify": modify, "reject": reject,
                     "direct_adoption_rate": round(adopt / n, 3), "usable_rate": round((adopt + modify) / n, 3),
                     "missing_items": len(missing_lines(values.get(f"{uid}_missing", ""))),
                     "minutes": minutes,
                     "active_minutes": sum(minutes.values()) if None not in minutes.values() else None,
                     "self_reported_start": values.get(f"{uid}_start"), "self_reported_end": values.get(f"{uid}_end"),
                     "server_first_edit": first, "server_last_edit": last}
    return rows


def export(folder, out):
    data = json.loads((folder / "method-b-data.json").read_text("utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    summary = {"candidates_sha256": data["candidates_sha256"], "reviewers": {}}
    for path in sorted((folder / "answers").glob("*.json")):
        record = json.loads(path.read_text("utf-8"))
        name, values = record["reviewer"], record.get("values", {})
        rows = summarize(data, record)
        summary["reviewers"][name] = {"submitted_at": record.get("submitted_at"), "first_opened_at": record.get("first_opened_at"),
                                      "open_problems": len(problems(data, name, values)), "units": rows}
        date = (record.get("submitted_at") or record.get("updated_at") or "")[:10].replace("-", "")
        lines = [f"# 低资源方法B记录 - {name}", "",
                 f"- 候选文件SHA-256：`{data['candidates_sha256']}`（11月5日存证版本）",
                 f"- 首次打开（服务器时间）：{record.get('first_opened_at') or '未记录'}",
                 f"- 交表（服务器时间）：{record.get('submitted_at') or '未交表'}",
                 f"- 既往接触：{values.get('exposure', '').strip() or '未填'}",
                 f"- AI使用情况：{values.get('ai_use', '').strip() or '未填'}", ""]
        for uid, row in rows.items():
            unit = data["units"][uid]
            lines += [f"## {uid} 方法B候选复核记录", "",
                      f"- 开始 / 结束（本人填写）：{row['self_reported_start'] or '未填'} / {row['self_reported_end'] or '未填'}",
                      f"- 本单元首次 / 最后保存（服务器时间）：{row['server_first_edit'] or '无'} / {row['server_last_edit'] or '无'}",
                      f"- 暂停时段及原因：{values.get(uid + '_pause', '').strip() or '未填'}",
                      "- 主动分钟：" + "，".join(f"{label} {row['minutes'][m] if row['minutes'][m] is not None else '未填'}" for m, label in MINUTES)
                      + f"，合计 {row['active_minutes'] if row['active_minutes'] is not None else '未填全'}",
                      f"- 处理：直接采纳 {row['adopt']}、修改后采纳 {row['modify']}、拒绝 {row['reject']}（共 {row['candidates']} 条）；"
                      f"漏项 {row['missing_items']} 条", "",
                      "| 候选编号 | 候选内容 | 处理 | 修改内容或拒绝理由 |", "| --- | --- | --- | --- |"]
            labels = dict(DECISIONS)
            for i, c in enumerate(unit["candidates"], 1):
                content = f"{c['subject']}｜{c['predicate']}｜{c['object']}".replace("|", "/")
                note = values.get(f"{uid}_c{i}_note", "").strip().replace("\n", " ").replace("|", "/")
                lines.append(f"| {i} | {content} | {labels.get(values.get(f'{uid}_c{i}'), '未处理')} | {note} |")
            lines += ["", "### 系统候选漏掉的内容", ""]
            lines += [f"{n}. {item}" for n, item in enumerate(missing_lines(values.get(f"{uid}_missing", "")), 1)] or ["无"]
            lines.append("")
        (out / f"低资源方法B记录-{name}-{date or '未交表'}.md").write_text("\n".join(lines), "utf-8")
    (out / "method-b-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", "utf-8")
    return {n: {"submitted_at": r["submitted_at"], "open_problems": r["open_problems"],
                "units": {u: (x["adopt"], x["modify"], x["reject"], x["missing_items"], x["active_minutes"]) for u, x in r["units"].items()}}
            for n, r in summary["reviewers"].items()}


# ---------- serve (runs on the server) ----------

class Store:
    def __init__(self, folder):
        self.data = json.loads((folder / "method-b-data.json").read_text("utf-8"))
        self.tokens = json.loads((folder / "tokens.json").read_text("utf-8"))
        self.images = folder / "images"
        self.answers_dir = folder / "answers"
        self.answers_dir.mkdir(exist_ok=True)
        self.lock = threading.Lock()
        self.allowed = {name: field_names(self.data, name) for name in self.tokens.values()}

    def path(self, reviewer):
        return self.answers_dir / (sha256(("method-b:" + reviewer).encode("utf-8")).hexdigest()[:16] + ".json")

    def load(self, reviewer):
        path = self.path(reviewer)
        if path.exists():
            return json.loads(path.read_text("utf-8"))
        return {"reviewer": reviewer, "candidates_sha256": self.data["candidates_sha256"], "values": {},
                "events": [], "first_opened_at": None, "submitted_at": None}

    def write(self, record):
        record["updated_at"] = now()
        path = self.path(record["reviewer"])
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps(record, ensure_ascii=False, indent=1), "utf-8")
        os.replace(pending, path)

    def opened(self, reviewer):
        with self.lock:
            record = self.load(reviewer)
            if not record.get("first_opened_at"):
                record["first_opened_at"] = now()
                self.write(record)
            return record

    def save(self, reviewer, changes):
        with self.lock:
            record = self.load(reviewer)
            if record.get("submitted_at"):
                return 409, {"error": "已交表，不能再修改"}
            allowed = self.allowed[reviewer]
            for name, value in changes.items():
                if name not in allowed or not isinstance(value, str) or len(value) > TEXT_LIMIT:
                    return 400, {"error": "无效字段"}
                if allowed[name] is not None and value not in allowed[name] and value != "":
                    return 400, {"error": "无效取值"}
            record["values"].update(changes)
            record.setdefault("events", []).append([now(), sorted(changes)])
            del record["events"][:-EVENT_LIMIT]
            self.write(record)
            return 200, {"saved": len(changes), "updated_at": record["updated_at"]}

    def submit(self, reviewer):
        with self.lock:
            record = self.load(reviewer)
            if record.get("submitted_at"):
                return 200, {"submitted_at": record["submitted_at"]}
            blocking = problems(self.data, reviewer, record["values"])
            if blocking:
                return 409, {"error": "还有未填项", "problems": blocking}
            record["submitted_at"] = now()
            self.write(record)
            return 200, {"submitted_at": record["submitted_at"]}


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class Handler(holdout.Handler):
    """Method-B pages under /b/<token>/; any other path goes to the 30-question questionnaire if loaded."""
    method_b = None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), re.sub(r"/[rb]/[^/ ]+", "/?/<token>", fmt % args)))

    def route_b(self):
        match = re.fullmatch(r"/b/([A-Za-z0-9_-]{16,64})(/.*)?", self.path.split("?", 1)[0])
        if not match or match.group(1) not in self.method_b.tokens:
            return None, None
        return self.method_b.tokens[match.group(1)], match.group(2) or "/"

    def not_found(self):
        return self.reply(404, "not found", "text/plain; charset=utf-8")

    def do_GET(self):
        if not self.path.startswith("/b/"):
            return super().do_GET() if holdout.Handler.store else self.not_found()
        reviewer, rest = self.route_b()
        if reviewer is None:
            return self.not_found()
        store = self.method_b
        if rest == "/":
            return self.reply(200, PAGE, "text/html; charset=utf-8")
        if rest == "/api/data":
            ids = store.data["assignment"][reviewer]
            return self.reply(200, dict(store.data, reviewer=reviewer, assignment={reviewer: ids},
                                        units={u: store.data["units"][u] for u in ids}))
        if rest == "/api/answers":
            record = store.opened(reviewer)
            return self.reply(200, {k: record.get(k) for k in ("values", "submitted_at", "first_opened_at")})
        image = re.fullmatch(r"/img/(scan-p\d\.jpg)", rest)
        if image and any(image.group(1) in store.data["units"][u]["images"] for u in store.data["assignment"][reviewer]):
            return self.reply(200, (store.images / image.group(1)).read_bytes(), "image/jpeg")
        return self.not_found()

    def do_POST(self):
        if not self.path.startswith("/b/"):
            return super().do_POST() if holdout.Handler.store else self.not_found()
        reviewer, rest = self.route_b()
        if reviewer is None:
            return self.not_found()
        length = int(self.headers.get("Content-Length") or 0)
        if length > 256 * 1024:
            return self.reply(413, {"error": "too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.reply(400, {"error": "bad json"})
        if rest == "/api/save" and isinstance(body, dict):
            return self.reply(*self.method_b.save(reviewer, body))
        if rest == "/api/submit":
            return self.reply(*self.method_b.submit(reviewer))
        return self.reply(404, {"error": "not found"})


def serve(folder, holdout_folder, host, port, cert=None, key=None):
    import ssl
    Handler.method_b = Store(folder)
    if holdout_folder:
        holdout.Handler.store = holdout.Store(holdout_folder)
    httpd = holdout.Server((host, port), Handler)
    if cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    sys.stderr.write(f"serving {len(Handler.method_b.tokens)} method-B link(s)"
                     f"{' and the 30-question questionnaire' if holdout_folder else ''} on {host}:{port}\n")
    httpd.serve_forever()


PAGE = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer">
<title>方法B候选复核</title>
<style>
:root{--ink:#1d2433;--muted:#5d6675;--line:#d9dee7;--brand:#1f4e79;--soft:#eef3f9;--ok:#1b7f4b;--warn:#b5651d}
*{box-sizing:border-box}body{margin:0;font:16px/1.6 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);background:#f6f7f9}
header{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid var(--line);padding:8px 12px;display:flex;gap:8px;align-items:center}
header .t{font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
button{font:inherit;border:1px solid var(--line);background:#fff;border-radius:8px;padding:6px 12px;color:var(--ink)}
button.primary{background:var(--brand);border-color:var(--brand);color:#fff}button:disabled{opacity:.5}
main{max-width:760px;margin:0 auto;padding:12px 12px 90px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:10px 0}
h2{font-size:18px;margin:4px 0 8px}h3{font-size:15px;margin:12px 0 4px;color:var(--brand)}
.muted{color:var(--muted);font-size:14px}.quote{color:var(--muted);font-size:14px;border-left:3px solid var(--line);padding-left:8px;margin:4px 0}
.src{background:var(--soft);border-radius:8px;padding:8px 10px;margin:6px 0;word-break:break-all}
.cand{border-left:4px solid var(--brand)}.tag{display:inline-block;font-size:12px;background:var(--soft);color:var(--brand);border-radius:6px;padding:0 6px;margin-right:6px}
.row{margin:10px 0}.row .lab{font-weight:600;font-size:14px;margin-bottom:4px}
.seg{display:flex;flex-wrap:wrap;gap:6px}.seg label{flex:1 1 0;min-width:84px}
.seg input{position:absolute;opacity:0;pointer-events:none}
.seg span{display:block;text-align:center;padding:9px 4px;border:1px solid var(--line);border-radius:8px;background:#fff}
.seg input:checked+span{background:var(--brand);border-color:var(--brand);color:#fff}
textarea,input[type=text]{width:100%;font:inherit;border:1px solid var(--line);border-radius:8px;padding:8px}textarea{min-height:64px}
.inline{display:flex;gap:6px}.inline input{flex:1}.mins{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
.hint{font-size:13px;color:var(--warn);margin-top:4px}.done{color:var(--ok)}
nav.foot{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid var(--line);display:flex;gap:8px;padding:8px 12px;align-items:center}
nav.foot .s{flex:1;font-size:13px;color:var(--muted);text-align:center}
table{border-collapse:collapse;width:100%;font-size:14px}td{border:1px solid var(--line);padding:6px;vertical-align:top}
img.scan{width:100%;border:1px solid var(--line);border-radius:6px;margin:4px 0}
.locked{background:#e7f5ee;border:1px solid #9fd6b8;border-radius:10px;padding:10px}
details summary{cursor:pointer;color:var(--brand);font-weight:600}
</style></head><body>
<header><button id="menu">目录</button><div class="t" id="title">加载中…</div><button id="help">说明</button></header>
<main id="main"></main>
<nav class="foot"><button id="prev">上一单元</button><div class="s" id="status"></div><button id="next" class="primary">下一单元</button></nav>
<script>
const base=location.pathname.replace(/\/+$/,'');let D=null,U=[],V={},submitted=null,cur=-1,timers={};
const $=s=>document.querySelector(s),esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const TIME=/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/,MIN=/^\d{1,3}$/;
function setStatus(t,cls){const s=$('#status');s.textContent=t;s.className='s '+(cls||'')}
async function api(p,body){const r=await fetch(base+p,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const j=await r.json();if(!r.ok)throw Object.assign(new Error(j.error||r.status),{info:j});return j}
function nowText(){const d=new Date(),p=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`}
function unitProblems(id){const u=D.units[id],out=[];let und=0,notes=[];u.candidates.forEach((c,i)=>{const v=V[`${id}_c${i+1}`];if(!v)und++;else if(v!=='adopt'&&!(V[`${id}_c${i+1}_note`]||'').trim())notes.push(i+1)});
 if(und)out.push(`还有 ${und} 条候选未处理`);if(notes.length)out.push(`第 ${notes.join('、')} 条需写修改内容或拒绝理由`);
 if(!(V[id+'_missing']||'').trim())out.push('漏项未填（没有就写“无”）');
 const s=V[id+'_start']||'',e=V[id+'_end']||'';if(!TIME.test(s)||!TIME.test(e))out.push('开始或结束时刻未填');else if(e<s)out.push('结束早于开始');
 if(!(V[id+'_pause']||'').trim())out.push('暂停情况未填（没有就写“无”）');if(D.minutes.some(([m])=>!MIN.test(V[`${id}_${m}`]||'')))out.push('三段主动分钟未填全');return out}
function globalProblems(){return[['exposure','既往接触'],['ai_use','AI使用情况']].filter(([k])=>!(V[k]||'').trim()).map(([,l])=>l+'未填')}
function decided(){let n=0,t=0;U.forEach(id=>D.units[id].candidates.forEach((c,i)=>{t++;if(V[`${id}_c${i+1}`])n++}));return[n,t]}
function progressText(){const[n,t]=decided();return `已处理 ${n}/${t} 条`}
async function save(changes){if(submitted)return;Object.assign(V,changes);setStatus('保存中…');
 try{await api('/api/save',changes);setStatus('已保存 · '+progressText(),'done')}catch(e){setStatus('保存失败，请检查网络后再改一次')}}
function saveText(name,val){V[name]=val;clearTimeout(timers[name]);timers[name]=setTimeout(()=>save({[name]:val}),700)}
const dis=()=>submitted?'disabled':'';
function textBox(name,label,ph,small){return `<div class="row"><div class="lab">${esc(label)}</div><textarea data-name="${name}" placeholder="${esc(ph||'')}" ${small?'style="min-height:44px"':''} ${dis()}>${esc(V[name]||'')}</textarea></div>`}
function timeBox(name,label){return `<div class="row"><div class="lab">${esc(label)}</div><div class="inline"><input type="text" data-name="${name}" value="${esc(V[name]||'')}" placeholder="2026-11-13 14:05" ${dis()}><button data-now="${name}" ${dis()}>填入现在</button></div></div>`}
function hint(id){const el=document.getElementById('hint_'+id);if(!el)return;const p=unitProblems(id);el.className='hint'+(p.length?'':' done');el.textContent=p.length?p.join('；'):'本单元已填完'}
function totalMin(id){const v=D.minutes.map(([m])=>V[`${id}_${m}`]||'');return v.every(x=>MIN.test(x))?v.reduce((a,b)=>a+ +b,0):'—'}
function show(i){cur=i;const id=U[i],u=D.units[id];$('#title').textContent=`${id} · ${u.candidates.length} 条候选（${i+1}/${U.length}）`;
 let h=`<div class="card"><h2>${id} 单元目标</h2><div>${esc(u.target)}</div>`;
 if(u.seen_in_development)h+='<div class="muted">该单元资料在项目开发中使用过，系统候选质量可能偏乐观。</div>';
 h+='<h3>原文</h3>';u.texts.forEach(t=>{h+=`<div class="src"><div class="muted">${esc(t.source_id)} · ${esc(t.locator)}${t.transcription?'（署名AI转录，仅便于定位，以下方扫描页为准）':''}</div>${esc(t.text)}</div>`});
 u.images.forEach(n=>{h+=`<details><summary>扫描原页 ${esc(n.replace(/\D/g,''))}（点开可放大）</summary><a href="${base}/img/${n}" target="_blank" rel="noreferrer"><img class="scan" src="${base}/img/${n}" alt="扫描原页"></a></details>`});
 h+='<div class="muted">原始网址与原件散列：'+u.sources.map(s=>`${esc(s.source_id)}　${esc(s.url)}　SHA-256 ${esc(s.original_sha256.slice(0,12))}…`).join('<br>')+'</div></div>';
 h+=`<div class="card"><h3>① 开始</h3>`+timeBox(id+'_start','开始时刻（开始阅读原文时点）')+'</div>';
 h+=`<div class="card"><h3>② 逐条复核</h3><div class="muted">直接采纳＝原样可用；修改后采纳＝写出改后的完整内容；拒绝＝写理由。</div></div>`;
 u.candidates.forEach((c,n)=>{const k=`${id}_c${n+1}`;h+=`<div class="card cand"><div><span class="tag">${n+1}</span><span class="tag">${esc(D.kinds[c.kind]||c.kind)}</span></div>
  <div style="margin-top:4px"><b>${esc(c.subject)}</b>　—${esc(c.predicate)}→　<b>${esc(c.object)}</b></div>`+
  (c.qualifiers.length?`<div class="muted">限定：${esc(c.qualifiers.join('；'))}</div>`:'')+`<div class="quote">「${esc(c.quote)}」</div>
  <div class="row"><div class="seg">`+D.decisions.map(([code,t])=>`<label><input type="radio" name="${k}" value="${code}" ${V[k]===code?'checked':''} ${dis()}><span>${esc(t)}</span></label>`).join('')+`</div></div>`+
  textBox(k+'_note','修改内容或拒绝理由','直接采纳可留空',true)+'</div>'});
 h+=`<div class="card"><h3>③ 系统候选漏掉的内容</h3>`+textBox(id+'_missing','每行一条：原文定位＋漏掉的内容；没有就写“无”','例：第一百五十五条 第二款 ……')+'</div>';
 h+=`<div class="card"><h3>④ 结束与用时</h3>`+timeBox(id+'_end','结束时刻')+textBox(id+'_pause','暂停时段及原因','没有就写“无”',true)+
  `<div class="row"><div class="lab">主动分钟（整数，不含暂停）</div><div class="mins">`+D.minutes.map(([m,l])=>`<div><div class="muted">${esc(l)}</div><input type="text" inputmode="numeric" data-name="${id}_${m}" value="${esc(V[id+'_'+m]||'')}" ${dis()}></div>`).join('')+
  `</div><div class="muted" id="total_${id}">合计：${totalMin(id)} 分钟</div></div><div class="hint" id="hint_${id}"></div></div>`;
 $('#main').innerHTML=h;hint(id);window.scrollTo(0,0);$('#prev').disabled=i===0;$('#next').textContent=i===U.length-1?'交表':'下一单元';
 $('#main').querySelectorAll('input[type=radio]').forEach(r=>r.addEventListener('change',()=>{save({[r.name]:r.value});hint(id)}));
 $('#main').querySelectorAll('textarea,input[type=text]').forEach(t=>t.addEventListener('input',()=>{saveText(t.dataset.name,t.value.trim()===''?'':t.value);hint(id);const tot=document.getElementById('total_'+id);if(tot)tot.textContent='合计：'+totalMin(id)+' 分钟'}));
 $('#main').querySelectorAll('button[data-now]').forEach(b=>b.onclick=()=>{const name=b.dataset.now,el=$(`input[data-name="${name}"]`);el.value=nowText();save({[name]:el.value});hint(id)});
 try{localStorage.setItem(base+':cur',i)}catch(e){}}
function menu(){cur=-1;$('#title').textContent='目录 · '+progressText();$('#prev').disabled=true;$('#next').textContent='开始/继续';
 let h='<div class="card"><h2>单元</h2><table>'+U.map((id,i)=>{const p=unitProblems(id);return `<tr><td><a href="#" data-i="${i}">${id}</a>（${D.units[id].candidates.length} 条）</td><td class="${p.length?'hint':'done'}">${p.length?esc(p.join('；')):'已填完'}</td></tr>`}).join('')+'</table></div>';
 const g=globalProblems(),left=U.reduce((n,id)=>n+unitProblems(id).length,0)+g.length;h+='<div class="card"><h2>交表</h2>';
 if(submitted)h+=`<div class="locked">已于 ${esc(submitted)} 交表，记录已锁定。请告诉陈子炫已完成。</div>`;
 else h+=(g.length?`<div class="hint">说明页还有：${esc(g.join('；'))}</div>`:'')+`<div>${left?'还有未填项，见上表。':'全部填完。'}内容自动保存，可随时退出，用同一链接接着做。交表后不能再改。</div><p><button class="primary" id="submit" ${left?'disabled':''}>确认交表</button></p>`;
 $('#main').innerHTML=h+'</div>';$('#main').querySelectorAll('a[data-i]').forEach(a=>a.onclick=e=>{e.preventDefault();show(+a.dataset.i)});
 const sb=$('#submit');if(sb)sb.onclick=async()=>{if(!confirm('交表后不能再修改，确认吗？'))return;try{const r=await api('/api/submit',{});submitted=r.submitted_at;menu()}catch(e){alert(e.message+(e.info&&e.info.problems?'\n'+e.info.problems.join('\n'):''))}};window.scrollTo(0,0)}
function help(){cur=-1;$('#title').textContent='填写说明';$('#prev').disabled=true;$('#next').textContent='开始/继续';
 let h=`<div class="card"><h2>低资源构建 · 方法B候选复核</h2><div>复核人：${esc(D.reviewer)}　单元：${esc(U.join('、'))}（共 ${decided()[1]} 条候选）</div>
 <div class="muted">候选文件为 11 月 5 日存证版本，SHA-256 ${esc(D.candidates_sha256.slice(0,16))}…，未重新生成。“方法B”指人工复核系统候选，与盲评里的输出A/B无关。</div>
 <h3>怎么做</h3><div>每个单元按 ①开始 → ②逐条复核 → ③补漏项 → ④结束与用时 的顺序做。开始阅读原文前先点“填入现在”记开始时刻；休息或处理别的事时暂停并记下时段。每次点选、打字都会自动保存，服务器同时记下保存时刻，可随时退出，用同一链接接着做。</div>
 <h3>规则</h3><div>独立完成，不和别人讨论候选；不使用AI判断候选对错或补写漏项；本链接含未公开候选，不转发。以页面给出的原文为准，网址只作出处。</div>
 <h3>各项怎么填</h3><table>`+D.rules.map(([a,b])=>`<tr><td><b>${esc(a)}</b></td><td>${esc(b)}</td></tr>`).join('')+'</table></div>';
 h+='<div class="card"><h3>开始前先填</h3>'+textBox('exposure','既往接触（如实填写：盲评、扫描复核等是否读过这些原文；是否看过这些候选）','例：11月7日盲评中读过……')+
 textBox('ai_use','AI使用情况（没用写“未使用”；只用于排版也要写明）','未使用')+'</div>';
 $('#main').innerHTML=h;$('#main').querySelectorAll('textarea').forEach(t=>t.addEventListener('input',()=>saveText(t.dataset.name,t.value)));window.scrollTo(0,0)}
$('#menu').onclick=menu;$('#help').onclick=help;
$('#prev').onclick=()=>{if(cur>0)show(cur-1)};
$('#next').onclick=()=>{if(cur<0){let i=0;try{i=+(localStorage.getItem(base+':cur')||0)}catch(e){}show(Math.min(i,U.length-1))}else if(cur<U.length-1)show(cur+1);else menu()};
(async()=>{try{D=await api('/api/data');U=D.assignment[D.reviewer];const a=await api('/api/answers');V=a.values||{};submitted=a.submitted_at;
 setStatus(submitted?'已交表（只读）':progressText());help()}catch(e){$('#main').innerHTML='<div class="card">加载失败，请检查网络后刷新。</div>'}})();
</script></body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_pack = sub.add_parser("pack", help="Build method-b-data.json and reviewer links")
    p_pack.add_argument("--candidates", type=Path, required=True)
    p_pack.add_argument("--units", type=Path, required=True)
    p_pack.add_argument("--corpus", type=Path, required=True)
    p_pack.add_argument("--scan-pdf", type=Path)
    p_pack.add_argument("--reviewer", action="append", required=True)
    p_pack.add_argument("--out", type=Path, required=True)
    p_serve = sub.add_parser("serve", help="Serve the method-B form (and optionally the finished questionnaire)")
    p_serve.add_argument("--folder", type=Path, required=True)
    p_serve.add_argument("--holdout-folder", type=Path)
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8090)
    p_serve.add_argument("--cert")
    p_serve.add_argument("--key")
    p_export = sub.add_parser("export", help="Write Markdown records and a counts-only summary from an answers copy")
    p_export.add_argument("--folder", type=Path, required=True)
    p_export.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "pack":
        print(json.dumps(pack(args.candidates, args.units, args.corpus, args.reviewer, args.out, args.scan_pdf),
                         ensure_ascii=False, indent=1))
    elif args.command == "serve":
        serve(args.folder, args.holdout_folder, args.host, args.port, args.cert, args.key)
    else:
        print(json.dumps(export(args.folder, args.out), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
