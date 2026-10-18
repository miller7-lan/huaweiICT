"""Freeze a 30-question development run using actual query-selected source spans."""

import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_quality import publication_policy
from gov_mcp.knowledge_review import register_development_questions
from gov_mcp.knowledge_structure import VERSION as STRUCTURE_VERSION, brief
from private_runtime import ensure_private_directory
from verify_knowledge_source_units import build_units, parent_keys
from verify_knowledge_shadow_retrieval import validate_reference
import verify_knowledge_model_prevalidation as preview


ROOT = Path(__file__).resolve().parents[1]


def selected_evidence(store, selected, units):
    """Only selected exact spans enter prompts; reference answers/targets are absent."""
    if not store.read_only or len(selected) > 5:
        raise ValueError("需要只读候选库及最多五个检索单元")
    lookup = {(unit["revision_id"], unit["id"]): unit for unit in units}
    if len(lookup) != len(units):
        raise ValueError("检索单元标识重复")
    parents, evidence, seen = set(), [], set()
    with store.db() as db:
        for item in selected:
            unit = lookup.get((item["revision_id"], item["unit_id"]))
            if not unit or any(item[key] != unit[key] for key in ("source_id", "spans", "text_sha256")):
                raise ValueError("检索结果与冻结原文单元不匹配")
            parents.update(parent_keys(unit))
            if len(parents) > 5 or len(unit["parts"]) != len(unit["spans"]):
                raise ValueError("完整跨页引用必须符合五个父定位上限")
            row, _ = store._verify(db, unit["revision_id"], require_review=False)
            meta = json.loads(row["metadata"])
            doc = db.execute("SELECT site_id,url FROM documents WHERE id=?", (row["document_id"],)).fetchone()
            latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()[0]
            if row["state"] != "pending_review" or latest != row["id"] or doc["site_id"] != unit["source_id"]:
                raise ValueError("开发上下文须绑定当前待复核修订")
            source = db.execute("SELECT config,paused_reason,last_success FROM sites WHERE id=?", (doc["site_id"],)).fetchone()
            for part, span in zip(unit["parts"], unit["spans"]):
                chunk = db.execute("SELECT text FROM chunks WHERE revision_id=? AND locator=?", (row["id"], span["locator"])).fetchone()
                start, end = span["range"]
                if (not chunk or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(chunk[0])
                        or part["locator"] != span["locator"] or part["range"] != span["range"]
                        or chunk[0][start:end] != part["text"] or fingerprint(chunk[0].encode()) != span["parent_text_sha256"]
                        or fingerprint(part["text"].encode()) != span["span_text_sha256"]):
                    raise ValueError("检索片段与原文位置或指纹不匹配")
                key = (row["id"], span["locator"], start, end)
                if key in seen:
                    continue
                seen.add(key)
                fields = meta.get("source_observation", {}).get("fields", {})
                evidence.append({"evidence_id": "E-"+fingerprint(key)[:16], "source_id": doc["site_id"],
                    "url": doc["url"], "title": meta["title"], "revision": row["id"],
                    "original_sha256": row["sha256"], "locator": span["locator"], "source_span": [start, end],
                    "source_unit_id": unit["id"], "source_unit_proposal_unapproved": True,
                    "region": meta["region"], "matter": meta["matter"], "review_state": row["state"],
                    "effective_from": meta.get("effective_from"), "effective_to": meta.get("effective_to"),
                    "published_at": meta.get("published_at"), "scope_confirmed": meta.get("scope_confirmed", False),
                    "current_applicability": "unknown",
                    "page_observations": {k: v for k, v in fields.items() if k in ("block:document_fields", "block:page_date", "meta:pubdate")},
                    "source_check": {"enabled": json.loads(source["config"])["enabled"], "paused": bool(source["paused_reason"]), "last_success": source["last_success"]},
                    "text": part["text"]})
    return evidence


def model_descriptor(store, offline_model_run=None):
    state = json.loads((store.root / "consultation-agent-state.json").read_text(encoding="utf-8"))
    model = state["active_model"]
    if offline_model_run:
        _, previous = preview.load_manifest(offline_model_run)
        if previous["model"] != model:
            raise ValueError("历史冻结模型与当前配置名称不同，不能切换模型")
        return {"name": model, "digest": previous["model_digest"],
            "definition_sha256": previous["model_definition_sha256"], "source_kind": "prior_frozen_manifest",
            "source_run_id": offline_model_run, "server_checked": False}
    with httpx.Client(trust_env=False, timeout=10) as client:
        installed = client.get(preview.MODEL_ORIGIN+"/api/tags").raise_for_status().json()["models"]
        selected = next(item for item in installed if item["name"] == model)
        info = client.post(preview.MODEL_ORIGIN+"/api/show", json={"model": model}).raise_for_status().json()
    return {"name": model, "digest": selected["digest"], "definition_sha256": fingerprint(info),
        "source_kind": "live_local_api", "source_run_id": None, "server_checked": True}


