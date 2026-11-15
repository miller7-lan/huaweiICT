"""Comparison-only bounded CLI repair over the f699 frozen verifier.

This is a scoped prototype, not the full native V2 contract. It certifies
citations/numbers only; semantic completeness remains an external assessment.
Each state directory is a single-use durable task, never an automatic retry.
"""
import hashlib
import json
from pathlib import Path
from copy import deepcopy

from gov_mcp import frozen_qa as qa
from gov_mcp.json_output import StrictOutput

VERSION = "bb03-bounded-cli-prototype-1"
RULES = ("每条claim只陈述该条quote支持的事实，不同金额分开。年份引用标题或文头；"
         "文头日期明确写为文件日期，不改称发布日期或施行日期。保留对象、分支、条件、例外、单位。"
         "不得遗漏问题必答内容来逃避核验。只用给定证据，历史材料不确认现行效力。")


class GapPlan(StrictOutput):
    missing_items: list[str]
    next_query: str


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def identity(unit):
    return tuple(unit.get(k) for k in ("source_id", "revision_id", "original_sha256", "locator"))


class Task:
    def __init__(self, path, question, units, corpus_sha256):
        self.path = Path(path)
        # Atomic mkdir prevents duplicate/concurrent task execution, including restarts.
        self.path.mkdir(parents=True, exist_ok=False)
        self.units = {identity(u): deepcopy(u) for u in units}
        if len(self.units) != len(units):
            raise ValueError("DUPLICATE_CORPUS_IDENTITY")
        self.state = {"version": VERSION, "task_id": self.path.name,
                      "question": question, "corpus_sha256": corpus_sha256,
                      "evidence": [], "events": [], "repair_reserved": False,
                      "model_calls_reserved": 0, "retrieval_calls": 0,
                      "required_checks": ["scope", "retrieved_source", "exact_contiguous_quote", "numbers_units_dates"],
                      "uncovered_checks": ["complete_semantic_entailment", "answer_completeness", "current_validity"],
                      "final": None, "decision": None}
        self.save()

    def save(self):
        p = self.path / "state.json"
        temp = p.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(p)

    def event(self, origin, reason, **details):
        self.state["events"].append({"stop_origin": origin, "reason_code": reason, **details})
        self.save()

    def add(self, packet, corpus_sha256, task_id):
        if corpus_sha256 != self.state["corpus_sha256"] or task_id != self.state["task_id"]:
            raise ValueError("TASK_OR_CORPUS_MISMATCH")
        known = {identity(u) for u in self.state["evidence"]}
        additions = []
        for u in packet:
            key = identity(u)
            original = self.units.get(key)
            if original is None or original["text"] != u["text"]:
                raise ValueError("SOURCE_VERSION_OR_TEXT_MISMATCH")
            if key not in known:
                item = deepcopy(original)
                item.update(evidence_id="E" + str(len(known) + 1), rank=len(known) + 1,
                            text_sha256=hashlib.sha256(original["text"].encode()).hexdigest())
                additions.append(item); known.add(key)
        # Bounded packet, no text truncation or partial mutation on overflow.
        merged = self.state["evidence"] + additions
        if len(merged) > 48 or sum(len(u["text"]) for u in merged) > 30000:
            raise ValueError("EVIDENCE_BUDGET_EXCEEDED")
        self.state["evidence"] = merged
        self.save()

    def reserve_repair(self):
        if self.state["repair_reserved"]:
            raise ValueError("REPAIR_BUDGET_EXHAUSTED")
        self.state["repair_reserved"] = True
        self.save()  # Reserve before external operations; never reset on failure.

    def call(self, chat, messages, contract, stage):
        if self.state["model_calls_reserved"] >= 3:
            raise ValueError("MODEL_BUDGET_EXHAUSTED")
        self.state["model_calls_reserved"] += 1
        self.event("model", "CALL_RESERVED", stage=stage)
        response = chat(messages, contract, stage)
        # Also validate when using fixture adapters.
        contract.model_validate(response["output"])
        return response["output"]

    def finish(self, decision, final):
        self.state.update(decision=decision, final=final)
        self.save()
        return deepcopy(self.state)


