"""Authenticated MCP surface for the bounded cross-document flow (frozen_qa_flow_v3).

A Nexent agent's own model writes the drafts. Two tools carry the flow:

- govkb_flow_open(question)  scope gates, retrieval, a task id, evidence and answer rules;
- govkb_flow_submit(task_id, draft)  the same decision step as frozen_qa_flow_v3.ask:
  "final" (answer, partial answer or stop), or "revise" because the checks failed or
  because a follow-up retrieval added evidence.

The agent cannot widen its own budget: each task accepts at most MAX_MODEL_CALLS
drafts, counted and saved before a draft is processed, so a crash or a retry never
resets it. A finished task returns its stored result again and changes nothing.
Task files hold evidence text and drafts and stay in the private runtime. Nothing is
published; results stay PAUSED_UNREVIEWED archive explanations.
"""
import argparse
import hashlib
import json
import re
import secrets
import socket
import threading
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from gov_mcp import frozen_qa, frozen_qa_flow_v3 as flow
from gov_mcp.frozen_retrieval import EvidenceIndex
from gov_mcp.json_output import validate_contract
from gov_mcp.server import BearerGate


SERVICE = "govkb_frozen_flow"
PORT = 8787
TASK_ID = re.compile(r"[A-Za-z0-9_-]{20,64}")
SOURCES = ("frozen_qa.py", "frozen_qa_flow_v3.py", "frozen_qa_flow_server.py", "frozen_retrieval.py",
           "knowledge_retrieval.py")
REVISE_FAILED = ("未通过确定性核验。请只依据同一任务的证据重写完整草稿后再次提交：source_id 与 locator 照抄证据标注；"
                 "quote 必须是该证据原文的连续片段；每条 claim 的金额、期限、日期及单位必须出现在它自己的 quote 中；"
                 "原文为否定的不得写成可以。无法改正的结论请删除，并把对应缺项写入 unknowns。")
REVISE_EVIDENCE = "已按缺项补充证据（见 evidence_added）。请结合原有证据与新增证据重新作答后再次提交。"


def code_fingerprints():
    base = Path(__file__).parent
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in SOURCES}


def shown(units):
    return [{key: item[key] for key in ("rank", "source_id", "locator", "text", "cells") if key in item}
            for item in units]


class FlowService:
    def __init__(self, units, corpus_sha256, task_dir, rules="strict", follow_up_after_answer=False):
        if rules not in flow.RULE_PROFILES:
            raise ValueError("UNKNOWN_RULE_PROFILE")
        self.index = EvidenceIndex(units)
        self.units, self.corpus_sha256, self.rules = list(self.index.units), corpus_sha256, rules
        self.follow_up_after_answer = follow_up_after_answer
        self.task_dir = Path(task_dir)
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()  # one draft at a time; the service is a single process

    def answer_rules(self):
        if self.rules == "light":
            return frozen_qa.COMMON_RULES + flow.LIGHT_RULES
        return frozen_qa.COMMON_RULES + frozen_qa.PROPOSED_RULES + flow.FLOW_RULES

    def _path(self, task_id):
        return self.task_dir / (task_id + ".json")

    def _save(self, task):
        path = self._path(task["task_id"])
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(task, ensure_ascii=False), "utf-8")
        temp.replace(path)

    def _result(self, task, replayed=False):
        return {"status": "OK", "action": "final", "task_id": task["task_id"], "result_kind": task["result_kind"],
                "stop_origin": task["stop_origin"], "final": task["final"], "final_checks": task["final_checks"],
                "drafts_used": task["state"]["calls"], "claims_dropped": task["state"]["claims_dropped"],
                "claims_excerpted": task["state"]["claims_excerpted"], "replayed": replayed, **frozen_qa.STATUS}

    def open(self, question):
        if type(question) is not str or not question.strip() or len(question) > 500:
            return {"status": "BLOCKED", "error": "QUESTION_REQUIRED_MAX_500_CHARS"}
        gate = frozen_qa.scope_gate(question)
        if gate:  # Before retrieval; no task is created, so nothing can be submitted for it.
            return {"status": "STOP", "gate": gate, "message": frozen_qa.GATE_MESSAGES[gate],
                    "final": frozen_qa._stop(gate, frozen_qa.GATE_MESSAGES[gate]), **frozen_qa.STATUS}
        packet = self.index.retrieve(question)
        state = flow.new_state()
        state["pool"] = packet["evidence"]
        state["rounds"].append({"kind": "initial", "added": len(state["pool"])})
        task = {"task_id": secrets.token_urlsafe(24), "question": question, "corpus_sha256": self.corpus_sha256,
                "rules": self.rules, "state": state, "drafts": [], "final": None, "result_kind": None,
                "stop_origin": None, "final_checks": None}
        with self.lock:
            self._save(task)
        return {"status": "OK", "task_id": task["task_id"], "evidence": shown(state["pool"]),
                "answer_rules": self.answer_rules(), "max_drafts": flow.MAX_MODEL_CALLS,
                "retrieval_plan": {"version": packet["plan"]["version"], "semantic_coverage_verified": False,
                                   "queries": frozen_qa.query_hints(packet["plan"], state["pool"])},
                "corpus_sha256": self.corpus_sha256, **frozen_qa.STATUS}

    def submit(self, task_id, output):
        if type(task_id) is not str or TASK_ID.fullmatch(task_id) is None:
            return {"status": "BLOCKED", "error": "UNKNOWN_TASK"}
        try:
            validate_contract(output, frozen_qa.AnswerOutput)
        except (ValueError, TypeError, RecursionError):
            # A malformed draft is refused without spending the budget.
            return {"status": "BLOCKED", "error": "ANSWER_CONTRACT_VIOLATION"}
        with self.lock:
            path = self._path(task_id)
            if not path.is_file():
                return {"status": "BLOCKED", "error": "UNKNOWN_TASK"}
            task = json.loads(path.read_text("utf-8"))
            if task["corpus_sha256"] != self.corpus_sha256 or task["rules"] != self.rules:
                return {"status": "BLOCKED", "error": "TASK_BELONGS_TO_ANOTHER_CORPUS_OR_PROFILE"}
            if task["final"] is not None:
                return self._result(task, replayed=True)
            if len(task["drafts"]) >= flow.MAX_MODEL_CALLS:
                # Reserved by an earlier attempt that never finished (for example a crash).
                return self._finish(task, frozen_qa._stop("DRAFT_BUDGET_EXHAUSTED", "本任务的草稿次数已用完，未形成可核验的答复。"),
                                    "stop", "DRAFT_BUDGET_EXHAUSTED")
            task["drafts"].append(output)
            self._save(task)  # Reserve before processing: a failure below still counts.
            state = task["state"]
            state["calls"] = len(task["drafts"]) - 1  # advance() counts this draft itself
            step = flow.advance(state, output, self.units, follow_up_after_answer=self.follow_up_after_answer)
            if step["action"] == "final":
                return self._finish(task, step["final"], step["kind"], step["origin"])
            self._save(task)
            base = {"status": "OK", "action": "revise", "task_id": task_id, "drafts_used": state["calls"],
                    "drafts_left": flow.MAX_MODEL_CALLS - state["calls"], **frozen_qa.STATUS}
            if step["action"] == "repair":
                return {**base, "reason": "FAILED_CHECKS", "failures": step["failures"], "instructions": REVISE_FAILED}
            return {**base, "reason": "MORE_EVIDENCE", "evidence_added": shown(step["added"]),
                    "instructions": REVISE_EVIDENCE}

    def _finish(self, task, final, kind, origin):
        pool = task["state"]["pool"]
        task.update(final=final, result_kind=flow.label_kind(final, kind), stop_origin=origin,
                    final_checks=flow.check(final, pool) if final["outcome"] == "answer" else None)
        self._save(task)
        return self._result(task)