def prepare(retrieval_run, offline_model_run=None):
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", retrieval_run):
        raise ValueError("需要精确检索开发运行编号")
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update", read_only=True)
    output = ROOT / "evidence/knowledge-update"
    report = json.loads((output / ("source-unit-development-"+retrieval_run+".json")).read_text(encoding="utf-8"))
    frozen = store.root / "shadow-retrieval" / report["baseline_run_id"]
    reference_bytes = (frozen / "reference-frozen.json").read_bytes()
    snapshot = json.loads((frozen / "corpus-frozen.json").read_text(encoding="utf-8"))
    private_units = json.loads((store.root / "source-units" / retrieval_run / "units-frozen.json").read_text(encoding="utf-8"))
    if (not report["passed_integrity_checks"] or fingerprint(reference_bytes) != report["reference_sha256"]
            or fingerprint(snapshot) != report["corpus_sha256"] or fingerprint(private_units) != report["private_unit_snapshot_sha256"]):
        raise ValueError("冻结检索语料、参考或单元不匹配")
    units, proposals = build_units(snapshot["chunks"], snapshot["bindings"])
    if private_units != {"materialized_units": units, "proposals": proposals}:
        raise ValueError("冻结单元不能按原始片段重建")
    reference = json.loads(reference_bytes)
    validate_reference(reference)
    if len(reference["cases"]) != 30 or len(report["records"]) != 30:
        raise ValueError("必须保留完整30题开发集")
    reference_path = ROOT / "evaluation/knowledge/pilot-ai-reference-answers.json"
    if reference_path.read_bytes() != reference_bytes:
        raise ValueError("当前参考已经变化；不能混用检索快照")
    rubric_bytes = (ROOT / "evaluation/knowledge/pilot-model-prevalidation-rubric.json").read_bytes()
    if set(json.loads(rubric_bytes)["case_rules"]) != {case["id"] for case in reference["cases"]}:
        raise ValueError("评阅规则必须与完整30题对应")
    descriptor = model_descriptor(store, offline_model_run)
    model = descriptor["name"]
    cases = []
    for question, row in zip(reference["cases"], report["records"]):
        if (question["id"], question["question"]) != (row["case_id"], row["question"]):
            raise ValueError("检索问题顺序或内容不一致")
        excerpts = selected_evidence(store, row["selected_units"], units)
        entry = {"id": question["id"], "question": question["question"], "evidence": excerpts,
            "web_only_reference_ids_withheld": [],
            "source_structure_notes": [{"evidence_id": item["evidence_id"], **brief(item["text"])} for item in excerpts]}
        entry["payload_sha256"] = fingerprint(preview.model_payload(model, entry, "conditions_v2"))
        cases.append(entry)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+uuid.uuid4().hex[:8]
    register_development_questions(KnowledgeStore(store.root), [case["question"] for case in cases], "retrieved-unit-development:"+run_id)
    directory = preview.run_directory(run_id)
    ensure_private_directory(directory)
    (directory / "reference-frozen.json").write_bytes(reference_bytes)
    (directory / "rubric-frozen.json").write_bytes(rubric_bytes)
    manifest = {"schema": 1, "kind": "retrieved_source_unit_model_prevalidation", "run_id": run_id,
        "prepared_at": datetime.now(timezone.utc).isoformat(), "model": model, "model_digest": descriptor["digest"],
        "model_definition_sha256": descriptor["definition_sha256"], "model_descriptor_source_kind": descriptor["source_kind"],
        "model_descriptor_source_run_id": descriptor["source_run_id"], "model_server_checked_at_preparation": descriptor["server_checked"],
        "reference_sha256": fingerprint(reference_bytes),
        "rubric_sha256": fingerprint(rubric_bytes), "prompt_profile": "conditions_v2", "citation_policy": "distinct_quotes_v2",
        "source_structure_version": STRUCTURE_VERSION, "dataset_role": "development_prevalidation_not_independent_acceptance",
        "system_sha256": fingerprint(preview.SYSTEMS["conditions_v2"]), "schema_sha256": fingerprint(preview.SCHEMA),
        "publication_policy_sha256": fingerprint(publication_policy(store)), "counts_before": store.status()["counts"],
        "context_selection": "Actual frozen query-selected source-unit parts; no reference targets or answers choose model evidence.",
        "retrieval_run_id": retrieval_run, "retrieval_rank_profile": report["development_rank_profile"],
        "retrieval_signal_version": report["query_signal_version"], "retrieval_corpus_sha256": report["corpus_sha256"],
        "unarchived_web_content_in_model_input": False, "gold_answers_in_model_input": False, "cases": cases}
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k: v for k, v in manifest.items() if k != "cases"}
    summary.update(manifest_sha256=fingerprint(path.read_bytes()), case_count=len(cases),
        max_input_characters=max(len(preview.model_payload(model, case, "conditions_v2")["messages"][1]["content"]) for case in cases),
        max_evidence_fragments=max(len(case["evidence"]) for case in cases),
        human_review_completed=False, official_publication_performed=False)
    encoded = json.dumps(summary, ensure_ascii=False, indent=2)
    (output / ("model-prevalidation-plan-"+run_id+".json")).write_text(encoded, encoding="utf-8")
    (output / "model-prevalidation-plan.json").write_text(encoded, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="冻结完整30题检索后模型开发验证；不会发布候选")
    parser.add_argument("--retrieval-run", required=True)
    parser.add_argument("--offline-model-run", help="仅从已验证的历史冻结运行读取模型描述；实际运行仍须检查本机模型摘要")
    args = parser.parse_args()
    prepare(args.retrieval_run, args.offline_model_run)