def check(output, evidence):
    normalized = qa.normalize_citation_locators(output, evidence)
    result = qa.verify(normalized, evidence)
    by_key = {(u["source_id"], u["locator"]): u for u in evidence}
    failures = list(result["failures"])
    for index, claim in enumerate(normalized["assertions"], 1):
        unit = by_key.get((claim["source_id"], claim["locator"]))
        if unit is not None and (not claim["quote"].strip() or claim["quote"] not in unit["text"]):
            failures.append(f"A{index}_EXACT_CONTIGUOUS_QUOTE_REQUIRED")
    return {"passed": not failures, "failures": failures}


def messages(question, evidence, plan=None):
    value = qa.build_messages(question, evidence, "proposed", retrieval_plan=plan)
    value[0]["content"] += "\n" + RULES
    return value


def run(question, units, chat, state_path, corpus_sha256, initial_output=None):
    task = Task(state_path, question, units, corpus_sha256)
    gate = qa.scope_gate(question)
    if gate:
        task.event("backend_gate", gate)
        return task.finish("STOP", qa._stop(gate, qa.GATE_MESSAGES[gate]))
    try:
        packet = qa.retrieve_with_plan(units, question, 5)
        task.state["retrieval_calls"] += 1
        task.add(packet["evidence"], corpus_sha256, task.state["task_id"])
        evidence = task.state["evidence"]
        if initial_output is None:
            output = task.call(chat, messages(question, evidence, packet["plan"]), qa.AnswerOutput, "initial")
        else:
            # Paired experiment only: use the exact baseline draft; no gold data.
            output = qa.AnswerOutput.model_validate(initial_output).model_dump()
            task.state["shared_initial_draft"] = True
        task.state["drafts"] = [deepcopy(output)]
        checks = check(output, evidence)
        task.event("model_stop" if output["outcome"] == "stop" else "verifier",
                   "MODEL_INSUFFICIENT" if output["outcome"] == "stop" else "INITIAL_CHECK",
                   checks=checks, draft_version=1)
        if output["outcome"] == "stop" or not checks["passed"]:
            task.reserve_repair()
            if output["outcome"] == "stop":
                plan_messages = [
                    {"role": "system", "content": "复核停止的具体缺口。只建议一次定向检索，不能回答问题、编造用户事实、扩大范围或认定现行。若无可查缺口，next_query为空。"},
                    {"role": "user", "content": json.dumps({"question": question, "draft": output, "evidence": evidence}, ensure_ascii=False)}]
                gap = task.call(chat, plan_messages, GapPlan, "gap_plan")
                task.event("stop_review", "GAP_PLAN", **gap)
                query = gap["next_query"].strip()
                if len(query) > 500:
                    raise ValueError("QUERY_TOO_LONG")
                if query:
                    supplement = qa.retrieve_with_plan(units, query, 5)
                    task.state["retrieval_calls"] += 1
                    task.add(supplement["evidence"], corpus_sha256, task.state["task_id"])
                    evidence = task.state["evidence"]
            repair_messages = messages(question, evidence)
            repair_messages.append({"role": "user", "content": "仅一次修复机会：逐项回答原问题；只用累计证据；按核验失败修引文、日期身份与事实绑定。仍缺证据则停止并列出缺口。先前草稿和失败：" + json.dumps({"draft": output, "checks": checks}, ensure_ascii=False)})
            output = task.call(chat, repair_messages, qa.AnswerOutput, "repair")
            task.state["drafts"].append(deepcopy(output))
            checks = check(output, evidence)
            task.event("verifier", "RECHECK", checks=checks, draft_version=2)
        task.state["checks"] = checks
        if output["outcome"] == "stop":
            return task.finish("STOP", qa._stop("UNRESOLVED_AFTER_REPAIR", output["stop_reason"], output["unknowns"]))
        if not checks["passed"]:
            return task.finish("STOP", qa._stop("VERIFICATION_FAILED", "一次修复后仍未通过核验。", checks["failures"]))
        output = qa.normalize_citation_locators(output, evidence)
        # Discard the unverified summary; all final business statements are checked claims.
        final = {**output, "answer": "\n".join(dict.fromkeys(c["claim"].strip() for c in output["assertions"]))}
        return task.finish("CHECKED_CITATIONS_NUMBERS", final)
    except Exception as error:
        task.event("execution", "EXECUTION_FAILED", error_type=type(error).__name__)
        return task.finish("EXECUTION_FAILED", qa._stop("EXECUTION_FAILED", "调用、契约或运行预算异常，本次未完成。"))
