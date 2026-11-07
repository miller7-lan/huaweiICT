"""Phone-friendly web questionnaire for the 30-question blind review (standard library only).

pack  (local): check the review package hashes and write review-data.json plus a
      tokens.json with one secret link per reviewer. The A/B key is never read.
serve (server): serve each reviewer only their own page and answers under
      /r/<token>/; unknown paths answer 404. Answers autosave to
      answers/<reviewer>.json with the same field names and codes as the PDF form,
      so holdout_review_form_to_csv.py converts either. Submitting locks the sheet.
The data holds reference answers: keep the folder private and remove it after review.
"""
import argparse
from hashlib import sha256
import html
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

CHOICES = [
    ["ok", "结果可接受", [["Y", "是"], ["N", "否"]]],
    ["cov", "关键断言覆盖", [["all", "全部"], ["part", "部分"], ["none", "无"], ["na", "不适用"]]],
    ["err", "有事实错误", [["Y", "是"], ["N", "否"]]],
    ["cite", "引用支撑结论", [["Y", "是"], ["N", "否"], ["na", "不适用"]]],
    ["ban", "触犯禁止项", [["Y", "是"], ["N", "否"]]],
    ["score", "总评", [["s2", "2"], ["s1", "1"], ["s0", "0"]]],
]
# Remark required by the review rules when one of these codes is chosen.
NEEDS_REMARK = {"ok": ["N"], "cite": ["N"], "err": ["Y"], "ban": ["Y"], "score": ["s1", "s0"]}
RULES = [
    ["结果可接受", "实际结果（答复/停止）在“可接受结果”里选“是”，否则“否”"],
    ["关键断言覆盖", "只看答复题：参考关键断言全部讲到选“全部”，讲到一部分选“部分”，都没讲到选“无”；停止题或输出为停止时选“不适用”"],
    ["有事实错误", "输出里有与原始资料不符的说法（数字、日期、条件、机构、文件名等）选“是”，并在备注写明哪句"],
    ["引用支撑结论", "引文确实来自所写来源和定位、并能支撑结论选“是”；对不上或撑不起结论选“否”；没有引文的停止输出选“不适用”"],
    ["触犯禁止项", "输出出现了“不得出现”所列内容选“是”，并在备注写明"],
    ["总评", "2 = 可直接给办事人员参考；1 = 方向对但需要改；0 = 不能用或可能误导"],
    ["备注", "选了“否”、“是（错误/禁止项）”或总评 0、1 时必填一句理由"],
]
OUTCOMES = {"answer": "答复", "stop": "停止"}
TEXT_LIMIT = 2000


# ---------- pack (runs locally) ----------

def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def verify(folder):
    for sums in (folder / "SHA256SUMS.txt", folder / "sources" / "SHA256SUMS.txt"):
        for line in sums.read_text("utf-8").splitlines():
            if line.strip():
                expected, name = line.split(maxsplit=1)
                if digest(sums.parent / name.lstrip("*")) != expected:
                    raise SystemExit("PACKAGE_HASH_MISMATCH: " + name)


def as_list(value):
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def zh(value):
    return "、".join(OUTCOMES.get(v, v) for v in as_list(value))


def render_source(text):
    """Frozen source Markdown to plain HTML; links stay text so no token leaves via Referer."""
    parts, section = [], 0
    for line in text.splitlines():
        safe = html.escape(line)
        if line.startswith("# "):
            parts.append(f"<h1>{safe[2:]}</h1>")
        elif line.startswith("## "):
            section += 1
            parts.append(f'<h3 id="s{section}">{safe[3:]}</h3>')
        elif line.strip():
            parts.append(f"<p>{safe}</p>")
    return "\n".join(parts)