def create_app(service, token, port=PORT):
    if not isinstance(token, str) or len(token) < 32 or not token.isascii():
        raise ValueError("DEDICATED_TOKEN_REQUIRED")
    security = TransportSecuritySettings(
        allowed_hosts=["localhost:*", "127.0.0.1:*", f"host.docker.internal:{port}"],
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*"])
    mcp = FastMCP(SERVICE, transport_security=security)
    startup = code_fingerprints()

    def execute(operation, *args):
        if code_fingerprints() != startup:
            return {"status": "BLOCKED", "error": "SERVICE_SOURCE_CHANGED_RESTART_REQUIRED"}
        return operation(*args)

    @mcp.tool()
    def govkb_flow_open(question: str) -> dict:
        """Start one question: returns a task_id, frozen Henan evidence and answer rules, or a scope stop."""
        return execute(service.open, question)

    @mcp.tool()
    def govkb_flow_submit(task_id: str, outcome: str, answer: str, assertions: list[dict],
                          stop_reason: str = "", unknowns: list[str] | None = None) -> dict:
        """Submit a draft for the task: outcome 'answer' or 'stop'; each assertion has claim, source_id,
        locator and an exact quote. Returns action 'final' with the checked result, or action 'revise'
        with failures or added evidence; revise and submit again with the same task_id."""
        output = {"outcome": outcome, "answer": answer, "assertions": assertions,
                  "stop_reason": stop_reason, "unknowns": [] if unknowns is None else unknowns}
        return execute(service.submit, task_id, output)

    async def health(request):
        return JSONResponse({"service": SERVICE, "status": "UP", "publication_enabled": False, "rules": service.rules,
                             "follow_up_after_answer": service.follow_up_after_answer,
                             "max_drafts": flow.MAX_MODEL_CALLS, "corpus_sha256": service.corpus_sha256,
                             "startup_source_sha256": startup})
    app = mcp.sse_app()
    app.router.routes.insert(0, Route("/health", health))
    return BearerGate(app, token, TransportSecurityMiddleware(security))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path(".runtime/frozen-flow-20261114"),
                        help="Private directory for access.json and task files")
    parser.add_argument("--corpus", type=Path, required=True, help="The frozen corpus.json to serve (591 units)")
    parser.add_argument("--rules", choices=flow.RULE_PROFILES, default="strict")
    parser.add_argument("--follow-up-after-answer", action="store_true")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            parser.error("PORT_OCCUPIED_EXISTING_SERVICE_PRESERVED")
    corpus = json.loads(args.corpus.read_text("utf-8"))
    corpus_sha = corpus["manifest"]["corpus_sha256"]
    if corpus_sha != frozen_qa.sha(json.dumps(corpus["units"], ensure_ascii=False, sort_keys=True)):
        parser.error("CORPUS_HASH_MISMATCH")
    args.runtime.mkdir(parents=True, exist_ok=True)
    access = args.runtime / "access.json"
    if not access.exists():
        # Private bearer for the Nexent MCP binding; never printed or committed.
        access.write_text(json.dumps({"mcp_token": secrets.token_urlsafe(32)}), "utf-8")
    token = json.loads(access.read_text("utf-8"))["mcp_token"]
    service = FlowService(corpus["units"], corpus_sha, args.runtime / "flow-tasks", args.rules,
                          args.follow_up_after_answer)
    uvicorn.run(create_app(service, token, args.port), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
