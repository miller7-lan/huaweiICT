"""Traditional keyword-matching Q&A bot, used only as a comparison arm (no model call).

It stands for the first generation of government Q&A bots: the question's literal keywords
(character bigrams, the same literal scoring as frozen_qa.retrieve) are matched against the
frozen corpus, and the single best-matching unit is returned verbatim as the answer. By design
it has no scope gate, no splitting of compound questions, no synthesis across documents and no
verification, because such bots have none. Titles and page dates are never returned on their
own, so the arm is not made weaker than a real FAQ bot.

It is not part of the product and is not loaded by the flow service. Its output has the same
fields as the other arms, so the same review pack and scoring sheet are used for all of them.
"""
from gov_mcp import frozen_qa

METHOD = "keyword_bot"
NO_MATCH = "未找到与问题匹配的内容，请换个问法或转人工。"


def ask(question, units, *, context=""):
    """Return one record shaped like frozen_qa.ask / flow.ask records."""
    if type(question) is not str or not question.strip():
        raise ValueError("INVALID_QUESTION")
    body = [unit for unit in units if unit.get("evidence_kind") not in frozen_qa.METADATA_KINDS]
    query = question + ("\n" + context if context else "")
    hits = frozen_qa.retrieve(body, query, k=1)
    if hits:
        unit = hits[0]
        final = {"outcome": "answer", "answer": unit["text"], "stop_reason": "", "unknowns": [],
                 "assertions": [{"claim": unit["text"], "source_id": unit["source_id"],
                                 "locator": unit["locator"], "quote": unit["text"]}]}
        kind = "answer"
    else:
        final = {"outcome": "stop", "answer": NO_MATCH, "stop_reason": "NO_KEYWORD_MATCH", "unknowns": [],
                 "assertions": []}
        kind = "stop"
    return {"schema_version": 1, "method": METHOD, "question": question, "question_sha256": frozen_qa.sha(question),
            "retrieval": {"version": "keyword-bigram-top1", "top_k": 1,
                          "evidence": [{key: item.get(key) for key in ("rank", "source_id", "locator", "score",
                                                                       "text_sha256")} for item in hits]},
            "gate": None, "model": {"called": False, "calls": []}, "model_output": None, "checks": None,
            "result_kind": kind, "final": final, "status": dict(frozen_qa.STATUS)}