def pack(package, reviewers, out):
    verify(package)
    blind_path = next(package.glob("blind-*.json"))
    questions = json.loads((package / "questions.json").read_text("utf-8"))
    reference = {r["id"]: r for r in json.loads((package / "reference.json").read_text("utf-8"))}
    blind = json.loads(blind_path.read_text("utf-8"))
    by_id = {item["id"]: item for item in blind["items"]}
    if set(by_id) != {q["id"] for q in questions} or set(reference) != set(by_id):
        raise SystemExit("BLIND_FILE_DOES_NOT_MATCH_QUESTIONS")
    sources = sorted((package / "sources").glob("*.md"))
    items = []
    for q in questions:
        r, item = reference[q["id"]], by_id[q["id"]]
        items.append({"id": q["id"], "category": r["category"], "question": q["question"],
                      "context": q.get("context") or "", "expected": zh(r["expected_outcome"]),
                      "acceptable": zh(r.get("acceptable_outcomes")),
                      "key_assertions": r.get("key_assertions") or [],
                      "must_not": as_list(r.get("must_not")), "stop_boundary": as_list(r.get("stop_boundary")),
                      "notes": as_list(r.get("notes")),
                      "outputs": {label: {"outcome": OUTCOMES.get(item[label]["outcome"], item[label]["outcome"]),
                                          "answer": item[label]["answer"] or "",
                                          "assertions": item[label]["assertions"],
                                          "stop_reason": item[label]["stop_reason"] or "",
                                          "unknowns": item[label]["unknowns"] or []} for label in "AB"}})
    data = {"run_id": blind["run_id"], "package_sums_sha256": digest(package / "SHA256SUMS.txt"),
            "choices": CHOICES, "needs_remark": NEEDS_REMARK, "rules": RULES, "items": items,
            "sources": [{"code": p.name.split("-", 1)[0], "id": p.name.split("-", 1)[1][:-3],
                         "html": render_source(p.read_text("utf-8"))} for p in sources]}
    out.mkdir(parents=True, exist_ok=True)
    (out / "review-data.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    tokens_path = out / "tokens.json"
    tokens = json.loads(tokens_path.read_text("utf-8")) if tokens_path.exists() else {}
    for name in reviewers:
        if name not in tokens.values():
            tokens[secrets.token_urlsafe(18)] = name
    tokens_path.write_text(json.dumps(tokens, ensure_ascii=False, indent=1), "utf-8")
    return {"folder": str(out), "run_id": data["run_id"], "questions": len(items), "reviewers": sorted(tokens.values()),
            "key_read": False}


# ---------- serve (runs on the server) ----------

class Store:
    def __init__(self, folder):
        self.folder = folder
        self.data = json.loads((folder / "review-data.json").read_text("utf-8"))
        self.tokens = json.loads((folder / "tokens.json").read_text("utf-8"))
        self.answers_dir = folder / "answers"
        self.answers_dir.mkdir(exist_ok=True)
        self.lock = threading.Lock()
        self.allowed = {}
        for item in self.data["items"]:
            for label in "AB":
                for suffix, _, options in CHOICES:
                    self.allowed[f"{item['id']}_{label}_{suffix}"] = {code for code, _ in options}
                self.allowed[f"{item['id']}_{label}_note"] = None
            self.allowed[f"{item['id']}_ref_issue"] = None
            self.allowed[f"{item['id']}_ref_basis"] = None
        self.sources = {s["code"]: s for s in self.data["sources"]}

    def path(self, reviewer):
        return self.answers_dir / (sha256(reviewer.encode("utf-8")).hexdigest()[:16] + ".json")

    def load(self, reviewer):
        path = self.path(reviewer)
        if path.exists():
            return json.loads(path.read_text("utf-8"))
        return {"run_id": self.data["run_id"], "reviewer": reviewer, "values": {}, "submitted_at": None}

    def write(self, record):
        record["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        path = self.path(record["reviewer"])
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps(record, ensure_ascii=False, indent=1), "utf-8")
        os.replace(pending, path)

    def problems(self, values):
        missing, remarks = [], []
        for item in self.data["items"]:
            for label in "AB":
                prefix = f"{item['id']}_{label}_"
                missing += [prefix + s for s, _, _ in CHOICES if not values.get(prefix + s)]
                if not values.get(prefix + "note", "").strip() and any(
                        values.get(prefix + s) in codes for s, codes in NEEDS_REMARK.items()):
                    remarks.append(item["id"] + label)
        return missing, remarks

    def save(self, reviewer, changes):
        with self.lock:
            record = self.load(reviewer)
            if record.get("submitted_at"):
                return 409, {"error": "已提交，不能再修改"}
            for name, value in changes.items():
                if name not in self.allowed or not isinstance(value, str):
                    return 400, {"error": "无效字段"}
                allowed = self.allowed[name]
                if (allowed is not None and value not in allowed and value != "") or len(value) > TEXT_LIMIT:
                    return 400, {"error": "无效取值"}
                record["values"][name] = value
            self.write(record)
            return 200, {"saved": len(changes), "updated_at": record["updated_at"]}

    def submit(self, reviewer):
        with self.lock:
            record = self.load(reviewer)
            if record.get("submitted_at"):
                return 200, {"submitted_at": record["submitted_at"]}
            missing, remarks = self.problems(record["values"])
            if missing or remarks:
                return 409, {"error": "还有未填项", "missing": len(missing), "remarks": remarks}
            record["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self.write(record)
            return 200, {"submitted_at": record["submitted_at"]}


class Handler(BaseHTTPRequestHandler):
    store = None
    server_version = "review"
    sys_version = ""

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), re.sub(r"/r/[^/ ]+", "/r/<token>", fmt % args)))

    def reply(self, status, body, kind="application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else (
            json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body).encode("utf-8")
        self.send_response(status)
        for header, value in (("Content-Type", kind), ("Content-Length", str(len(raw))),
                              ("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"),
                              ("X-Robots-Tag", "noindex, nofollow"), ("X-Content-Type-Options", "nosniff"),
                              ("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                          "script-src 'self' 'unsafe-inline'; base-uri 'none'")):
            self.send_header(header, value)
        self.end_headers()
        self.wfile.write(raw)

    def route(self):
        match = re.fullmatch(r"/r/([A-Za-z0-9_-]{16,64})(/.*)?", self.path.split("?", 1)[0])
        if not match or match.group(1) not in self.store.tokens:
            return None, None
        return self.store.tokens[match.group(1)], match.group(2) or "/"

    def do_GET(self):
        reviewer, rest = self.route()
        if reviewer is None:
            return self.reply(404, "not found", "text/plain; charset=utf-8")
        if rest == "/":
            return self.reply(200, PAGE, "text/html; charset=utf-8")
        if rest == "/api/data":
            data = dict(self.store.data, sources=[{k: s[k] for k in ("code", "id")} for s in self.store.data["sources"]])
            return self.reply(200, dict(data, reviewer=reviewer))
        if rest == "/api/answers":
            return self.reply(200, self.store.load(reviewer))
        source = re.fullmatch(r"/source/([A-Z][0-9])", rest)
        if source and source.group(1) in self.store.sources:
            s = self.store.sources[source.group(1)]
            return self.reply(200, SOURCE_PAGE.replace("__TITLE__", html.escape(s["code"] + " " + s["id"]))
                              .replace("__BODY__", s["html"]), "text/html; charset=utf-8")
        return self.reply(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        reviewer, rest = self.route()
        if reviewer is None:
            return self.reply(404, "not found", "text/plain; charset=utf-8")
        length = int(self.headers.get("Content-Length") or 0)
        if length > 256 * 1024:
            return self.reply(413, {"error": "too large"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.reply(400, {"error": "bad json"})
        if rest == "/api/save" and isinstance(body, dict):
            return self.reply(*self.store.save(reviewer, body))
        if rest == "/api/submit":
            return self.reply(*self.store.submit(reviewer))
        return self.reply(404, {"error": "not found"})


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def serve(folder, host, port, cert=None, key=None):
    Handler.store = Store(folder)
    httpd = Server((host, port), Handler)
    if cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    sys.stderr.write(f"serving {len(Handler.store.tokens)} reviewer link(s) on {host}:{port}\n")
    httpd.serve_forever()


SOURCE_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer">
<title>__TITLE__</title><style>body{font:16px/1.65 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
margin:0;padding:14px 16px 60px;background:#fafafa;color:#222}h1{font-size:19px}h3{font-size:15px;color:#1f4e79;
margin:18px 0 4px}p{margin:4px 0;word-break:break-all}.top{position:sticky;top:0;background:#fafafa;padding:6px 0}
</style></head><body><div class="top"><a href="javascript:history.back()">← 返回评分</a>（可用浏览器“页内查找”搜条款号）</div>
__BODY__</body></html>"""

PAGE = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer">
<title>30题人工盲评</title>
<style>
:root{--ink:#1d2433;--muted:#5d6675;--line:#d9dee7;--brand:#1f4e79;--soft:#eef3f9;--ok:#1b7f4b;--warn:#b5651d;--bad:#b42318}
*{box-sizing:border-box}body{margin:0;font:16px/1.6 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);background:#f6f7f9}
header{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid var(--line);padding:8px 12px;display:flex;gap:8px;align-items:center}
header .t{font-weight:600;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
button{font:inherit;border:1px solid var(--line);background:#fff;border-radius:8px;padding:6px 12px;color:var(--ink)}
button.primary{background:var(--brand);border-color:var(--brand);color:#fff}
main{max-width:760px;margin:0 auto;padding:12px 12px 90px}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:10px 0}
h2{font-size:18px;margin:4px 0 8px}h3{font-size:15px;margin:12px 0 4px;color:var(--brand)}
.muted{color:var(--muted);font-size:14px}.quote{color:var(--muted);font-size:14px;border-left:3px solid var(--line);padding-left:8px;margin:2px 0 8px}
ul{margin:4px 0;padding-left:20px}.out{border-left:4px solid var(--brand)}
.row{margin:10px 0}.row .lab{font-weight:600;font-size:14px;margin-bottom:4px}
.seg{display:flex;flex-wrap:wrap;gap:6px}.seg label{flex:1 1 0;min-width:64px}
.seg input{position:absolute;opacity:0;pointer-events:none}
.seg span{display:block;text-align:center;padding:9px 4px;border:1px solid var(--line);border-radius:8px;background:#fff}
.seg input:checked+span{background:var(--brand);border-color:var(--brand);color:#fff}
textarea{width:100%;min-height:64px;font:inherit;border:1px solid var(--line);border-radius:8px;padding:8px}
.hint{font-size:13px;color:var(--warn);margin-top:4px}.done{color:var(--ok)}
nav.foot{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:1px solid var(--line);display:flex;gap:8px;padding:8px 12px;align-items:center}
nav.foot .s{flex:1;font-size:13px;color:var(--muted);text-align:center}
.grid{display:grid;grid-template-columns:repeat(5,1fr);gap:6px}.grid button{padding:10px 0}
.grid .full{background:#e7f5ee;border-color:#9fd6b8}.grid .part{background:#fff6e5;border-color:#f0c98a}
table{border-collapse:collapse;width:100%;font-size:14px}td{border:1px solid var(--line);padding:6px;vertical-align:top}
.locked{background:#e7f5ee;border:1px solid #9fd6b8;border-radius:10px;padding:10px}
</style></head><body>
<header><button id="menu">目录</button><div class="t" id="title">加载中…</div><button id="help">说明</button></header>
<main id="main"></main>
<nav class="foot"><button id="prev">上一题</button><div class="s" id="status"></div><button id="next" class="primary">下一题</button></nav>
<script>
const base=location.pathname.replace(/\/+$/,'');let D=null,V={},submitted=null,cur=-1,timers={};
const $=s=>document.querySelector(s),esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function setStatus(t,cls){const s=$('#status');s.textContent=t;s.className='s '+(cls||'')}
async function api(p,body){const r=await fetch(base+p,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const j=await r.json();if(!r.ok)throw Object.assign(new Error(j.error||r.status),{info:j});return j}
function outStatus(id,l){const p=id+'_'+l+'_';const miss=D.choices.filter(c=>!V[p+c[0]]).length;
 const need=!(V[p+'note']||'').trim()&&Object.entries(D.needs_remark).some(([s,codes])=>codes.includes(V[p+s]));return{miss,need}}
function qStatus(id){const a=outStatus(id,'A'),b=outStatus(id,'B');const total=D.choices.length*2;const miss=a.miss+b.miss;
 return miss===total?'empty':(miss||a.need||b.need)?'part':'full'}
function progress(){let n=0;D.items.forEach(it=>['A','B'].forEach(l=>{const s=outStatus(it.id,l);if(!s.miss&&!s.need)n++}));return n}
async function save(changes){if(submitted)return;Object.assign(V,changes);setStatus('保存中…');
 try{await api('/api/save',changes);setStatus('已保存 · 已完成 '+progress()+'/60 份','done')}catch(e){setStatus('保存失败，请检查网络后再点一次','');}}
function saveText(name,val){V[name]=val;clearTimeout(timers[name]);timers[name]=setTimeout(()=>save({[name]:val}),700)}
function radios(name,label,options){return `<div class="row"><div class="lab">${esc(label)}</div><div class="seg">`+
 options.map(([code,text])=>`<label><input type="radio" name="${name}" value="${code}" ${V[name]===code?'checked':''} ${submitted?'disabled':''}><span>${esc(text)}</span></label>`).join('')+`</div></div>`}
function textBox(name,label,ph){return `<div class="row"><div class="lab">${esc(label)}</div><textarea data-name="${name}" placeholder="${esc(ph||'')}" ${submitted?'disabled':''}>${esc(V[name]||'')}</textarea></div>`}
function outputCard(it,l){const o=it.outputs[l],p=it.id+'_'+l+'_';let h=`<div class="card out"><h3>输出 ${l}（${esc(o.outcome)}）</h3><div>${esc(o.answer)||'（无）'}</div>`;
 o.assertions.forEach(a=>{h+=`<div>· ${esc(a.claim)}</div><div class="quote">「${esc(a.quote)}」—— ${esc(a.source_id)} ${esc(a.locator)}</div>`});
 if(o.stop_reason)h+=`<div>停止理由：${esc(o.stop_reason)}</div>`;if(o.unknowns.length)h+=`<div>未知项：${esc(o.unknowns.join('；'))}</div>`;
 h+=`<h3>对输出 ${l} 的评分</h3>`+D.choices.map(([s,label,opts])=>radios(p+s,label,opts)).join('')+textBox(p+'note','备注','选了“否”、“是（错误/禁止项）”或总评 0、1 时必填一句理由');
 return h+`<div class="hint" id="h_${it.id}_${l}"></div></div>`}
function hints(it){['A','B'].forEach(l=>{const s=outStatus(it.id,l),el=document.getElementById('h_'+it.id+'_'+l);if(!el)return;
 el.className='hint'+(!s.miss&&!s.need?' done':'');el.textContent=!s.miss&&!s.need?'这份已填完':[s.miss?`还有 ${s.miss} 项未选`:'',s.need?'按说明需要写一句理由':''].filter(Boolean).join('；')})}
function show(i){cur=i;const it=D.items[i];$('#title').textContent=`${it.id} · ${it.category}（${i+1}/30）`;
 let h=`<div class="card"><h2>${esc(it.id)} · ${esc(it.category)}</h2><div><b>问题：</b>${esc(it.question)}</div>`;
 if(it.context)h+=`<div><b>情境：</b>${esc(it.context)}</div>`;h+=`<div><b>应有结果：</b>${esc(it.expected)}（可接受：${esc(it.acceptable)}）</div>`;
 if(it.key_assertions.length){h+='<h3>参考关键断言</h3>';it.key_assertions.forEach((a,n)=>{h+=`<div>${n+1}. ${esc(a.claim)} —— ${esc(a.source)} ${esc(a.locator)}</div><div class="quote">${esc(a.quote)}</div>`})}
 [['不得出现',it.must_not],['停止边界',it.stop_boundary],['出题备注',it.notes]].forEach(([t,v])=>{if(v.length)h+=`<h3>${t}</h3><ul>`+v.map(x=>`<li>${esc(x)}</li>`).join('')+'</ul>'});
 h+='<div class="muted">查看原文：'+D.sources.map(s=>`<a href="${base}/source/${s.code}">${s.code}</a>`).join('　')+'</div></div>';
 h+=outputCard(it,'A')+outputCard(it,'B');
 h+=`<div class="card"><h3>${it.id} 参考答案复核（没有问题就留空）</h3>`+textBox(it.id+'_ref_issue','参考答案的问题')+textBox(it.id+'_ref_basis','原文依据（来源编号 + 定位）')+'</div>';
 $('#main').innerHTML=h;hints(it);window.scrollTo(0,0);$('#prev').disabled=i===0;$('#next').textContent=i===D.items.length-1?'完成情况':'下一题';
 $('#main').querySelectorAll('input[type=radio]').forEach(r=>r.addEventListener('change',()=>{save({[r.name]:r.value});hints(it)}));
 $('#main').querySelectorAll('textarea').forEach(t=>t.addEventListener('input',()=>{saveText(t.dataset.name,t.value);hints(it)}));
 try{localStorage.setItem(base+':cur',i)}catch(e){}}
function menu(){cur=-1;$('#title').textContent='目录 · 已完成 '+progress()+'/60 份';$('#prev').disabled=true;$('#next').textContent='开始/继续';
 let h='<div class="card"><h2>题目目录</h2><div class="muted">绿色 = 两份都填完；黄色 = 填了一部分。</div><div class="grid" style="margin-top:8px">'+
 D.items.map((it,i)=>`<button data-i="${i}" class="${qStatus(it.id)}">${it.id}</button>`).join('')+'</div></div>';
 const left=60-progress();h+='<div class="card"><h2>交表</h2>';
 if(submitted)h+=`<div class="locked">已于 ${esc(submitted)} 提交，评分已锁定。请告诉陈子炫已评完。</div>`;
 else h+=`<div>${left?`还有 ${left} 份输出没填完（含需写理由的备注）。`:'60 份都已填完。'}所有内容自动保存，可随时退出再进。全部填完后点下面按钮交表，交表后不能再改。</div><p><button class="primary" id="submit" ${left?'disabled':''}>确认交表</button></p>`;
 $('#main').innerHTML=h+'</div>';$('#main').querySelectorAll('.grid button').forEach(b=>b.onclick=()=>show(+b.dataset.i));
 const sb=$('#submit');if(sb)sb.onclick=async()=>{if(!confirm('交表后不能再修改，确认吗？'))return;try{const r=await api('/api/submit',{});submitted=r.submitted_at;menu()}catch(e){alert(e.message)}};window.scrollTo(0,0)}
function help(){cur=-1;$('#title').textContent='填写说明';$('#prev').disabled=true;$('#next').textContent='开始/继续';
 $('#main').innerHTML=`<div class="card"><h2>30题人工盲评</h2><div>评审人：${esc(D.reviewer)}　运行编号：${esc(D.run_id)}</div>
 <h3>怎么填</h3><div>每题有输出 A、B，各点选 6 项，需要时写一句备注；每题末尾有参考答案复核栏，没问题就留空。每点一下、每打一段字都会自动保存，可以随时退出，下次打开同一链接接着填。全部填完到“目录”里交表。</div>
 <h3>规则</h3><div>独立评，交表前不讨论；不猜 A/B 对应哪种方法；不把题目、参考答案或输出交给任何 AI 工具代评或辅助打分；本链接含参考答案，不转发给他人。发现参考答案有误时不改参考，按原文事实评分，并在备注写“参考有误，见复核栏”。</div>
 <h3>各项怎么选</h3><table>`+D.rules.map(([a,b])=>`<tr><td><b>${esc(a)}</b></td><td>${esc(b)}</td></tr>`).join('')+`</table>
 <p class="muted">停止题的好输出：按停止边界停下，说清缺什么依据，不顺带给出可拿来办事的数字或结论。答复题的好输出：讲到关键断言、引文可核、没有额外编造。资料都是冻结的历史归档，任何输出把它说成“现行有效”都算事实错误。</p>
 <h3>资料编号对照</h3><table>`+D.sources.map(s=>`<tr><td>${s.code}</td><td><a href="${base}/source/${s.code}">${esc(s.id)}</a></td></tr>`).join('')+'</table></div>';window.scrollTo(0,0)}
$('#menu').onclick=menu;$('#help').onclick=help;
$('#prev').onclick=()=>{if(cur>0)show(cur-1)};
$('#next').onclick=()=>{if(cur<0){let i=0;try{i=+(localStorage.getItem(base+':cur')||0)}catch(e){}show(Math.min(i,D.items.length-1))}else if(cur<D.items.length-1)show(cur+1);else menu()};
(async()=>{try{D=await api('/api/data');const a=await api('/api/answers');V=a.values||{};submitted=a.submitted_at;
 setStatus(submitted?'已交表（只读）':'已完成 '+progress()+'/60 份');help()}catch(e){$('#main').innerHTML='<div class="card">加载失败，请检查网络后刷新。</div>'}})();
</script></body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_pack = sub.add_parser("pack", help="Build review-data.json and reviewer links from the review package")
    p_pack.add_argument("--package", type=Path, required=True)
    p_pack.add_argument("--reviewer", action="append", required=True)
    p_pack.add_argument("--out", type=Path, required=True)
    p_serve = sub.add_parser("serve", help="Serve the questionnaire")
    p_serve.add_argument("--folder", type=Path, required=True)
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8090)
    p_serve.add_argument("--cert")
    p_serve.add_argument("--key")
    args = parser.parse_args()
    if args.command == "pack":
        print(json.dumps(pack(args.package, args.reviewer, args.out), ensure_ascii=False, indent=1))
    else:
        serve(args.folder, args.host, args.port, args.cert, args.key)


if __name__ == "__main__":
    main()
