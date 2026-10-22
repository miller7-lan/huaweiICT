"""Bounded archive-successor inspection driven by the production Worker scheduler.

This adapter never publishes rules/documents, fetches a URL or invokes a model.
Its native driver supplies actual managed-agent extraction and review.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

from pydantic import Field
from gov_mcp.json_output import StrictOutput, validate_contract
from gov_mcp.knowledge.store import Store, KBError, digest, encoded
from gov_mcp.knowledge_fetch import parse_html

SERVICE = "govkb_successor_update"
SOURCE = "henan-duration-successor-archives"


class Duration(StrictOutput):
    value: int
    unit: str
    source_id: str
    quote: str


class Difference(StrictOutput):
    old: Duration
    new: Duration
    exception: Duration
    population: str
    interval: str
    exception_scope: str
    relationship: str
    effective_date: str | None
    can_subtract_days: bool
    whole_old_document_repealed: bool


class MachineReview(StrictOutput):
    acceptable: bool
    omissions: list[str] = Field(max_length=20)
    conflicts: list[str] = Field(max_length=20)
    notes: list[str] = Field(max_length=20)


def implementation():
    root = Path(__file__).parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("successor_update.py", "successor_update_server.py", "knowledge/worker.py", "knowledge/store.py")}


def evidence_packet(value):
    """Keep full policy clauses below the installed platform's 2000-character compaction limit."""
    packet = {"packet": encoded(value)}
    if len(json.dumps(packet, ensure_ascii=False, indent=2)) > 1800:
        raise KBError("COMPLETE_EVIDENCE_EXCEEDS_PLATFORM_BUDGET")
    return packet


def load_documents(root):
    root = Path(root)
    definition = json.loads((root / "pair-definition.json").read_text("utf-8"))
    documents = {}
    for spec in definition["sources"]:
        role = spec["role"]
        if role not in {"old", "new"}: raise KBError("EXACT_SUCCESSOR_PAIR_REQUIRED")
        raw = (root / "sources" / (role + ".html")).read_bytes()
        if hashlib.sha256(raw).hexdigest() != spec["original_sha256"] or len(raw) != spec["original_bytes"]:
            raise KBError("ORIGINAL_SOURCE_CHANGED")
        chunks, _ = parse_html(raw, {"class": "TRS_UEDITOR"} if role == "old" else {"id": "content"}, min_text_length=1)
        if (len(chunks) < 4 or chunks[0]["locator"] != spec["selected_locator"]
                or hashlib.sha256(chunks[0]["text"].encode()).hexdigest() != spec["selected_text_sha256"]
                or not chunks[3]["text"].startswith("二、")):
            raise KBError("COMPLETE_FIRST_ITEM_CONTEXT_REQUIRED")
        documents[role] = {**deepcopy(spec), "data_class": "public", "business_approval": False,
                           "current_applicability": "unknown", "effective_from": None,
                           "segments": [{"locator": c["locator"], "quote": c["text"],
                                         "text_sha256": hashlib.sha256(c["text"].encode()).hexdigest()} for c in chunks[:3]]}
    if set(documents) != {"old", "new"}: raise KBError("EXACT_SUCCESSOR_PAIR_REQUIRED")
    return documents


def validate_difference(value, documents):
    validate_contract(value, Difference)
    old, new = documents["old"], documents["new"]
    old_text, new_text = old["segments"][0]["quote"], new["segments"][0]["quote"]
    before = re.search(r"缩短至([0-9]+)个自然日", old_text)
    after = re.search(r"由([0-9]+)个自然日缩短至([0-9]+)个工作日", new_text)
    special = re.search(r"时限由([0-9]+)个自然日缩短至([0-9]+)个工作日", new_text)
    if not before or not after or not special: raise KBError("UNRECOGNIZED_DURATION_SOURCE")
    for key, doc, number, unit in (("old", old, int(before[1]), "calendar_day"),
                                   ("new", new, int(after[2]), "working_day"),
                                   ("exception", new, int(special[2]), "working_day")):
        item = value[key]
        if (item["value"] != number or item["unit"] != unit or item["source_id"] != doc["source_id"]
                or item["quote"] != doc["segments"][0]["quote"]):
            raise KBError("DURATION_OR_EXACT_FULL_CLAUSE_MISMATCH")
    if (int(before[1]) != int(after[1]) or value["population"] != "本省户籍居民"
            or value["interval"] != "从受理之日起到领证之日"
            or value["exception_scope"] not in {"中考、高考特殊时期急需用证考生通过“绿色通道”",
                                                "中考、高考特殊时期急需用证考生通过“绿色通道”申办"}
            or value["relationship"] != "cross_url_successor_candidate"
            or value["effective_date"] is not None or value["can_subtract_days"] is not False
            or value["whole_old_document_repealed"] is not False):
        raise KBError("SCOPE_DATE_UNIT_OR_SUCCESSION_LIMIT_VIOLATION")
    return deepcopy(value)


