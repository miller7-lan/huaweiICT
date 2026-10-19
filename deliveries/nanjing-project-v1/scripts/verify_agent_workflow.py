"""Reproducible full synthetic lifecycle; public evidence contains no credentials."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.decision.workflow import Workflow
from private_runtime import ensure_private_directory


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), "utf-8")


def run(output_directory=None):
    started = time.perf_counter()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    runtime = ROOT/".runtime/agent-workflow-proof"/run_id
    ensure_private_directory(runtime)
    w = Workflow(runtime)
    actor = "demo-professional"
    facts = {"reason": "expiry", "age_group": "adult", "self_service": True, "electronic_verified": False}
    checks = []
    def check(name, ok):
        checks.append({"name": name, "passed": bool(ok)})
        if not ok: raise AssertionError(name)
    initial = w.status()["head"]
    check("初始来源为空且 API 保持留空", w.status()["source_state"] == "waiting_source_configuration")
    w.configure_demo(actor=actor)
    w.heartbeat(force=True)
    w.set_profile("noise", actor=actor)
    w.heartbeat(force=True)
    check("无正文变化不执行模型角色或创建版本", not w.roles.calls and len(w.status()["versions"]) == 1)
    unknown = w.query({k:v for k,v in facts.items() if k != "electronic_verified"}, "branch-adult-self")
    check("未知电子资格不默认要求纸质表", unknown["counts"]["confirmed"] == 3 and unknown["follow_up"]["enabled"])
    before = w.query(facts, "branch-adult-self", parent_task=unknown["task_id"])
    check("一次取齐已知依据后独立核验，不固定拆轮", before["counts"]["confirmed"] == 4 and before["retrieval_rounds"] == 1)
    capped = w.query(facts, "branch-adult-self", retriever=lambda n, refs, snap: [])
    check("依据持续缺失时最多三轮，不产生确定要求", capped["retrieval_rounds"] == 3 and capped["counts"]["confirmed"] == 0)
    w.set_profile("changed", actor=actor)
    w.heartbeat(force=True)
    package = w.status()["packages"][0]
    check("Jev 合同回放与实际模型明确区分", package["classification"]["summary"]["actual_models"] == [] and package["classification"]["summary"]["http_attempts"] == 0)
    check("更新主控、整理、核验子角色均调用程序工具", {"update_controller", "extractor", "update_verifier", "jev"} <= {c["role"] for c in w.roles.calls})
    check("新原文及本地稀疏向量自动可检索", "保持条码清晰" in w.search_documents("条码清晰")["hits"][0]["quote"])
    check("未经复核业务知识没有生效", w.status()["head"] == initial)
    blocked = w.query(facts, "branch-adult-self")
    check("变更发现即暂停关联要求，其余继续服务", blocked["counts"]["paused"] == 2 and blocked["counts"]["confirmed"] == 2)
    check("未经发布的新原文仅在候选依据区展示", len(blocked["candidate_evidence"]) == 2)
    check("未指定截止时间不是逾期", not package["overdue"] and package["deadline"] is None)
    w.deadline(package["id"], "2000-01-01T00:00:00+08:00", actor=actor)
    check("人工指定截止时间，逾期不解除暂停", w.status()["packages"][0]["overdue"])
    def publish(ids):
        w.review(package["id"], ids, actor=actor, decision="accepted", note="仅为可复现工程模拟，不是实际业务审批。")
        return w.publish(package["id"], ids, w.status()["head"], actor=actor)
    partial = publish(["req-slip"])
    after_partial = w.query(facts, "branch-adult-self")
    check("分组发布依赖完整项，保留其他冲突", partial["status"] == "partially_published" and after_partial["counts"]["paused"] == 1)
    complete = publish(["req-card-apply", "alias-reviewed-short", "ontology-example-note"])
    after = w.query(facts, "branch-adult-self")
    check("确认新要求后才恢复对应服务", complete["status"] == "published" and after["counts"]["paused"] == 0)
    check("同义与本体建议需要确认且不扩大执行器权限", any(a["label"] == "旧证" for a in after["confirmed_aliases"]) and w.status()["ontology"][0]["executable"] is False)
    history = w.history(before["task_id"])
    check("历史答案不改写，叠加当前可用性", history["original_answer"] == before and history["current_availability"]["counts"]["paused"] == 2)
    w.rollback(initial, w.status()["head"], actor=actor)
    rolled = w.query(facts, "branch-adult-self")
    check("回退保留历史与当前冲突控制", len(w.status()["versions"]) == 3 and rolled["counts"]["paused"] == 2)
    w.set_profile("unavailable", actor=actor)
    w.heartbeat(force=True)
    check("来源失效明确失败且保留旧版本", w.status()["sources"][0]["status"] == "failed" and w.status()["head"] == initial)
    restored = Workflow(runtime)
    check("重启保留任务、版本、暂停和审计", restored.history(before["task_id"])["original_answer"] == before and restored.query(facts, "branch-adult-self")["counts"]["paused"] == 2)
    with restored.store.db() as db:
        audit = [{"operation": r[0], "payload": json.loads(r[1])} for r in db.execute("SELECT operation,payload FROM audit")]
    check("核对与发布分别留存记录", any(a["operation"] == "workflow_professional_review" for a in audit) and any(a["operation"] == "workflow_professional_publish" for a in audit))
    out = Path(output_directory) if output_directory is not None else ROOT/"evidence/agent-workflow"
    out.mkdir(parents=True, exist_ok=True)
    sources = [*sorted((ROOT/"src/gov_mcp/decision").glob("*.py")), ROOT/"src/gov_mcp/decision/workbench.html",
               ROOT/"config/decision/workflow-source.json", ROOT/"config/decision/providers.template.json", Path(__file__).resolve()]
    report = {"run_id": run_id, "mode": "synthetic_engineering_only", "actual_remote_model_calls": 0,
              "real_site_collection_verified": False, "real_jev_accuracy_verified": False, "new_nexent_workflow_verified": False,
              "passed": sum(c["passed"] for c in checks), "total": len(checks), "checks": checks,
              "elapsed_seconds": round(time.perf_counter()-started, 3),
              "source_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    write(out/"verification.json", report)
    write(out/"example-before.json", before)
    write(out/"example-partial.json", after_partial)
    write(out/"example-after.json", after)
    write(out/"audit.json", audit)
    write(out/"tool-traces.json", restored.rows("trace"))
    write(runtime/"verification.json", report)
    print(json.dumps({"passed": report["passed"], "total": report["total"], "model_calls": 0, "evidence": str(out)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Keep this verification in a separate evidence directory")
    run(parser.parse_args().output)
