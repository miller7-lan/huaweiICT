"""Small, classified, version-pinned M2 preview workflow; never a formal approval."""

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.core import result


NOTICE = "工程演示：资料及材料规则待业务复核；模拟材料声明不代表真实材料或正式审批。"
MATTER = "demo-id-renewal"
CATEGORY_LABELS = {"identity": "身份材料", "household": "户籍材料", "eligibility": "适用条件", "procedure": "办理流程"}


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class WorkflowBlocked(Exception):
    def __init__(self, code, **data):
        self.code, self.data = code, data


class M2Store:
    def __init__(self, runtime: Path, timeout=2):
        self.runtime = Path(runtime)
        marker = json.loads((self.runtime / "phase.json").read_text(encoding="utf-8"))
        if marker.get("phase") != "M2" or marker.get("mode") != "engineering_demo":
            raise ValueError("An explicitly prepared M2 demo runtime is required")
        self.path = self.runtime / "m2.sqlite3"
        self.timeout = timeout
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sources(
                    source_id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
                    region TEXT NOT NULL, matter_id TEXT NOT NULL, document_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL, review_status TEXT NOT NULL, tags_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(
                    version_id TEXT PRIMARY KEY, state TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chunks(
                    chunk_id TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES versions(version_id),
                    source_id TEXT NOT NULL REFERENCES sources(source_id), locator TEXT NOT NULL,
                    category TEXT NOT NULL, region TEXT NOT NULL, matter_id TEXT NOT NULL,
                    text TEXT NOT NULL, PRIMARY KEY(chunk_id,version_id));
                CREATE INDEX IF NOT EXISTS classification_idx ON chunks(region,matter_id,category,version_id);
                CREATE TABLE IF NOT EXISTS tasks(
                    task_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                    matter_id TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES versions(version_id),
                    state TEXT NOT NULL, question TEXT NOT NULL, materials_json TEXT NOT NULL,
                    created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS task_owner_idx ON tasks(tenant_id,owner_id,created_at);
                CREATE TABLE IF NOT EXISTS handoffs(
                    handoff_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(task_id),
                    tenant_id TEXT NOT NULL, owner_id TEXT NOT NULL, version_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(tenant_id,owner_id,idempotency_key));
                CREATE TABLE IF NOT EXISTS reviews(
                    handoff_id TEXT PRIMARY KEY REFERENCES handoffs(handoff_id),
                    reviewer_id TEXT NOT NULL, version_id TEXT NOT NULL, assessment_sha256 TEXT NOT NULL,
                    confirmation_kind TEXT NOT NULL, created_at TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=self.timeout)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def seed_preview(self, sources, chunks):
        """Import only a fingerprinted local preview; business review remains pending."""
        manifest = {"sources": sources, "chunks": chunks}
        version = "preview-" + digest(manifest)[:16]
        with self.connect(write=True) as db:
            for source in sources:
                if source["review_status"] != "pending_business_review" or not source["url"].startswith("https://"):
                    raise ValueError("Invalid preview source")
                previous = db.execute("SELECT sha256 FROM sources WHERE source_id=?", (source["source_id"],)).fetchone()
                if previous and previous["sha256"] != source["sha256"]:
                    raise ValueError("Source revision needs a new source identifier")
                db.execute("INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?,?,?)", (
                    source["source_id"], source["title"], source["url"], source["region"], MATTER,
                    source["document_type"], source["sha256"], source["review_status"], canonical(source["tags"])))
            db.execute("INSERT OR IGNORE INTO versions VALUES(?,?,?,?)", (version, "demo_preview", digest(manifest), now()))
            for chunk in chunks:
                if chunk["category"] not in CATEGORY_LABELS or not chunk["text"].strip():
                    raise ValueError("Invalid classified chunk")
                db.execute("INSERT OR IGNORE INTO chunks VALUES(?,?,?,?,?,?,?,?)", (
                    chunk["chunk_id"], version, chunk["source_id"], chunk["locator"], chunk["category"],
                    chunk["region"], MATTER, chunk["text"]))
        return version

    def counts(self):
        with self.connect() as db:
            return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("sources", "versions", "chunks", "tasks", "handoffs", "reviews")}


class M2Tools:
    def __init__(self, runtime: Path):
        self.runtime = Path(runtime)
        self.store = M2Store(runtime)
        self.lock = threading.Lock()

    def principal(self, tenant, user):
        mapping = json.loads((self.runtime / "role-map.json").read_text(encoding="utf-8"))
        principal = mapping.get(tenant, {}).get(user)
        if not isinstance(principal, dict) or principal.get("role") not in ("citizen", "staff"):
            raise WorkflowBlocked("ROLE_NOT_CONFIGURED")
        return principal["role"]

    def execute(self, name, tenant_id, user_id, **args):
        start = time.perf_counter()
        role = None
        try:
            role = self.principal(tenant_id, user_id)
            permissions = {
                "get_environment_status": {"citizen", "staff"}, "create_task": {"citizen"},
                "search_evidence": {"citizen", "staff"}, "declare_materials": {"citizen"},
                "create_handoff": {"citizen"}, "list_handoffs": {"staff"},
                "check_case_prerequisites": {"staff"}, "confirm_review": {"staff"}}
            if name not in permissions:
                raise WorkflowBlocked("UNKNOWN_TOOL")
            if role not in permissions[name]:
                raise WorkflowBlocked("FORBIDDEN")
            with self.store.connect(write=name in {"create_task", "declare_materials", "create_handoff", "confirm_review"}) as db:
                value = self.dispatch(db, name, tenant_id, user_id, role, args)
        except WorkflowBlocked as error:
            value = result("BLOCKED", error.code, **error.data)
        except sqlite3.OperationalError as error:
            code = "TOOL_TIMEOUT" if getattr(error, "sqlite_errorcode", 0) & 0xff in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else "STORAGE_UNAVAILABLE"
            value = result("ERROR", code, attempts=1)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            value = result("ERROR", "CONFIG_OR_STORAGE_UNAVAILABLE")
        value["data"].update(request_id=secrets.token_hex(8), role=role, demo_mode=True,
                             notice=NOTICE, formal_approval=False)
        entry = {"time": now(), "tool": "govm2_" + name, "request_id": value["data"]["request_id"],
                 "principal_sha256": hashlib.sha256(f"{tenant_id}\0{user_id}".encode()).hexdigest(),
                 "role": role, "duration_ms": round((time.perf_counter()-start)*1000, 2), "result": value}
        with self.lock, (self.runtime / "tool-audit.jsonl").open("a", encoding="utf-8") as log:
            log.write(canonical(entry) + "\n")
        return value

    def task(self, db, tenant, user, role, task_id=None):
        if task_id:
            row = db.execute("SELECT * FROM tasks WHERE task_id=? AND tenant_id=?", (task_id, tenant)).fetchone()
        else:
            row = db.execute("SELECT * FROM tasks WHERE tenant_id=? AND owner_id=? ORDER BY rowid DESC LIMIT 1", (tenant, user)).fetchone()
        if not row or (role == "citizen" and row["owner_id"] != user):
            raise WorkflowBlocked("TASK_NOT_FOUND")
        return row

    def handoff(self, db, tenant, handoff_id=None):
        if handoff_id:
            row = db.execute("SELECT * FROM handoffs WHERE handoff_id=? AND tenant_id=?", (handoff_id, tenant)).fetchone()
        else:
            row = db.execute("SELECT * FROM handoffs WHERE tenant_id=? ORDER BY rowid DESC LIMIT 1", (tenant,)).fetchone()
        if not row:
            raise WorkflowBlocked("HANDOFF_NOT_FOUND")
        return row

    def citation(self, row):
        return {"citation_id": row["chunk_id"], "source_id": row["source_id"], "title": row["title"],
                "url": row["url"], "locator": row["locator"], "quote": row["text"],
                "knowledge_version": row["version_id"], "category": row["category"], "region": row["region"],
                "review_status": row["review_status"]}

    def evidence(self, db, task, query):
        rows = db.execute("""SELECT c.*,s.title,s.url,s.review_status FROM chunks c JOIN sources s USING(source_id)
            WHERE c.version_id=? AND c.matter_id=? AND c.region IN ('national','郑州市二七区')""",
            (task["version_id"], task["matter_id"])).fetchall()
        normalized = re.sub(r"\s+", "", query)
        grams = {normalized[i:i+2] for i in range(max(len(normalized)-1, 0))}
        ranked = [(sum(g in r["text"] for g in grams), r) for r in rows]
        return [self.citation(r) for score, r in sorted(ranked, key=lambda x: (-x[0], x[1]["chunk_id"])) if score > 0][:4]

    def assessment(self, db, row):
        snapshot = json.loads(row["snapshot_json"])
        declarations = snapshot["materials"]
        # This is a draft presence checklist from the cited local guide. The
        # household-register necessity is explicitly unresolved, never a verdict.
        missing = ["原居民身份证"] if declarations["identity_card"] == "missing" else []
        unknown = ["原居民身份证"] if declarations["identity_card"] == "unknown" else []
        pending = ["居民户口簿在本地成年人到期换领中的具体要求", "原件、共享核实和当前政策版本"]
        value = result("OK", "PREVIEW_ASSESSMENT_COMPLETE", handoff_id=row["handoff_id"],
                       task_id=row["task_id"], state=row["state"], materials=declarations,
                       missing_draft_items=missing, unknown_items=unknown, source_review_pending=pending,
                       ready_for_formal_processing=False, input_origin="simulated_material_presence")
        value["knowledge_version"] = row["version_id"]
        value["citations"] = snapshot["citations"]
        value["data"]["assessment_sha256"] = digest({"handoff": row["handoff_id"], "version": row["version_id"],
            "materials": declarations, "missing": missing, "unknown": unknown, "pending": pending})
        return value

    def dispatch(self, db, name, tenant, user, role, args):
        if name == "get_environment_status":
            version = db.execute("SELECT version_id FROM versions ORDER BY rowid DESC LIMIT 1").fetchone()
            if not version:
                raise WorkflowBlocked("NO_PREVIEW_KNOWLEDGE")
            value = result("OK", "M2_PREVIEW_READY", matter_id=MATTER,
                matter_name="郑州市二七区成年人本地户籍身份证到期换领（候选范围）",
                source_review_status="pending_business_review", classifications=CATEGORY_LABELS)
            value["knowledge_version"] = version["version_id"]
            return value
        if name == "create_task":
            question = args.get("question", "")
            if not isinstance(question, str) or not question.strip() or len(question) > 300:
                raise WorkflowBlocked("INVALID_QUESTION")
            if re.search(r"\d{17}[\dXx]|[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", question):
                raise WorkflowBlocked("PERSONAL_DATA_NOT_ALLOWED")
            version = db.execute("SELECT version_id FROM versions ORDER BY rowid DESC LIMIT 1").fetchone()
            if not version:
                raise WorkflowBlocked("NO_PREVIEW_KNOWLEDGE")
            task_id = "task-" + secrets.token_hex(6)
            materials = {"identity_card": "unknown", "household_register": "unknown"}
            db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)", (task_id, tenant, user, MATTER,
                version["version_id"], "draft", question.strip(), canonical(materials), now()))
            value = result("OK", "DEMO_TASK_CREATED", task_id=task_id, state="draft", materials=materials)
            value["knowledge_version"] = version["version_id"]
            return value
        if name == "list_handoffs":
            rows = db.execute("SELECT handoff_id,task_id,version_id,state,created_at FROM handoffs WHERE tenant_id=? ORDER BY rowid DESC", (tenant,)).fetchall()
            value = result("OK", "HANDOFF_QUEUE", pending_count=sum(r["state"] != "demo_reviewed" for r in rows))
            value["items"] = [dict(r) for r in rows]
            return value
        if name in ("check_case_prerequisites", "confirm_review"):
            row = self.handoff(db, tenant, args.get("handoff_id"))
            value = self.assessment(db, row)
            if name == "confirm_review":
                if args.get("confirmed") is not True or args.get("assessment_sha256") != value["data"]["assessment_sha256"]:
                    raise WorkflowBlocked("CONFIRMATION_OR_ASSESSMENT_MISMATCH")
                previous = db.execute("SELECT * FROM reviews WHERE handoff_id=?", (row["handoff_id"],)).fetchone()
                if previous and previous["assessment_sha256"] != value["data"]["assessment_sha256"]:
                    raise WorkflowBlocked("REVIEW_CONFLICT")
                db.execute("INSERT OR IGNORE INTO reviews VALUES(?,?,?,?,?,?)", (row["handoff_id"], user,
                    row["version_id"], value["data"]["assessment_sha256"], "demo_ui_confirmation", now()))
                db.execute("UPDATE handoffs SET state='demo_reviewed' WHERE handoff_id=?", (row["handoff_id"],))
                db.execute("UPDATE tasks SET state='demo_reviewed' WHERE task_id=?", (row["task_id"],))
                value["message_code"] = "DEMO_REVIEW_RECORDED"
                value["data"].update(state="demo_reviewed", confirmation_kind="demo_ui_confirmation", business_rule_review_completed=False)
            return value
        task = self.task(db, tenant, user, role, args.get("task_id"))
        if name == "search_evidence":
            query = args.get("query") or task["question"]
            if not isinstance(query, str) or len(query) > 300:
                raise WorkflowBlocked("INVALID_QUESTION")
            citations = self.evidence(db, task, query)
            value = result("OK" if citations else "EMPTY", "PREVIEW_EVIDENCE_FOUND" if citations else "NO_SUPPORTING_EVIDENCE",
                task_id=task["task_id"], state=task["state"], question=task["question"],
                materials=json.loads(task["materials_json"]), source_review_status="pending_business_review")
            value["knowledge_version"] = task["version_id"]
            value["citations"] = citations
            value["items"] = [{"text": c["quote"], "citation_id": c["citation_id"], "category": c["category"]} for c in citations]
            return value
        if task["state"] != "draft":
            if name == "create_handoff":
                existing = db.execute("SELECT * FROM handoffs WHERE task_id=?", (task["task_id"],)).fetchone()
                if existing and existing["idempotency_key"] == args.get("idempotency_key"):
                    value = result("OK", "HANDOFF_REUSED", task_id=task["task_id"], handoff_id=existing["handoff_id"], state=existing["state"])
                    value["knowledge_version"] = existing["version_id"]
                    return value
            raise WorkflowBlocked("TASK_ALREADY_HANDED_OFF")
        if name == "declare_materials":
            materials = args.get("materials")
            if not isinstance(materials, dict) or set(materials) != {"identity_card", "household_register"} or any(v not in ("present", "missing", "unknown") for v in materials.values()):
                raise WorkflowBlocked("INVALID_MATERIAL_DECLARATION")
            db.execute("UPDATE tasks SET materials_json=? WHERE task_id=?", (canonical(materials), task["task_id"]))
            value = result("OK", "SIMULATED_MATERIALS_SAVED", task_id=task["task_id"], materials=materials)
            value["knowledge_version"] = task["version_id"]
            return value
        key = args.get("idempotency_key")
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", key):
            raise WorkflowBlocked("INVALID_IDEMPOTENCY_KEY")
        citations = self.evidence(db, task, task["question"])
        if not citations:
            raise WorkflowBlocked("NO_SUPPORTING_EVIDENCE")
        snapshot = {"task_id": task["task_id"], "version_id": task["version_id"], "question": task["question"],
                    "materials": json.loads(task["materials_json"]), "citations": citations, "input_origin": "simulated_material_presence"}
        existing = db.execute("SELECT * FROM handoffs WHERE tenant_id=? AND owner_id=? AND idempotency_key=?", (tenant, user, key)).fetchone()
        if existing:
            raise WorkflowBlocked("IDEMPOTENCY_CONFLICT")
        handoff_id = "handoff-" + secrets.token_hex(6)
        db.execute("INSERT INTO handoffs VALUES(?,?,?,?,?,?,?,?,?,?)", (handoff_id, task["task_id"], tenant, user,
            task["version_id"], key, digest(snapshot), canonical(snapshot), "awaiting_demo_review", now()))
        db.execute("UPDATE tasks SET state='handed_off' WHERE task_id=?", (task["task_id"],))
        value = result("OK", "HANDOFF_CREATED", task_id=task["task_id"], handoff_id=handoff_id, state="awaiting_demo_review")
        value["knowledge_version"] = task["version_id"]
        value["citations"] = citations
        return value