class SuccessorEngine:
    """Narrow Worker protocol adapter; source arrival is an explicitly labeled replay."""
    def __init__(self, root):
        self.root = Path(root)
        self.documents = load_documents(root)
        self.store = Store(self.root / "state")
        self.config = {"enabled": True, "heartbeat_seconds": 5, "lease_seconds": 120,
                       "run_budget_seconds": 180, "sources": [{"id": SOURCE, "enabled": True}]}
        self.provider = SimpleNamespace(name="native_deepseek_no_jev")
        self.processing_hash = digest(implementation())

    def snapshot(self):
        control = json.loads((self.root / "arrival.json").read_text("utf-8"))
        if set(control) != {"visible_roles", "enabled", "paused_reason"} or control["enabled"] is not True or control["paused_reason"] is not None:
            raise KBError("SOURCE_PAUSED_OR_DISABLED")
        roles = control["visible_roles"]
        if roles not in (["old"], ["old", "new"]): raise KBError("EXACT_OLD_THEN_SUCCESSOR_VISIBILITY_REQUIRED")
        docs = load_documents(self.root)
        visible = {role: docs[role] for role in roles}
        return {"documents": visible, "binding": digest(visible), "control": control}

    def poll(self, run, force=False):
        current = self.snapshot()
        previous = self.store.get("sources", SOURCE) or {}
        changed = previous.get("binding") != current["binding"]
        source = {"id": SOURCE, "source_config": self.config["sources"][0], "processing_hash": self.processing_hash,
                  "binding": current["binding"], "next_check": self.store.clock() + 60,
                  "last_check": self.store.clock(), "status": "changed" if changed else "unchanged",
                  "job_ids": previous.get("job_ids", [])}
        if changed and set(current["documents"]) == {"old", "new"}:
            asset = {"source_url": "archive-collection:two-distinct-official-urls", "segments": [
                {"segment_id": role + "-" + s["locator"], "content": s["quote"]}
                for role, doc in current["documents"].items() for s in doc["segments"]]}
            job, created = self.store.enqueue(run, SOURCE, [asset], digest({"source": current["binding"], "code": self.processing_hash}))
            source["job_ids"] = [job["id"]]
            self.store.audit("successor_discovered", {"job_id": job["id"], "binding": current["binding"], "created": created})
        self.store.put("sources", SOURCE, source)
        self.store.audit("archive_collection_checked", {"roles": list(current["documents"]), "binding": current["binding"], "changed": changed})

    def select_jobs(self, run):
        source = self.store.get("sources", SOURCE)
        selected = []
        for key in source.get("job_ids", []):
            job = self.store.get("jobs", key)
            if job["status"] == "processed": continue
            job.update(run_id=run["id"], fence=run["fence"], status="working", attempts=job["attempts"] + 1)
            self.store.update_job(run, job)
            selected.append(key)
        run["jobs"] = selected
        self.store.put("runs", run["id"], run)
        return selected

    def needs_publication(self): return False

    def publish_documents(self, run):
        self.store.audit("publication_withheld", {"run_id": run["id"], "business_rules_published": False})

    def context(self, run_id):
        run = self.store.get("runs", run_id)
        if not run or len(run.get("jobs", [])) != 1: raise KBError("ONE_LEASED_UPDATE_JOB_REQUIRED")
        with self.store.db() as db: self.store.assert_lease(run, db)
        if (self.store.get("state", "native_campaign") or {}).get("run_id") != run_id:
            raise KBError("AUTHORIZED_NATIVE_RUN_REQUIRED")
        job = self.store.get("jobs", run["jobs"][0])
        current = self.snapshot()
        source = self.store.get("sources", SOURCE)
        campaign = self.store.get("state", "native_campaign")
        if (current["binding"] != source["binding"] or campaign["binding"] != current["binding"]
                or campaign["implementation"] != implementation() or job.get("run_id") != run_id
                or job.get("fence") != run["fence"] or job["id"] not in source["job_ids"]):
            raise KBError("SOURCE_CODE_OR_RUN_BINDING_CHANGED")
        return run, job, current

    def get_batch(self, run_id, view=None):
        run, job, current = self.context(run_id)
        if view not in {"old", "new"}: raise KBError("EXPLICIT_DOCUMENT_VIEW_REQUIRED")
        if job["stage"] not in {"discovered", "extracted"}: raise KBError("EVIDENCE_READ_STAGE_REQUIRED")
        key = "extract_reads" if job["stage"] == "discovered" else "review_reads"
        seen = job.get(key, [])
        if view in seen: raise KBError("REPEATED_EVIDENCE_READ_BLOCKED")
        if view != ("old" if not seen else "new"): raise KBError("OLD_THEN_NEW_EVIDENCE_REQUIRED")
        doc = current["documents"][view]
        value = {"run_id": run_id, "batch_id": job["id"], "stage": job["stage"], "view": view,
                 "document": {k: deepcopy(doc[k]) for k in ("source_id", "url", "revision", "original_sha256", "page_publication_date", "effective_from")},
                 "source_binding": current["binding"], "formal_approval": False}
        value["document"]["segments"] = [{"locator": s["locator"], "quote": s["quote"]} for s in doc["segments"]]
        if job["stage"] == "extracted" and view == "old":
            candidate = self.store.get("candidates", job["candidate_id"])
            # All original clauses still follow in the separate document views.
            # Avoid repeating the same source quote three times in the review payload.
            value["candidate_fields"] = deepcopy(candidate["difference"])
            for name in ("old", "new", "exception"):
                value["candidate_fields"][name].pop("quote")
            value["exact_complete_clause_quotes_checked"] = True
        packet = evidence_packet(value)  # Refuse oversize context; never silently drop a condition.
        job[key] = seen + [view]
        with self.store.db(True) as db:
            self.store.assert_lease(run, db)
            latest = self.store.get("jobs", job["id"], db)
            if latest.get(key, []) != seen or latest["stage"] != job["stage"]:
                raise KBError("JOB_CHANGED_DURING_EVIDENCE_READ")
            db.execute("UPDATE jobs SET payload=? WHERE id=?", (encoded(job), job["id"]))
        return packet

    def submit(self, run_id, difference):
        run, job, current = self.context(run_id)
        if job["stage"] != "discovered": raise KBError("EXTRACTION_STAGE_REQUIRED")
        if job.get("extract_reads") != ["old", "new"]: raise KBError("BOTH_FULL_DOCUMENT_VIEWS_REQUIRED")
        value = validate_difference(difference, current["documents"])
        candidate = {"id": digest({"binding": current["binding"], "difference": value}),
                     "difference": value, "status": "pending_review", "formal_approval": False,
                     "business_conclusion_status": "PAUSED_UNREVIEWED", "formally_published": False,
                     "source_binding": current["binding"], "machine_review": None}
        with self.store.db(True) as db:
            self.store.assert_lease(run, db)
            self.store.put("candidates", candidate["id"], candidate, db)
            job.update(candidate_id=candidate["id"], stage="extracted")
            db.execute("UPDATE jobs SET payload=? WHERE id=?", (encoded(job), job["id"]))
        return {"candidate_id": candidate["id"], "status": "pending_review", "published": False}

    def review(self, run_id, review):
        run, job, _ = self.context(run_id)
        if job["stage"] != "extracted": raise KBError("REVIEW_STAGE_REQUIRED")
        if job.get("review_reads") != ["old", "new"]: raise KBError("BOTH_FULL_DOCUMENT_VIEWS_REQUIRED")
        validate_contract(review, MachineReview)
        candidate = self.store.get("candidates", job["candidate_id"])
        validate_difference(candidate["difference"], self.documents)
        candidate["machine_review"] = deepcopy(review)
        self.store.put("candidates", candidate["id"], candidate)
        if review["acceptable"] is not True or review["omissions"] or review["conflicts"]:
            raise KBError("MACHINE_REVIEW_REQUIRES_ATTENTION")
        job["stage"] = "validated"
        self.store.update_job(run, job)
        return {"candidate_id": candidate["id"], "stage": "validated", "requires_human_review": True}

    def state(self, run_id):
        _, job, _ = self.context(run_id)
        return {"stage": job["stage"], "can_finish": job["stage"] == "validated", "published": False}

    def finish(self, run_id):
        run, job, current = self.context(run_id)
        if job["stage"] != "validated": raise KBError("VALIDATED_CANDIDATE_REQUIRED")
        candidate = self.store.get("candidates", job["candidate_id"])
        validate_difference(candidate["difference"], current["documents"])
        receipt = {"run_id": run_id, "batch_id": job["id"], "candidate": candidate,
                   "source_binding": current["binding"], "formal_approval": False,
                   "status": "candidate_awaiting_human_review", "published": False,
                   "actual_effective_date": None, "business_conclusion_status": "PAUSED_UNREVIEWED"}
        self.context(run_id)
        self.store.put("state", "final_receipt", receipt)
        job.update(stage="finished", status="processed")
        self.store.update_job(run, job)
        return receipt
