"""Persistent real-source engineering releases on the existing knowledge Store.

A preview release is never a human business approval. Actor identity is supplied
by a trusted host; public MCP/HTTP arguments must never choose their own role.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hmac
import json
from pathlib import Path
import secrets
import time
import uuid

from gov_mcp.knowledge.store import Store, KBError
from gov_mcp.matter_guidance import guidance, TOPICS
from gov_mcp.matter_graph import (
    canonical_hash, extract_candidates, file_hash, load_pack,
    preview_ontology, prove, SEED, scope_check,
)


class LifecycleError(ValueError):
    pass


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    kind: str = "service"

    def require(self, *roles):
        if not self.id or self.role not in roles or self.kind not in ("human", "service", "automated_test"):
            raise LifecycleError("权限不足或身份未绑定")


MARKER = {"schema": 1, "purpose": "nanjing_real_source_engineering_lifecycle"}
RELEASE_CLASS = "real_source_engineering_preview"


def source_identity(source):
    return canonical_hash({key: source.get(key) for key in
        ("source_id", "url", "region", "selector", "segments", "published_at",
         "effective_from", "effective_from_basis")})


class MatterLifecycle:
    def __init__(self, root, *, clock=time.time, max_age_seconds=86400):
        self.root = Path(root)
        self.clock, self.max_age_seconds = clock, max_age_seconds
        if type(max_age_seconds) is not int or not 60 <= max_age_seconds <= 86400:
            raise LifecycleError("来源新鲜度窗口必须为60至86400秒")
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / "matter-lifecycle.json"
        if marker.exists():
            if json.loads(marker.read_text(encoding="utf-8")) != MARKER:
                raise LifecycleError("运行目录用途不匹配")
        else:
            if any(self.root.iterdir()):
                raise LifecycleError("不允许在既有业务运行目录初始化")
            with marker.open("x", encoding="utf-8") as stream:
                json.dump(MARKER, stream)
        self.store = Store(self.root / "store", clock=clock)

    def head(self):
        return self.store.get("state", "active_rules")

    def _load(self, record):
        files = record["files"]
        def read(relative):
            if relative not in files:
                raise LifecycleError("版本没有登记该来源文件")
            return self.store.read_object(files[relative])
        pack = load_pack(self.root, read_file=read)
        if file_hash(read("rules-and-sources.json")) != record["package_sha256"]:
            raise LifecycleError("版本与规则包绑定不一致")
        return pack

    def _candidate(self, candidate_id, db=None):
        value = self.store.get("candidates", candidate_id, db)
        if not value or canonical_hash(value["identity"]) != candidate_id:
            raise LifecycleError("候选不存在或完整性失效")
        expected = canonical_hash({k: v for k, v in value.items() if k != "record_sha256"})
        if value.get("record_sha256") != expected:
            raise LifecycleError("候选记录已被修改")
        return value

    def _guard(self, record, source_ids, db=None):
        for source_id in source_ids:
            control = self.store.get("sources", source_id, db)
            if not control or not control["enabled"]:
                raise LifecycleError("SOURCE_PAUSED:" + source_id)
            if control["semantic_sha256"] != record["source_identities"][source_id]:
                raise LifecycleError("SOURCE_CHANGED:" + source_id)
            age = self.clock() - control["checked_at"]
            if age < -60 or age > self.max_age_seconds:
                raise LifecycleError("SOURCE_STALE:" + source_id)

    def prepare(self, package_root, *, ontology_stage, principal):
        principal.require("maintainer")
        package_root = Path(package_root).resolve()
        pack = load_pack(package_root)
        if pack["scope"]["region"] != "南京市" or pack["scope"]["matter"] != "resident-id-expiry-renewal":
            raise LifecycleError("候选不属于南京身份证期满换领")
        if ontology_stage not in ("seed", "evolved"):
            raise LifecycleError("只接受已实现的本体版本")
        ontology = deepcopy(SEED) if ontology_stage == "seed" else preview_ontology(extract_candidates(pack))
        sources = {s["source_id"]: source_identity(s) for s in pack["sources"]}
        identity = {"scope": pack["scope"], "sources": sources, "rules": pack["rules"],
                    "ontology": ontology}
        candidate_id = canonical_hash(identity)
        manifest = json.loads((package_root / "manifest.json").read_text(encoding="utf-8"))
        paths = [item["path"] for item in manifest["files"]] + ["manifest.json"]
        files = {path: self.store.object((package_root / path).read_bytes()) for path in paths}
        record = {"id": candidate_id, "identity": identity, "source_identities": sources,
            "files": files, "package_sha256": files["rules-and-sources.json"],
            "ontology": ontology, "ontology_stage": ontology_stage,
            "release_class": RELEASE_CLASS, "human_business_review": False,
            "knowledge_published": False}
        record["record_sha256"] = canonical_hash(record)
        with self.store.db(True) as db:
            previous = self.store.get("state", "matter_source_set", db) or []
            changed = []
            for source in pack["sources"]:
                checked = datetime.fromisoformat(source["checked_at"])
                if checked.tzinfo is None or checked.timestamp() > self.clock() + 60:
                    raise LifecycleError("来源检查时间无效")
                old = self.store.get("sources", source["source_id"], db)
                if old and old["semantic_sha256"] != sources[source["source_id"]]:
                    changed.append(source["source_id"])
                self.store.put("sources", source["source_id"], {
                    "semantic_sha256": sources[source["source_id"]],
                    "checked_at": max(checked.timestamp(), old["checked_at"]) if old and old["semantic_sha256"] == sources[source["source_id"]] else checked.timestamp(),
                    "enabled": old["enabled"] if old else True,
                    "reason": old.get("reason") if old else None}, db)
            for removed in set(previous) - set(sources):
                old = self.store.get("sources", removed, db)
                old.update(enabled=False, reason="来源不在最新候选范围")
                self.store.put("sources", removed, old, db)
                changed.append(removed)
            self.store.put("state", "matter_source_set", sorted(sources), db)
            existing = self.store.get("candidates", candidate_id, db)
            if not existing:
                self.store.put("candidates", candidate_id, record, db)
            self.store.audit("matter_prepare", {"candidate": candidate_id, "changed_sources": changed,
                "actor": principal.id, "actor_kind": principal.kind, "no_model_calls": True}, db)
        return {"status": "UNCHANGED" if existing else "CANDIDATE_READY", "candidate_id": candidate_id,
                "changed_sources": changed, "knowledge_published": False}

    def release_preview(self, candidate_id, expected_head, *, note, principal):
        principal.require("maintainer")
        if not isinstance(note, str) or not note.strip() or len(note) > 1000:
            raise LifecycleError("需要工程复核说明")
        with self.store.db(True) as db:
            record = self._candidate(candidate_id, db)
            self._load(record)
            self._guard(record, record["source_identities"], db)
            if self.store.get("state", "active_rules", db) != expected_head:
                raise KBError("BASE_VERSION_CHANGED")
            current = self.store.get("versions", expected_head, db) if expected_head else None
            if current and current["data"]["candidate"]["id"] == candidate_id:
                self.store.audit("matter_repeat_release_unchanged", {"candidate": candidate_id,
                    "actor": principal.id, "note": note}, db)
                return {"version": expected_head, "status": "UNCHANGED", "knowledge_published": False}
            data = {"release_class": RELEASE_CLASS, "candidate": record,
                    "technical_review": {"actor": principal.id, "actor_kind": principal.kind, "note": note},
                    "human_business_review": False, "knowledge_published": False}
            version = self.store.publish("rules", data, expected_head, principal.id, db=db)
        return {"version": version, "preview_release_active": True, "knowledge_published": False}

    def publish_business(self, *args, principal, **kwargs):
        principal.require("professional")
        raise LifecycleError("BUSINESS_PUBLICATION_PENDING:真实业务复核和独立评测尚未完成，不能将工程发布改为正式批准")

    def _version(self, version_id):
        value = self.store.version(version_id)
        if value["kind"] != "rules" or value["data"].get("release_class") != RELEASE_CLASS:
            raise LifecycleError("不是当前事项的已登记工程版本")
        return value["data"]["candidate"]

    def query(self, facts, *, principal, version_id=None, topic="old_card_lifecycle"):
        principal.require("citizen", "staff", "maintainer", "professional")
        selected = version_id or self.head()
        if not selected:
            return {"status": "NO_RELEASE", "knowledge_published": False}
        record = self._version(selected)
        pack = self._load(record)
        if topic not in TOPICS:
            raise LifecycleError("未知查询主题")
        gate = scope_check(pack, facts, require_old_card_valid=topic == "old_card_lifecycle")
        if gate["status"] != "IN_SCOPE":
            return {**gate, "version": selected, "knowledge_published": False}
        result = prove(pack, facts, ontology=record["ontology"]) if topic == "old_card_lifecycle" else guidance(pack, facts, topic, now=self.clock())
        sources = {e["source_id"] for row in result.get("source_relations", []) for e in row["evidence"]}
        sources.update(c["source_id"] for section in result.get("sections", []) for c in section["citations"])
        if not sources:
            sources = set(record["source_identities"])
        self._guard(record, sources)
        # Re-read mutable controls before returning a result; immutable version is pinned.
        with self.store.db() as db:
            self._guard(record, sources, db)
        return {**result, "version": selected, "release_class": RELEASE_CLASS,
                "source_package_sha256": record["package_sha256"]}

    def set_source_enabled(self, source_id, enabled, *, reason, principal):
        principal.require("maintainer")
        if type(enabled) is not bool or not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise LifecycleError("需要明确来源状态和维护说明")
        with self.store.db(True) as db:
            control = self.store.get("sources", source_id, db)
            if not control:
                raise LifecycleError("来源不存在")
            control.update(enabled=enabled, reason=reason)
            self.store.put("sources", source_id, control, db)
            self.store.audit("matter_source_control", {"source_id": source_id, "enabled": enabled,
                "actor": principal.id, "actor_kind": principal.kind, "reason": reason}, db)

    def rollback(self, target, expected_head, *, principal):
        principal.require("maintainer")
        self._load(self._version(target))
        self.store.rollback("rules", target, expected_head, principal.id)
        return {"version": target, "knowledge_published": False, "current_source_controls_still_apply": True}

    def open_case(self, facts, *, principal, topic="old_card_lifecycle"):
        principal.require("citizen", "staff")
        result = self.query(facts, principal=principal, topic=topic)
        if result["status"] not in ("DRAFT_PROOF_READY", "NO_SUPPORTED_MULTI_DOCUMENT_PROOF", "SOURCE_GUIDANCE_PENDING_REVIEW"):
            return result
        case_id = "case-" + uuid.uuid4().hex
        record = self._version(result["version"])
        with self.store.db(True) as db:
            self._guard(record, record["source_identities"], db)
            self.store.put("runs", case_id, {"id": case_id, "owner": principal.id, "facts": facts, "topic": topic,
                "version": result["version"], "state": "draft", "assignee": None, "handoff_hash": None}, db)
            self.store.audit("matter_case_opened", {"case_id": case_id, "version": result["version"],
                "actor": principal.id, "actor_kind": principal.kind}, db)
        return {"case_id": case_id, **result}

    def read_case(self, case_id, *, principal):
        principal.require("citizen", "staff")
        case = self.store.get("runs", case_id)
        if not case or principal.id not in (case["owner"], case["assignee"]):
            raise LifecycleError("无权读取该会话")
        if principal.id == case["assignee"] and principal.role != "staff":
            raise LifecycleError("交接接收身份已变化")
        result = self.query(case["facts"], principal=principal, version_id=case["version"], topic=case.get("topic", "old_card_lifecycle"))
        with self.store.db(True) as db:
            current = self.store.get("runs", case_id, db)
            if not current or principal.id not in (current["owner"], current["assignee"]):
                raise LifecycleError("会话读取权限已变化")
        return {"case_id": case_id, "state": case["state"], **result}

    def handoff(self, case_id, *, principal):
        principal.require("citizen")
        self.read_case(case_id, principal=principal)
        token = secrets.token_urlsafe(32)
        with self.store.db(True) as db:
            case = self.store.get("runs", case_id, db)
            if not case or case["owner"] != principal.id or case["assignee"]:
                raise LifecycleError("仅群众本人可交接尚未接收的会话")
            record = self._version(case["version"])
            self._guard(record, record["source_identities"], db)
            case.update(handoff_hash=file_hash(token.encode()), state="awaiting_staff")
            self.store.put("runs", case_id, case, db)
            self.store.audit("matter_handoff_created", {"case_id": case_id, "actor": principal.id}, db)
        return {"case_id": case_id, "handoff_token": token, "knowledge_published": False}

    def accept(self, case_id, token, *, principal):
        principal.require("staff")
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            raise LifecycleError("交接码无效")
        with self.store.db(True) as db:
            case = self.store.get("runs", case_id, db)
            if not case or case["state"] != "awaiting_staff" or not case["handoff_hash"] or not hmac.compare_digest(
                    case["handoff_hash"], file_hash(token.encode())):
                raise LifecycleError("交接码无效或已使用")
            record = self._version(case["version"])
            self._load(record)
            self._guard(record, record["source_identities"], db)
            case.update(state="staff_received", assignee=principal.id, handoff_hash=None)
            self.store.put("runs", case_id, case, db)
            self.store.audit("matter_handoff_received", {"case_id": case_id, "actor": principal.id}, db)
        return self.read_case(case_id, principal=principal)

    def confirm_handoff(self, case_id, *, acknowledge, note, principal):
        principal.require("staff")
        result = self.read_case(case_id, principal=principal)
        if acknowledge is not True or not isinstance(note, str) or not note.strip() or len(note) > 1000:
            raise LifecycleError("需要工作人员明确确认及说明")
        with self.store.db(True) as db:
            case = self.store.get("runs", case_id, db)
            if not case or case["assignee"] != principal.id or case["state"] != "staff_received":
                raise LifecycleError("不是本人接收的待确认交接")
            record = self._version(case["version"])
            self._guard(record, record["source_identities"], db)
            case.update(state="staff_confirmed", confirmation={"actor": principal.id,
                "actor_kind": principal.kind, "note": note, "at": self.clock(),
                "kind": "engineering_handoff_confirmation_not_business_approval"})
            self.store.put("runs", case_id, case, db)
            self.store.audit("matter_handoff_confirmed", {"case_id": case_id, "actor": principal.id,
                "actor_kind": principal.kind}, db)
        return {**result, "state": "staff_confirmed", "formal_business_approval": False}
