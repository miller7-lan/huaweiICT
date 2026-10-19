"""Replay the HTTP intake lifecycle with an explicit fake verifier, never cloud AI."""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from starlette.testclient import TestClient
from gov_mcp.decision.server import create_app
from gov_mcp.decision.workflow import Workflow


class ContractVerifier:
    def __init__(self): self.calls = []
    def enabled(self): return True
    def verify(self, template, values):
        self.calls.append(deepcopy(values))
        return {"model": "deepseek-contract-replay-not-real", "usage": {"input_tokens": 0, "output_tokens": 0}, "output": {
            "fields": [{"id": field["id"], "value": values[field["id"]],
                "display": next(option["label"] for option in field["options"] if type(option["value"]) is type(values[field["id"]]) and option["value"] == values[field["id"]])}
                for field in template["fields"]], "unknown_fields": sorted(key for key, value in values.items() if value is None)}}


def run(output):
    checks = []
    def check(name, value):
        checks.append({"name": name, "passed": bool(value)})
        if not value: raise AssertionError(name)
    origin = "http://127.0.0.1:8780"
    verifier = ContractVerifier()
    with tempfile.TemporaryDirectory(prefix="ict-intake-replay-") as root:
        workflow = Workflow(Path(root))
        app = create_app(workflow, "fixture-professional", "fixture-service", start_worker=False, form_verifier=verifier)
        with TestClient(app, base_url=origin, headers={"Origin": origin}) as client:
            check("未登录不能访问草稿", client.get("/api/forms/state").status_code == 403)
            client.post("/login", json={"role": "citizen"})
            form = client.post("/api/forms/start", json={}).json()
            check("首题与独立入口可用", form["question"]["id"] == "reason" and client.get("/guided-form").status_code == 200)
            for field, answer in [("reason", "到期了"), ("age_group", "成年人"), ("self_service", "我自己"), ("electronic_verified", "未验证")]:
                form = client.post("/api/forms/answer", json={"form_id": form["id"], "revision": form["revision"], "field_id": field, "answer": answer}).json()
            check("逐项填写不调用核查或创建咨询", not verifier.calls and not workflow.rows("query"))
            payload = {"form_id": form["id"], "revision": form["revision"], "consent": False}
            check("外发同意必需", client.post("/api/forms/review", json=payload).json()["error"] == "FORM_EGRESS_CONSENT_REQUIRED")
            payload["consent"] = True
            form = client.post("/api/forms/review", json=payload).json()
            check("原回答仅留本地，核查只接收代码", verifier.calls == [{"reason": "expiry", "age_group": "adult", "self_service": True, "electronic_verified": None}])
            check("未知不补全，结果必须人工检查", form["review"]["unknown_fields"] == ["electronic_verified"] and not workflow.rows("query"))
            submit = {"form_id": form["id"], "revision": form["revision"], "review_id": form["review"]["review_id"], "branch_id": "branch-adult-self", "acknowledge": False}
            check("人工确认不可跳过", client.post("/api/forms/submit", json=submit).json()["error"] == "FORM_HUMAN_CONFIRMATION_REQUIRED")
            submit["acknowledge"] = True
            response = client.post("/api/forms/submit", json=submit)
            check("进入现有咨询而非正式政务提交", response.status_code == 200 and response.json()["receipt"]["official_submission"] is False)
            receipt = response.json()["receipt"]
            check("未知保持追问而非纸质表默认", response.json()["answer"]["follow_up"]["enabled"])
            check("提交后原回答清除", response.json()["form"]["answers"] == {})
            check("重复提交不新增咨询", client.post("/api/forms/submit", json=submit).json()["receipt"] == receipt and len(workflow.rows("query")) == 1)
            check("查询历史仍受会话归属限制", client.get("/api/history", params={"task_id": receipt["task_id"]}).status_code == 200)
            workflow.configure_demo(actor="demo-professional")
            workflow.set_profile("changed", actor="demo-professional")
            workflow.heartbeat(force=True)
            history = client.get("/api/history", params={"task_id": receipt["task_id"]}).json()
            check("表单回执继续经过当前暂停门", bool(history["current_availability"]["paused"]))
            client.post("/logout", json={})
            check("退出使草稿与历史读取失效", client.get("/api/forms/state").status_code == 403 and client.get("/api/history", params={"task_id": receipt["task_id"]}).status_code == 403)
    paths = [ROOT/"src/gov_mcp/decision"/name for name in ("intake.py", "intake.html", "intake.js", "intake.css", "server.py", "workflow.py")]
    paths += [ROOT/"config/decision/intake-form.json", ROOT/"config/decision/connections.json", Path(__file__).resolve()]
    report = {"mode": "synthetic_http_contract_replay", "actual_remote_model_calls": 0, "real_deepseek_quality_verified": False,
        "official_submission": False, "passed": sum(item["passed"] for item in checks), "total": len(checks), "checks": checks,
        "source_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", "utf-8")
    print(json.dumps({"passed": report["passed"], "total": report["total"], "actual_remote_model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT/"evidence/guided-intake-20261018/verification.json")
    run(parser.parse_args().output)
