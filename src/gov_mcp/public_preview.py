"""Fixed public-source preview with durable stages and campaign-wide call limits.

This module cannot publish knowledge, accepts no visitor text, and has no URL or
filesystem arguments exposed to agents. Model judgments are source support only.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3
import uuid

from gov_mcp.classification import ClassificationError, fingerprint, JevProvider, validate_response
from gov_mcp.deepseek import PROFILES, load_key
from gov_mcp.jev_translation import DeepSeekTranslator, EnglishJevProvider, text_slots

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {
    "erqi": "https://eq.jiceng.zhengzhou.gov.cn/004008002/3394639.jhtml",
    "minggong": "https://eq.jiceng.zhengzhou.gov.cn/004008002/3404478.jhtml",
}
PORTAL = "https://www.hnzwfw.gov.cn/portal/guide/9BA34BC110B96284D26CE82456A51500?region=410103000000"
CASE = {"data_class": "public", "applicant_kind": "fictional", "age_group": "adult",
        "self_service": True, "household_scope": "local_erqi", "reason": "expiry",
        "window": "minggong", "personal_data_collected": False}
QUESTION = "成年、二七区本地户籍、本人身份证期满换领：资料怎样记载材料、收费和时限？能提前30天吗？请保留未核定项和原文引用。"
SERVICE = "govkb_public_preview"
SKILL = "govkb-public-preview"
TOOLS = {"govkb_preview_open_query", "govkb_preview_retrieve", "govkb_preview_verify",
         "govkb_preview_state", "govkb_preview_finalize"}
PAUSES = {
    "materials": "现行材料与户口簿告知承诺、共享核验关系未核定。",
    "duration": "15工作日与其他服务承诺的范围、起止及领取关系未核定。",
    "fee_relief": "个人减免资格、证明方式及邮费关系未核定；指南收费栏不等于实际应缴金额。",
    "advance_days": "现行提前办理窗口未核定，不从旧指南补出30天或其他天数。",
}


class PreviewError(ValueError):
    pass


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text("utf-8"))


def write(path, value):
    path = Path(path)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", "utf-8")
    pending.replace(path)


class Sections(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections, self.current, self.parts = {}, None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section":
            if self.current is not None or not attrs.get("id") or not attrs.get("title"):
                raise PreviewError("SOURCE_STRUCTURE_CHANGED")
            self.current, self.parts = attrs, []
        elif self.current and tag in ("br", "p"):
            self.parts.append("\n")

    def handle_data(self, text):
        if self.current:
            self.parts.append(text)

    def handle_endtag(self, tag):
        if tag == "section" and self.current:
            identifier = self.current["id"]
            text = " ".join("".join(self.parts).split())
            if identifier in self.sections or not text:
                raise PreviewError("SOURCE_STRUCTURE_CHANGED")
            self.sections[identifier] = {"title": self.current["title"], "text": text}
            self.current = None


def source_digest(raw):
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def check_manifest(root):
    root = Path(root).resolve()
    manifest = read(root / "manifest.json")
    if (set(manifest) != {"schema", "mode", "data_class", "case", "sources", "portal"}
            or manifest["schema"] != 1 or manifest["mode"] != "public_preview"
            or manifest["data_class"] != "public" or fingerprint(manifest["case"]) != fingerprint(CASE)):
        raise PreviewError("PUBLIC_FIXED_SCOPE_REQUIRED")
    sources = manifest["sources"]
    if not isinstance(sources, list) or len(sources) != 2 or {s["source_id"] for s in sources} != set(SOURCES):
        raise PreviewError("FIXED_SOURCE_SET_REQUIRED")
    for source in sources:
        if (set(source) != {"source_id", "url", "data_class", "raw_sha256", "retrieved_at",
                           "acquisition_method", "published_at", "effective_at", "sections"}
                or source["data_class"] != "public" or SOURCES.get(source["source_id"]) != source["url"]
                or source["acquisition_method"] != "official_http_archive"
                or source["published_at"] is not None or source["effective_at"] is not None):
            raise PreviewError("UNAPPROVED_SOURCE_METADATA")
        when = datetime.fromisoformat(source["retrieved_at"])
        if when.tzinfo is None or not 0 <= (datetime.now(timezone.utc) - when).total_seconds() <= 86400:
            raise PreviewError("SOURCE_REFRESH_REQUIRED")
        path = (root / "sources" / (source["source_id"] + ".html")).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 1024**2:
            raise PreviewError("SOURCE_FILE_INVALID")
        raw = path.read_bytes()
        if source_digest(raw) != source["raw_sha256"]:
            raise PreviewError("SOURCE_HASH_CHANGED")
        parser = Sections()
        parser.feed(raw.decode("utf-8"))
        if parser.sections != source["sections"] or len(parser.sections) != 9:
            raise PreviewError("SOURCE_PARSE_CHANGED")
    portal = manifest["portal"]
    if (not isinstance(portal, dict) or set(portal) != {"url", "status", "checked_at", "http_status", "raw_sha256", "method"}
            or portal["url"] != PORTAL or portal["status"] not in {"http_shell", "access_stopped", "unavailable", "captured_unreviewed"}
            or portal["method"] != "normal_public_http_no_login"
            or (portal["status"] in {"http_shell", "captured_unreviewed"} and portal["raw_sha256"] is None)):
        raise PreviewError("PORTAL_PROVENANCE_INVALID")
    when = datetime.fromisoformat(portal["checked_at"])
    if when.tzinfo is None or not 0 <= (datetime.now(timezone.utc) - when).total_seconds() <= 86400:
        raise PreviewError("PORTAL_CHECK_REFRESH_REQUIRED")
    if portal["raw_sha256"] is not None:
        path = (root / "sources/portal.html").resolve()
        if not path.is_relative_to(root) or source_digest(path.read_bytes()) != portal["raw_sha256"]:
            raise PreviewError("PORTAL_HASH_CHANGED")
    return manifest


def source_facts(manifest):
    by_id = {s["source_id"]: s for s in manifest["sources"]}
    def cite(source_id, section_id):
        source = by_id[source_id]
        quote = source["sections"][section_id]["text"]
        return {"source_id": source_id, "section_id": section_id, "quote": quote,
                "start": 0, "end": len(quote), "raw_sha256": source["raw_sha256"],
                "url": source["url"] + "#" + section_id}
    fee = cite("erqi", "section-text_04")
    duration = cite("erqi", "section-text_03")
    materials = cite("erqi", "section-txt1")
    if ("20元/证" not in fee["quote"] or duration["quote"] != "15个工作日"
            or not all(w in materials["quote"] for w in ("居民户口簿", "原居民身份证", "未满十六周岁", "跨户籍所在地市"))):
        raise PreviewError("GUIDE_FACTS_CHANGED_REVIEW_REQUIRED")
    specs = [
        ("fee", "20元/证", "The county guide states a standard renewal fee of 20 CNY per card; this does not establish personal fee-relief eligibility.", fee),
        ("duration", "15个工作日", "The county guide states 15 working days; its relationship to other service commitments remains unverified.", duration),
        ("materials", ["居民户口簿", "原居民身份证"], "The county guide lists a household register and the original resident identity card, with separate conditions for minors and cross-city processing. This is a source description, not a confirmed current checklist.", materials),
        ("address", by_id["minggong"]["sections"]["section-text_05"]["text"], "The Minggong guide gives the window address shown in this claim's value.", cite("minggong", "section-text_05")),
        ("phone", by_id["minggong"]["sections"]["section-text_06"]["text"], "The Minggong guide gives the contact phone number shown in this claim's value.", cite("minggong", "section-text_06")),
    ]
    return [{"id": i, "value": value, "statement": statement, "citation": reference} for i, value, statement, reference in specs]


class OneTranslation(DeepSeekTranslator):
    def translate(self, texts):
        if self.attempts >= 1:
            raise ClassificationError("TRANSLATION_BUDGET_EXHAUSTED")
        return super().translate(texts)


class LiveVerifier:
    """One Jev physical attempt and at most one translation, with full input saved."""
    def __call__(self, state, questions, output):
        import os
        key = os.environ.get("TYPESAFE_API_KEY", "")
        if not key:
            key = read(ROOT / ".runtime/jev/official.credentials.json").get("TYPESAFE_API_KEY", "")
        if not load_key(PROFILES["official"]) or not isinstance(key, str) or not key.strip():
            raise PreviewError("CREDENTIAL_REQUIRED")
        raw = JevProvider(key, max_calls=1)
        translator = OneTranslation(enabled=True)
        adapter = EnglishJevProvider(raw, translator)
        if sum(len(t) for t in set(slot[2] for slot in text_slots(deepcopy(state), deepcopy(questions)))) > 3000:
            raise PreviewError("TRANSLATION_INPUT_TOO_LARGE")
        record = {"original_state": state, "original_questions": questions, "result": None}
        try:
            record["result"] = validate_response(adapter.evaluate(state, questions, "jev-1.13.0"), questions, "jev-1.13.0")
            return record["result"]
        except ClassificationError:
            # A 429/5xx retry blocked by max_calls=1 can otherwise surface as a
            # generic budget error. Preserve the original terminal HTTP cause.
            if raw.receipts:
                code = {401: "AUTHENTICATION_FAILED", 402: "PAYMENT_REQUIRED", 403: "ACCESS_DENIED",
                        429: "RATE_LIMITED", 451: "ACCESS_RESTRICTED"}.get(raw.receipts[-1].get("http_status"))
                if code:
                    raise PreviewError(code) from None
            raise
        finally:
            record.update(jev_receipts=raw.receipts, translation_receipts=adapter.translation_receipts)
            write(output, record)


class Preview:
    def __init__(self, root, verifier=None):
        self.root = Path(root).resolve()
        self.verifier = verifier
        self.db_path = self.root / "preview.sqlite3"
        with self.db() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, ordinal INTEGER UNIQUE, status TEXT, repair_note TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, run_id TEXT UNIQUE, payload TEXT);
                CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, value INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS flags(name TEXT PRIMARY KEY, value TEXT NOT NULL);""")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _save(self, db, task):
        db.execute("INSERT OR REPLACE INTO tasks VALUES(?,?,?)", (task["task_id"], task["run_id"], json.dumps(task, ensure_ascii=False, allow_nan=False)))

    def _get(self, db, task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r"[0-9a-f]{32}", task_id):
            raise PreviewError("INVALID_TASK_ID")
        row = db.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise PreviewError("TASK_NOT_FOUND")
        return json.loads(row[0])

    def _context(self, task=None):
        manifest = check_manifest(self.root)
        gates = read(self.root / "gates.json")
        if (set(gates) != {"mode", "paused_fields", "source_stopped"} or gates["mode"] != "public_preview"
                or type(gates["source_stopped"]) is not bool or gates["source_stopped"]
                or not isinstance(gates["paused_fields"], dict) or not set(PAUSES) <= gates["paused_fields"].keys()
                or any(not isinstance(v, str) or not v.strip() for v in gates["paused_fields"].values())):
            raise PreviewError("CURRENT_PAUSE_GATE_BLOCKED")
        binding = fingerprint({"manifest": manifest, "gates": gates})
        if task and task["binding"] != binding:
            raise PreviewError("SNAPSHOT_OR_PAUSE_CHANGED")
        return manifest, gates, binding

    def begin_run(self, repair_note=None):
        self._context()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            runs = db.execute("SELECT * FROM runs ORDER BY ordinal").fetchall()
            if db.execute("SELECT 1 FROM flags WHERE name='external_block' OR name='passed'").fetchone():
                raise PreviewError("CAMPAIGN_STOPPED")
            if len(runs) >= 3:
                raise PreviewError("NATIVE_RUN_BUDGET_EXHAUSTED")
            if runs and (runs[-1]["status"] != "failed" or not isinstance(repair_note, str) or not 8 <= len(repair_note) <= 300):
                raise PreviewError("DOCUMENTED_REPAIR_REQUIRED")
            if db.execute("SELECT 1 FROM tasks WHERE json_extract(payload,'$.status')='verifying'").fetchone():
                raise PreviewError("VERIFICATION_STILL_IN_PROGRESS")
            run_id = uuid.uuid4().hex
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (run_id, len(runs)+1, "running", repair_note, utcnow()))
            db.execute("INSERT OR REPLACE INTO flags VALUES('active_run',?)", (run_id,))
        return run_id

    def finish_run(self, run_id, passed):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE runs SET status=? WHERE id=?", ("passed" if passed else "failed", run_id))
            if passed:
                db.execute("INSERT OR REPLACE INTO flags VALUES('passed','true')")

    def open_query(self, case, question):
        if fingerprint(case) != fingerprint(CASE) or question != QUESTION:
            raise PreviewError("PUBLIC_FIXED_SCOPE_REQUIRED")
        manifest, gates, binding = self._context()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT value FROM flags WHERE name='active_run'").fetchone()
            run = active and db.execute("SELECT * FROM runs WHERE id=? AND status='running'", (active[0],)).fetchone()
            if not run:
                raise PreviewError("AUTHORIZED_ACTIVE_RUN_REQUIRED")
            existing = db.execute("SELECT payload FROM tasks WHERE run_id=?", (active[0],)).fetchone()
            if existing:
                task = json.loads(existing[0]); self._context(task)
            else:
                task = {"task_id": uuid.uuid4().hex, "run_id": active[0], "binding": binding,
                        "status": "opened", "data_class": "public", "facts": None, "verification": None, "answer": None}
                self._save(db, task)
        return {"task_id": task["task_id"], "status": task["status"], "mode": "public_preview", "snapshot_binding": binding}

    def retrieve(self, task_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._get(db, task_id); manifest, _, _ = self._context(task)
            expected = source_facts(manifest)
            if task["facts"] is not None and task["facts"] != expected:
                raise PreviewError("STORED_CITATION_CHANGED")
            if task["status"] == "opened":
                task.update(facts=expected, status="retrieved"); self._save(db, task)
            return {"task_id": task_id, "status": task["status"], "source_facts": task["facts"], "mode": "public_preview"}

    def verify(self, task_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._get(db, task_id); manifest, _, _ = self._context(task)
            if task["facts"] != source_facts(manifest):
                raise PreviewError("EXACT_RETRIEVAL_REQUIRED")
            if task["verification"] is not None:
                return {"task_id": task_id, "status": task["status"], "verification": task["verification"], "cached_for_same_task": True}
            if task["status"] != "retrieved" or self.verifier is None:
                raise PreviewError("LIVE_VERIFICATION_NOT_READY")
            active = db.execute("SELECT value FROM flags WHERE name='active_run'").fetchone()
            run = db.execute("SELECT status FROM runs WHERE id=?", (task["run_id"],)).fetchone()
            if not active or active[0] != task["run_id"] or not run or run[0] != "running":
                raise PreviewError("TASK_NOT_IN_ACTIVE_RUN")
            count = db.execute("SELECT value FROM counters WHERE name='jev_reserved'").fetchone()
            if (count and count[0] >= 3) or db.execute("SELECT 1 FROM flags WHERE name='external_block'").fetchone():
                raise PreviewError("JEV_CAMPAIGN_STOPPED")
            db.execute("INSERT OR REPLACE INTO counters VALUES('jev_reserved',?)", ((count[0] if count else 0)+1,))
            task["status"] = "verifying"; self._save(db, task)
        state = {"data_class": "public", "case": CASE, "claims": [
            {"id": c["id"], "value": c["value"], "statement": c["statement"], "source_text": c["citation"]["quote"]}
            for c in task["facts"]]}
        questions = {c["id"]: {"type": "noul", "instructions": "Does claim " + c["id"] + " accurately describe its own attached source_text? Judge source support only, not current legal applicability."} for c in task["facts"]}
        questions["invented_advance"] = {"type": "noul", "instructions": "Do these attached source texts explicitly establish that this adult can renew exactly 30 days in advance?"}
        try:
            response = validate_response(self.verifier(state, questions, self.root / (task_id + "-jev.json")), questions, "jev-1.13.0")
            if not isinstance(response.get("english_input"), dict):
                raise PreviewError("ENGLISH_INPUT_RECEIPT_REQUIRED")
            probabilities = {k: v["noul"] for k, v in response["answers"].items()}
            receipt_sha = source_digest((self.root / (task_id + "-jev.json")).read_bytes())
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                task = self._get(db, task_id); self._context(task)
                task.update(status="can_finalize", verification={"probabilities": probabilities, "threshold": .8,
                    "receipt_sha256": receipt_sha, "source_binding": task["binding"]})
                self._save(db, task)
            return {"task_id": task_id, "status": "can_finalize", "verification": task["verification"]}
        except Exception as error:
            code = str(error) if isinstance(error, (ClassificationError, PreviewError)) and re.fullmatch(r"[A-Z_]+", str(error)) else "VERIFICATION_UNAVAILABLE"
            with self.db() as db:
                db.execute("BEGIN IMMEDIATE")
                task = self._get(db, task_id); task.update(status="failed", error_code=code); self._save(db, task)
                # A local code repair can be retried, but an external access,
                # quota or transport blocker must not trigger more requests.
                external = {"CREDENTIAL_REQUIRED", "MISSING_API_KEY", "AUTHENTICATION_FAILED", "PAYMENT_REQUIRED",
                    "ACCESS_DENIED", "ACCESS_RESTRICTED", "RATE_LIMITED", "TRANSPORT_UNAVAILABLE",
                    "UPSTREAM_HTTP_ERROR", "UPSTREAM_UNAVAILABLE", "TRANSLATION_UNAVAILABLE"}
                if code in external:
                    db.execute("INSERT OR REPLACE INTO flags VALUES('external_block',?)", (code,))
            raise PreviewError(code) from None

    def state(self, task_id):
        with self.db() as db:
            task = self._get(db, task_id); self._context(task)
            return {"task_id": task_id, "status": task["status"], "mode": "public_preview"}

    def finalize(self, task_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._get(db, task_id); manifest, gates, _ = self._context(task)
            if task["status"] not in {"can_finalize", "complete"} or task["facts"] != source_facts(manifest):
                raise PreviewError("VERIFIED_TASK_REQUIRED")
            verification = task["verification"]
            path = self.root / (task_id + "-jev.json")
            if source_digest(path.read_bytes()) != verification["receipt_sha256"] or verification["source_binding"] != task["binding"]:
                raise PreviewError("MODEL_RECEIPT_CHANGED")
            response = read(path)["result"]
            expected = {k: v["noul"] for k, v in response["answers"].items()}
            if expected != verification["probabilities"] or verification["threshold"] != .8:
                raise PreviewError("STORED_VERDICT_CHANGED")
            facts = [{**f, "status": "source_supported" if expected[f["id"]] >= .8 else "needs_review",
                      "support": expected[f["id"]]} for f in task["facts"]]
            answer = {"task_id": task_id, "status": "partial_preview", "mode": "public_preview", "data_class": "public",
                "snapshot_binding": task["binding"], "source_facts": facts,
                "pending_business_conclusions": [{"field": k, "status": "paused", "reason": v} for k, v in gates["paused_fields"].items()],
                "unknown_fields": ["advance_days"], "invented_advance_rejected": expected["invented_advance"] <= .2,
                "portal_evidence": manifest["portal"]["status"], "published": False, "formal_approval": False,
                "notice": "真实公开资料的原文解释预览；待核事项不构成现行办理要求，未提交任何申请。"}
            if task["answer"] is not None and task["answer"] != answer:
                raise PreviewError("PERSISTED_ANSWER_CHANGED")
            task.update(status="complete", answer=answer); self._save(db, task)
            return answer

    def get_task(self, task_id):
        with self.db() as db:
            return self._get(db, task_id)

    def counts(self):
        with self.db() as db:
            return {"native_runs": db.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                "jev_reserved": (db.execute("SELECT value FROM counters WHERE name='jev_reserved'").fetchone() or [0])[0]}
