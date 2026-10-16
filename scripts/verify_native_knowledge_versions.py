"""Actual Nexent model/MCP version transitions using an isolated synthetic store.

Creates temporary agents and a temporary MCP registration, then removes only the
IDs created by this run. It never publishes the real knowledge candidate store.
"""

import argparse
import copy
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx

from gov_mcp.knowledge import KnowledgeStore, encode, fingerprint
from gov_mcp.knowledge_answers import select_citations
from gov_mcp.knowledge_backup import create_bundle, restore_bundle
from gov_mcp.knowledge_fetch import schedule, worker_once
from nexent_api import NexentAPI, ROOT, RUNTIME, nonthinking_params
from private_runtime import ensure_private_directory
from provision_knowledge_qa import bundle, QUESTION


PORT = 8783
REGION, MATTER = "controlled-fixture", "version-transition-only"
OUTPUT_TOKENS = 1024


def main(presentation_only=False):
    run_id = uuid.uuid4().hex
    service_name = "govkb_version_fixture_"+run_id[:8]
    parent = RUNTIME / "knowledge-version-fixtures" / run_id
    ensure_private_directory(parent)
    initial = parent / "initial"
    fixture_store = KnowledgeStore.initialize(initial)
    (initial / "publication-policy.json").write_text(encode({"schema": 1, "mode": "engineering_fixture"}), encoding="utf-8")
    roles = json.loads((RUNTIME / "m2/mcp/role-map.json").read_text(encoding="utf-8"))
    scope = {"region": REGION, "matter": MATTER, "included_branch": "仅限模拟版本工程测试", "scope_status": "synthetic_fixture_only"}
    token = secrets.token_urlsafe(48)
    (parent / "fixture-token.txt").write_text(token, encoding="ascii")
    accounts = json.loads((RUNTIME / "m2/accounts.json").read_text(encoding="utf-8"))
    model_state = json.loads((RUNTIME / "knowledge-update/qa-agent-state.json").read_text(encoding="utf-8"))
    real_store = KnowledgeStore(RUNTIME / "knowledge-update", read_only=True)
    protected_keys = ("documents", "revisions", "chunks", "latest_chunks", "versions", "heads")
    real_before = {key: real_store.status()["counts"][key] for key in protected_keys}
    config = {"id": "synthetic-native-versions", "label": "纯模拟版本资料，不是办事依据", "enabled": True,
              "origin": "https://fixtures.invalid", "allowed_paths": ["/docs/"], "seeds": ["https://fixtures.invalid/docs/guide"],
              "interval_seconds": 3600, "delay_seconds": 5, "content_selector": {"id": "body"},
              "metadata": {"title": "纯模拟文件，仅验证版本链路", "region": REGION, "matter": MATTER,
                           "document_type": "synthetic", "effective_from": None, "effective_to": None,
                           "scope_confirmed": True, "usage_basis": "Self-authored synthetic fixture, no policy authority"},
              "access_review": {"approved": True, "reviewer": "synthetic_engineering_no_human", "actor_kind": "synthetic",
                                "usage_basis": "Self-authored fixture; no internet request",
                                "checked_at": datetime.now(timezone.utc).isoformat()}}
    fixture_store.register(config)
    class Fixture:
        content = "身份证到期换领工程测试仅需模拟材料甲。这不是真实办理要求。"
        def fetch(self, source, url):
            return 200, {"content-type": "text/html"}, ('<article id="body"><p>'+self.content+'</p></article>').encode(), url
    fixture = Fixture()
    api = NexentAPI(runtime_base="http://127.0.0.1:5018")
    owned_agents, agent_versions, agent_bundles, records = {}, {}, {}, []
    owned_service, process, log_stream = None, None, None
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "run_id": run_id,
              "model": model_state["active_model"], "synthetic_only": True, "real_network_source_requests": 0,
              "business_review_completed": False, "official_knowledge_published_by_this_test": 0,
              "presentation_only": presentation_only,
              "records": records, "cleanup": {}, "passed": False}

    def checkpoint():
        (parent / "run-state.json").write_text(json.dumps({"agent_ids": owned_agents, "mcp_id": owned_service,
            "server_pid": process.pid if process else None, "report": report}, ensure_ascii=False, indent=2), encoding="utf-8")

    def collect():
        with fixture_store.db() as db:
            db.execute("UPDATE sites SET next_check=0")
        schedule(fixture_store)
        assert worker_once(fixture_store, fixture, owner="synthetic-native-version-worker")

    def publish_fixture():
        pending = fixture_store.status()["pending_review"][0]
        revision = fixture_store.amend(pending["id"], dict(pending["metadata"], effective_from="2020-01-01"))
        fixture_store.review(revision, "synthetic_engineering_no_human", True)
        candidate = fixture_store.candidate(REGION, MATTER)
        return fixture_store.publish(candidate, "synthetic_engineering_no_human", True)

    def start_server(root):
        nonlocal process, log_stream
        for name, data in (("knowledge-role-map.json", roles), ("knowledge-scope.json", scope)):
            (root / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with socket.socket() as probe:
            probe.bind(("0.0.0.0", PORT))
        log_stream = (parent / (root.name+"-server.log")).open("ab")
        environment = dict(os.environ, GOVKB_MCP_TOKEN=token)
        process = subprocess.Popen([sys.executable, "-m", "gov_mcp.knowledge_server", "--runtime", str(root),
                                    "--host", "0.0.0.0", "--port", str(PORT)], cwd=ROOT,
                                   env=environment, stdout=log_stream, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        checkpoint()
        deadline = time.monotonic()+30
        with httpx.Client(trust_env=False, timeout=1) as http:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Owned fixture server exited during startup")
                try:
                    if http.get(f"http://127.0.0.1:{PORT}/health").status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(.5)
        raise TimeoutError("Owned fixture server did not become ready")

    def stop_server():
        nonlocal process, log_stream
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if log_stream:
            log_stream.close()

    def configure_agent(role, pinned_version=None):
        api.login(accounts["developer"])
        config_bundle = copy.deepcopy(agent_bundles[role])
        prompt = config_bundle["agent_info"]["1"]
        if pinned_version:
            needle = "govkb_search_evidence(query="+json.dumps(QUESTION, ensure_ascii=False)+")"
            replacement = needle[:-1]+", knowledge_version="+json.dumps(pinned_version)+")"
            for key in ("duty_prompt", "few_shots_prompt"):
                assert needle in prompt[key]
                prompt[key] = prompt[key].replace(needle, replacement)
        agent_id, model = owned_agents[role], model_state["active_model"]
        api.call("POST", "/agent/update", {"agent_id": agent_id, "author": "ICT 工程验证",
            "group_ids": [model_state["group_id"]], "display_name": prompt["display_name"], "ingroup_permission": "READ_ONLY",
            "model_ids": [model_state["models"][model]], **{key: prompt[key] for key in ("duty_prompt", "few_shots_prompt", "constraint_prompt")},
            "model_params_override": {str(model_state["models"][model]): {"temperature": .1, "extra_params": nonthinking_params()}},
            "requested_output_tokens": OUTPUT_TOKENS, "allow_chat_metadata": False, "enable_protocol_repair_retry": False})
        agent_versions[role] = api.call("POST", f"/agent/{agent_id}/publish", {"version_name": "synthetic-version-qa",
            "release_note": "Synthetic engineering only; no real policy publication"})["version_no"]
        checkpoint()

    def run_case(role, scenario, expected_code, version, marker=None, historical=False):
        start, events, value, conversation, audit = time.monotonic(), [], None, None, []
        failed = None
        identity = api.login(accounts[role])
        principal = fingerprint([identity["tenant_id"], identity["user_id"]])
        conversation = api.runtime_call("PUT", "/conversation/create", {"title": "纯模拟版本验证-"+scenario+"-"+role})["data"]["conversation_id"]
        audit_path = fixture_store.root / "mcp-audit.jsonl"
        offset = audit_path.stat().st_size if audit_path.exists() else 0
        print(role, scenario, "running", flush=True)
        try:
            payload = {"query": "仅运行模拟版本工程验证，完整返回真实工具结果。", "conversation_id": conversation,
                "agent_id": owned_agents[role], "model_id": model_state["models"][model_state["active_model"]],
                "version_no": agent_versions[role], "is_debug": False, "history": [], "requested_output_tokens": OUTPUT_TOKENS,
                "enable_plan": False, "enable_automation_tool": False}
            with api.http.stream("POST", api.runtime_base+"/agent/run", json=payload,
                    headers={"Authorization": "Bearer "+api.token}, timeout=httpx.Timeout(120, connect=10)) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if time.monotonic()-start > 180:
                        raise TimeoutError("Synthetic version run exceeded total deadline")
                    if line.startswith("data:") and line[5:].strip():
                        events.append(json.loads(line[5:].strip()))
            finals = [event["content"] for event in events if event.get("type") == "final_answer" and not event.get("depth", 0)]
            value = finals[0] if len(finals) == 1 and isinstance(finals[0], dict) else json.loads("".join(finals))
            with audit_path.open("rb") as stream:
                stream.seek(offset)
                audit = [json.loads(line) for line in stream.read().decode("utf-8").splitlines() if line]
            expected_status = "OK" if marker else "BLOCKED"
            tool_events = [event for event in events if event.get("type") == "tool"]
            checks = {"two_native_tools_in_order": [event.get("tool_name") for event in tool_events] == ["govkb_get_environment_status", "govkb_search_evidence"],
                "exactly_two_actual_calls": [row["tool"] for row in audit] == ["get_environment_status", "search_evidence"],
                "trusted_identity_and_role": bool(audit) and all(row["principal_sha256"] == principal for row in audit) and value.get("data", {}).get("role") == role,
                "final_matches_actual_last_tool": bool(audit) and value == audit[-1]["result"],
                "expected_status_and_version": value.get("status") == expected_status and value.get("message_code") == expected_code and value.get("knowledge_version") == version,
                "historical_flag_correct": value.get("data", {}).get("historical_snapshot") is historical,
                "citations_and_dates_correct": (bool(value.get("citations")) and all(marker in citation["quote"] and citation["effective_from"] == "2020-01-01" and citation["knowledge_version"] == version for citation in value["citations"])) if marker else not value.get("citations"),
                "no_error_or_step_limit": not any(event.get("type") in ("error", "max_steps_reached") for event in events)}
            if marker:
                bound = select_citations(value["citations"], version)
                presented = value.get("data", {}).get("answer", "")
                checks["complete_originals_and_ids_in_presentation"] = bool(bound) and all(
                    citation["quote"] in presented and citation["citation_id"] in presented
                    and citation["citation_id"] == item["citation_id"] and citation["quote"] == item["text"]
                    for citation, item in zip(bound, value["items"], strict=True))
                checks["date_types_and_nonapproval_visible"] = "发布日期：" in presented and "生效日期：2020-01-01" in presented and "不构成审批决定" in presented
        except (httpx.HTTPError, TimeoutError, ValueError, OSError, KeyError, TypeError) as error:
            failed = type(error).__name__
            checks = {"completed_without_technical_error": False}
            if conversation is not None:
                try:
                    api.http.get(api.runtime_base+"/agent/stop/"+str(conversation), headers={"Authorization": "Bearer "+api.token}, timeout=10).raise_for_status()
                except httpx.HTTPError:
                    report["cleanup"]["stop_uncertain_conversation"] = False
        trace_path = parent / (str(conversation)+".json")
        trace_path.write_text(json.dumps({"conversation_id": conversation, "events": events, "audit": audit, "result": value}, ensure_ascii=False, indent=2), encoding="utf-8")
        record = {"role": role, "scenario": scenario, "conversation_id": conversation, "checks": checks,
                  "passed": all(checks.values()), "duration_ms": round((time.monotonic()-start)*1000, 2),
                  "failure_class": failed, "private_trace_sha256": fingerprint(trace_path.read_bytes()), "actual_result": value}
        records.append(record)
        checkpoint()
        print(role, scenario, "PASS" if record["passed"] else "FAIL", [key for key, ok in checks.items() if not ok], flush=True)
        if not record["passed"]:
            raise RuntimeError("Synthetic native version case failed; private trace retained")

    try:
        collect()
        first = publish_fixture()
        start_server(fixture_store.root)
        api.login(accounts["developer"])
        api.call("POST", "/mcp/add", {"name": service_name, "server_url": f"http://host.docker.internal:{PORT}/sse",
            "authorization_token": "Bearer "+token, "enabled": True, "group_ids": str(model_state["group_id"]), "ingroup_permission": "READ_ONLY"})
        services = api.call("GET", "/mcp/list")["remote_mcp_server_list"]
        selected = next(service for service in services if service.get("remote_mcp_server_name", service.get("mcp_name")) == service_name)
        owned_service = next(selected[key] for key in ("mcp_id", "remote_mcp_server_id", "id") if key in selected)
        checkpoint()
        api.call("GET", "/tool/scan_tool")
        for role in ("citizen", "staff"):
            config_bundle = bundle(role)
            prompt = config_bundle["agent_info"]["1"]
            prompt.update(name="govkb_version_fixture_"+role+"_"+run_id[:8], display_name="仅模拟版本测试（"+role+"）",
                          description="临时工程验证，不含真实政策，不用于办事咨询。", requested_output_tokens=OUTPUT_TOKENS)
            prompt["constraint_prompt"] += " 此次所有证据均为明确标记的模拟材料，不代表正式业务规则。"
            for tool in prompt["tools"]:
                tool["usage"] = service_name
            config_bundle["mcp_info"] = [{"mcp_server_name": service_name, "mcp_url": f"http://host.docker.internal:{PORT}/sse"}]
            imported = api.call("POST", "/agent/import", {"agent_info": config_bundle, "force_import": False})
            owned_agents[role] = imported["agent_id_mapping"]["1"]
            agent_bundles[role] = config_bundle
            checkpoint()
            configure_agent(role)
        report["temporary_agent_ids"] = dict(owned_agents)
        report["fixture_first_version"] = first
        for role in ("citizen", "staff"):
            run_case(role, "first-version", "EVIDENCE_FOUND", first, "模拟材料甲")
        if not presentation_only:
            fixture.content = "身份证到期换领工程测试仅需模拟材料乙。这不是真实办理要求。"
            collect()
            run_case("citizen", "change-pending", "UPDATE_PENDING_REVIEW", first)
            second = publish_fixture()
            report["fixture_second_version"] = second
            for role in ("citizen", "staff"):
                run_case(role, "new-version", "EVIDENCE_FOUND", second, "模拟材料乙")
            configure_agent("staff", first)
            run_case("staff", "pinned-history", "EVIDENCE_FOUND", first, "模拟材料甲", historical=True)
            configure_agent("staff")
            backup = create_bundle(fixture_store, parent / "full-backup")
            restored = restore_bundle(backup["directory"], parent / "restored")
            stop_server()
            fixture_store = KnowledgeStore(restored["restored_to"], read_only=True)
            start_server(fixture_store.root)
            run_case("staff", "after-full-restore", "EVIDENCE_FOUND", second, "模拟材料乙")
            report["full_restore_verified_by_actual_model"] = True
        report["passed"] = len(records) == (2 if presentation_only else 7) and all(record["passed"] for record in records)
    except Exception as error:
        report["failure_class"] = type(error).__name__
        (parent / "failure-detail.txt").write_text(str(error), encoding="utf-8")
        if isinstance(error, RuntimeError) and str(error).startswith("Nexent "):
            report["failed_platform_operation"] = str(error)
        print("version fixture stopped:", type(error).__name__, flush=True)
    finally:
        try:
            api.login(accounts["developer"])
            for role, agent_id in owned_agents.items():
                try:
                    api.call("DELETE", "/agent", {"agent_id": agent_id})
                    report["cleanup"]["temporary_agent_removed_"+role] = True
                except Exception:
                    report["cleanup"]["temporary_agent_removed_"+role] = False
            if owned_service is not None:
                try:
                    api.call("DELETE", "/mcp/"+str(owned_service))
                    report["cleanup"]["temporary_mcp_removed"] = True
                except Exception:
                    report["cleanup"]["temporary_mcp_removed"] = False
            listed = api.call("GET", "/agent/list?tenant_id="+model_state["tenant_id"])
            if isinstance(listed, dict):
                listed = listed.get("data", listed.get("agents", []))
            remaining_ids = {agent["agent_id"] for agent in listed}
            remaining_services = api.call("GET", "/mcp/list")["remote_mcp_server_list"]
            remaining_names = {service.get("remote_mcp_server_name", service.get("mcp_name")) for service in remaining_services}
            report["post_cleanup_platform_checks"] = {
                "fixture_agents_absent": not bool(set(owned_agents.values()) & remaining_ids),
                "fixture_mcp_absent": service_name not in remaining_names,
                "original_qa_agents_present": set(model_state["agents"].values()) <= remaining_ids,
                "real_knowledge_mcp_present": "govkb_public_evidence_mcp" in remaining_names}
            report["cleanup"]["platform_inventory_verified"] = all(report["post_cleanup_platform_checks"].values())
        except Exception:
            report["cleanup"]["platform_cleanup_login"] = False
        api.close()
        stop_server()
        report["cleanup"]["owned_fixture_server_stopped"] = process is None or process.poll() is not None
        real_after = {key: real_store.status()["counts"][key] for key in protected_keys}
        report["real_candidate_business_counts_unchanged"] = real_before == real_after
        report["real_candidate_counts"] = real_after
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["scope"] = ("Two fixed-question native model/MCP calls on a synthetic published fixture, one per authenticated role; full original citations, stable IDs, date labels and exact last-tool copying. No dynamic visitor-question, business accuracy or UI acceptance claim." if presentation_only else
            "Intended coverage: seven model/MCP cases on synthetic data for first/new versions, pending stop, pinned history and restore. Only completed records establish actual coverage. No business-policy correctness or UI acceptance claim.")
        report["passed"] = report["passed"] and all(report["cleanup"].values()) and report["real_candidate_business_counts_unchanged"]
        checkpoint()
        output = ROOT / "evidence/knowledge-update"
        prefix = "native-answer-fixture" if presentation_only else "native-version-fixture"
        (output / (prefix+"-"+run_id[:8]+".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (output / (prefix+".json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "cases": len(records), "cleanup": report["cleanup"],
                      "real_candidate_business_counts_unchanged": report["real_candidate_business_counts_unchanged"]}, ensure_ascii=False), flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--presentation-only", action="store_true", help="Run two bounded native citation-presentation cases; retain earlier version-transition evidence")
    main(presentation_only=parser.parse_args().presentation_only)
