"""Observed whole-document changes feed unpublished matter lifecycle candidates."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import time

from gov_mcp.knowledge.store import Store, KBError
from gov_mcp.knowledge_fetch import parse_html, observe_html
from gov_mcp.matter_graph import load_pack, canonical_hash, file_hash, safe_child


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def page_data(raw, selector):
    rows, _ = parse_html(raw, selector, min_text_length=1)
    return rows, observe_html(raw, content_selector=selector)


def build_snapshot(template_root, destination, source_ids, *, capture_root=None):
    """Expand all body paragraphs, retain exact citations, never invent new legal rules."""
    template_root, destination = Path(template_root), Path(destination)
    template = load_pack(template_root)
    requested = set(source_ids)
    if not requested or len(requested) != len(source_ids) or requested - {s["source_id"] for s in template["sources"]}:
        raise ValueError("来源范围无效")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("不覆盖既有更新快照")
    capture = json.loads((Path(capture_root) / "snapshot.json").read_text(encoding="utf-8")) if capture_root else None
    captured = {s["source_id"]: s for s in capture["sources"]} if capture else {}
    pack = deepcopy(template)
    pack["sources"], files = [], {}
    for source in template["sources"]:
        if source["source_id"] not in requested: continue
        source = deepcopy(source)
        if capture:
            record = captured[source["source_id"]]
            if record.get("status") != "captured_pending_review" or record["url"] != source["url"]:
                raise ValueError("未成功取得固定来源：" + source["source_id"])
            raw = safe_child(Path(capture_root).resolve(), record["filename"]).read_bytes()
            if file_hash(raw) != record["original_sha256"] or len(raw) != record["bytes"]:
                raise ValueError("采集原件指纹不一致")
            source["checked_at"] = record["checked_at"]
        else:
            raw = safe_child(template_root.resolve(), source["original_file"]).read_bytes()
        rows, observed = page_data(raw, source["selector"])
        source.update(original_sha256=file_hash(raw), parsed_paragraphs=len(rows),
            segments=[{"segment_id": source["source_id"] + "-P" + str(i), **row,
                "text_sha256": file_hash(row["text"].encode())} for i, row in enumerate(rows, 1)],
            page_observation=observed)
        pack["sources"].append(source)
        files[source["original_file"]] = raw
    pack["rules"] = [r for r in pack["rules"] if r["source_id"] in requested]
    wanted = {c for r in pack["rules"] for c in r["citation_ids"]}
    pack["citations"] = [c for c in pack["citations"] if c["id"] in wanted]
    # A source change can invalidate old rule anchors. Fail rather than move or repair quotes.
    segments = {s["segment_id"]: s for source in pack["sources"] for s in source["segments"]}
    for citation in pack["citations"]:
        row = segments.get(citation["segment_id"])
        if not row or row["text_sha256"] != citation["context_sha256"] or row["text"][citation["start"]:citation["end"]] != citation["quote"]:
            raise ValueError("正文变化导致原引用失效，需重新复核：" + citation["id"])
    if "LAW-ID-BJ-TRAFFIC-2011" in requested and pack.get("law_crosscheck"):
        for path in (pack["law_crosscheck"]["original_file"], pack["law_crosscheck"]["visual_check"]):
            files[path] = safe_child(template_root.resolve(), path).read_bytes()
    else:
        pack.pop("law_crosscheck", None)
    if capture:
        receipt = {**capture, "sources": [s for s in capture["sources"] if s["source_id"] in requested]}
        files["capture-receipt.json"] = encoded(receipt)
        pack["observation"] = {"kind": "live_fixed_official_capture", "receipt_file": "capture-receipt.json",
            "receipt_sha256": file_hash(files["capture-receipt.json"])}
    else:
        pack["observation"] = {"kind": "archived_originals_controlled_baseline"}
    files["rules-and-sources.json"] = encoded(pack)
    files["README.md"] = ("# 南京事项全正文更新快照\n\n真实归档原件；全部正文段落纳入变化判断。规则、人工作用和正式发布仍待复核。"
        "\n来源子集为明确登记范围的工程试验，不代表政府网站历史版本或实际政策变更。\n").encode()
    manifest = {"schema": 1, "rule_count": len(pack["rules"]), "source_count": len(pack["sources"]),
        "formal_business_review": False, "knowledge_published": False,
        "rules_sha256": file_hash(files["rules-and-sources.json"]),
        "files": [{"path": p, "bytes": len(raw), "sha256": file_hash(raw)} for p, raw in sorted(files.items())]}
    files["manifest.json"] = encoded({**manifest, "file_count": len(files)})
    for relative, raw in files.items():
        target = (destination / relative).resolve()
        if not target.is_relative_to(destination.resolve()): raise ValueError("更新快照输出路径越界")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream: stream.write(raw)
    load_pack(destination)
    return pack


def validate_snapshot(root, *, now, max_age_seconds, initial=False):
    root = Path(root).resolve(); pack = load_pack(root)
    if pack["human_review"]["status"] != "pending" or pack["knowledge_published"] is not False:
        raise ValueError("更新入口不能写入人工批准或正式发布")
    observation = pack.get("observation", {})
    if observation.get("kind") == "archived_originals_controlled_baseline":
        if not initial: raise ValueError("归档基线不能冒充新的来源复查")
        records = None
    elif observation.get("kind") == "live_fixed_official_capture":
        raw = safe_child(root, observation["receipt_file"]).read_bytes()
        if file_hash(raw) != observation["receipt_sha256"]: raise ValueError("采集回执指纹变化")
        rows = json.loads(raw)["sources"]
        records = {r["source_id"]: r for r in rows}
        if len(records) != len(rows) or set(records) != {s["source_id"] for s in pack["sources"]}:
            raise ValueError("采集回执来源范围不匹配")
    else: raise ValueError("需要固定来源的实际采集回执")
    for source in pack["sources"]:
        raw = safe_child(root, source["original_file"]).read_bytes()
        rows, observed = page_data(raw, source["selector"])
        expected = [{"segment_id": source["source_id"] + "-P" + str(i), **row,
            "text_sha256": file_hash(row["text"].encode())} for i, row in enumerate(rows, 1)]
        if source["segments"] != expected or source.get("page_observation") != observed:
            raise ValueError("未完整绑定全部正文及页面元数据")
        checked = datetime.fromisoformat(source["checked_at"])
        if checked.tzinfo is None or not -60 <= now - checked.timestamp() <= max_age_seconds:
            raise ValueError("来源实际检查时间无效或过期")
        if records is not None:
            record = records[source["source_id"]]
            requests = [r for r in record["requests"] if r["url"] == source["url"] and r.get("http_status") == 200]
            if (record.get("status") != "captured_pending_review" or record["url"] != source["url"]
                or record["checked_at"] != source["checked_at"] or record["original_sha256"] != file_hash(raw)
                or record["bytes"] != len(raw) or len(requests) != 1 or requests[0]["sha256"] != file_hash(raw)
                or requests[0]["bytes"] != len(raw)):
                raise ValueError("检查时间、HTTP200与实际原件未绑定")
    return pack


def change_set(before, after):
    old = {s["source_id"]: s for s in before["sources"]} if before else {}
    new = {s["source_id"]: s for s in after["sources"]}
    changes = []
    for source_id in sorted(set(old) | set(new)):
        a, b = old.get(source_id), new.get(source_id)
        if a is None: kind = "source_added"
        elif b is None: kind = "source_removed"
        elif any(a.get(k) != b.get(k) for k in ("url", "selector", "published_at", "effective_from", "effective_from_basis", "region")):
            kind = "source_definition_changed"
        elif canonical_hash(a["segments"]) != canonical_hash(b["segments"]): kind = "body_changed"
        elif a.get("page_observation") != b.get("page_observation"): kind = "page_metadata_changed"
        elif a["original_sha256"] != b["original_sha256"]: kind = "raw_only_changed"
        elif a["checked_at"] != b["checked_at"]: kind = "checked_body_unchanged"
        else: kind = "same_observation"
        changes.append({"source_id": source_id, "kind": kind,
            "old_original_sha256": a["original_sha256"] if a else None,
            "new_original_sha256": b["original_sha256"] if b else None})
    old_rules = {r["id"]: r for r in before["rules"]} if before else {}
    new_rules = {r["id"]: r for r in after["rules"]}
    return {"sources": changes, "added_rules": sorted(set(new_rules) - set(old_rules)),
        "removed_rules": sorted(set(old_rules) - set(new_rules)),
        "changed_rules": sorted(k for k in set(old_rules) & set(new_rules) if old_rules[k] != new_rules[k])}


def inspect_capture(template_root, capture_root, source_ids):
    """Check live originals against the reviewed-development template before reusing any rule."""
    template = load_pack(template_root)
    captured = json.loads((Path(capture_root) / "snapshot.json").read_text(encoding="utf-8"))
    records = {r["source_id"]: r for r in captured["sources"]}
    result = []
    for source in template["sources"]:
        if source["source_id"] not in source_ids: continue
        record = records.get(source["source_id"], {})
        kind = "capture_failed"
        if record.get("status") == "captured_pending_review" and record.get("url") == source["url"]:
            raw = safe_child(Path(capture_root).resolve(), record["filename"]).read_bytes()
            if file_hash(raw) != record.get("original_sha256") or len(raw) != record.get("bytes"):
                raise ValueError("采集原件被修改")
            old = safe_child(Path(template_root).resolve(), source["original_file"]).read_bytes()
            a, meta_a = page_data(old, source["selector"])
            try:
                b, meta_b = page_data(raw, source["selector"])
                kind = "body_changed" if a != b else "page_metadata_changed" if meta_a != meta_b else "raw_only_changed" if old != raw else "unchanged"
            except (ValueError, UnicodeError):
                kind = "body_unreadable"
        result.append({"source_id": source["source_id"], "kind": kind})
    if len(result) != len(set(source_ids)): raise ValueError("采集范围不是已登记来源")
    return result


class MatterUpdater:
    def __init__(self, root, lifecycle, *, clock=time.time):
        self.root, self.lifecycle, self.clock = Path(root), lifecycle, clock
        self.store = Store(self.root / "update-journal", clock=clock)

    def stage(self, snapshot_root, *, ontology_stage, expected_head, principal):
        principal.require("maintainer")
        if self.lifecycle.head() != expected_head: raise KBError("BASE_VERSION_CHANGED")
        pack = validate_snapshot(snapshot_root, now=self.clock(), max_age_seconds=self.lifecycle.max_age_seconds, initial=expected_head is None)
        previous = self.lifecycle._load(self.lifecycle._version(expected_head)) if expected_head else None
        delta = change_set(previous, pack)
        manifest = json.loads((Path(snapshot_root) / "manifest.json").read_text(encoding="utf-8"))
        paths = [r["path"] for r in manifest["files"]] + ["manifest.json"]
        files = {p: self.store.object(safe_child(Path(snapshot_root).resolve(), p).read_bytes()) for p in paths}
        identifier = canonical_hash({"files": files, "ontology_stage": ontology_stage, "expected_head": expected_head})
        existing = self.store.get("runs", identifier)
        if existing: return {**existing, "replayed": True}
        for source in pack["sources"]:
            control = self.lifecycle.store.get("sources", source["source_id"])
            if control and datetime.fromisoformat(source["checked_at"]).timestamp() < control["checked_at"]:
                raise ValueError("不能用较旧回执覆盖当前来源观察")
        blocked = {row["source_id"] for row in delta["sources"] if row["kind"] in
            {"body_changed", "page_metadata_changed", "source_definition_changed", "source_removed"}}
        if delta["changed_rules"] or delta["removed_rules"]:
            blocked.update(r["source_id"] for r in (previous or pack)["rules"] if r["id"] in delta["changed_rules"] + delta["removed_rules"])
        for source_id in blocked:
            if self.lifecycle.store.get("sources", source_id):
                self.lifecycle.set_source_enabled(source_id, False, reason="Full-source update requires review; no automatic resume", principal=principal)
        candidate = self.lifecycle.prepare(snapshot_root, ontology_stage=ontology_stage, principal=principal)
        # Version records wrap the candidate; compare its identity rather than package timestamps.
        unchanged = expected_head is not None and self.lifecycle._version(expected_head)["id"] == candidate["candidate_id"]
        record = {"update_id": identifier, "expected_head": expected_head, "ontology_stage": ontology_stage,
            "status": "SOURCE_REVIEW_REQUIRED" if blocked else "NO_SEMANTIC_CHANGE" if unchanged else "ENGINEERING_CANDIDATE_READY",
            "delta": delta, "candidate_id": candidate["candidate_id"], "files": files,
            "blocked_sources": sorted(blocked), "knowledge_published": False, "human_business_review": False,
            "model_calls": 0, "actor": principal.id, "actor_kind": principal.kind}
        with self.store.db(True) as db:
            if self.lifecycle.head() != expected_head: raise KBError("BASE_VERSION_CHANGED")
            self.store.put("runs", identifier, record, db)
            self.store.audit("matter_update_staged", {"update_id": identifier, "status": record["status"]}, db)
        return {**record, "replayed": False}

    def block_capture(self, capture_root, source_ids, *, reason, principal):
        principal.require("maintainer")
        capture_root = Path(capture_root).resolve()
        raw = (capture_root / "snapshot.json").read_bytes()
        snapshot = json.loads(raw)
        if set(source_ids) - {r["source_id"] for r in snapshot["sources"]}:
            raise ValueError("暂停来源没有对应采集记录")
        files = {"snapshot.json": self.store.object(raw)}
        for row in snapshot["sources"]:
            if row["source_id"] in source_ids and row.get("filename"):
                content = safe_child(capture_root, row["filename"]).read_bytes()
                files[row["filename"]] = self.store.object(content)
        for source_id in source_ids:
            if self.lifecycle.store.get("sources", source_id):
                self.lifecycle.set_source_enabled(source_id, False, reason=reason, principal=principal)
        identifier = canonical_hash({"files": files, "sources": sorted(source_ids), "reason": reason})
        value = {"update_id": identifier, "status": "SOURCE_REVIEW_REQUIRED", "blocked_sources": sorted(source_ids),
            "files": files, "reason": reason, "knowledge_published": False, "model_calls": 0}
        self.store.put("runs", identifier, value)
        self.store.audit("matter_update_capture_blocked", {"update_id": identifier, "sources": sorted(source_ids)})
        return value

    def release_engineering(self, update_id, *, note, principal):
        principal.require("maintainer")
        record = self.store.get("runs", update_id)
        if not record or record["status"] not in ("ENGINEERING_CANDIDATE_READY", "NO_SEMANTIC_CHANGE", "ENGINEERING_RELEASED"):
            raise ValueError("更新尚未通过工程检查，来源暂停不能自动解除")
        if record.get("released_version"):
            return {"version": record["released_version"], "status": "ALREADY_RELEASED", "knowledge_published": False}
        released = self.lifecycle.release_preview(record["candidate_id"], record["expected_head"], note=note, principal=principal)
        record.update(status="ENGINEERING_RELEASED", released_version=released["version"])
        self.store.put("runs", update_id, record)
        self.store.audit("matter_update_engineering_release", {"update_id": update_id, "version": released["version"], "actor": principal.id})
        return released
