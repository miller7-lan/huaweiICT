"""Offline, synthetic rule-4 probes; no model, source modification or business approval."""
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1]).absolute()
sys.path.insert(0, str(repo / "src"))
from gov_mcp import frozen_qa as qa
from gov_mcp import frozen_qa_flow_v3 as flow


def unit(source, locator, text, kind="html_paragraph"):
    return dict(source_id=source, locator=locator, text=text,
                text_sha256=qa.sha(text), evidence_kind=kind,
                revision_id="synthetic-r1", original_sha256="synthetic-original")


body = unit("SYNTHETIC-A", "正文段落4", "测试事项工本费每证20元。")
heading = unit("SYNTHETIC-A", "文头", "2007年2月14日 财综[2007]34号")
pool = [body, heading]


def candidate(number):
    text = f"依据{number}，测试事项工本费每证20元。"
    return dict(outcome="answer", answer=text, stop_reason="", unknowns=[], assertions=[
        dict(claim=text, source_id=body["source_id"], locator=body["locator"], quote=body["text"]),
        dict(claim="来源文头", source_id=heading["source_id"], locator=heading["locator"], quote=heading["text"]),
    ])


cases = [
    ("correct_number", "财综〔2007〕34号", True),
    ("wrong_issuer_prefix", "豫财综〔2007〕34号", False),
    ("wrong_serial", "财综〔2007〕35号", False),
    ("wrong_year", "财综〔2008〕34号", False),
]
results = []
for name, number, expected in cases:
    output = candidate(number)
    check = flow.check(output, pool)
    settled = flow.settle_draft(output, pool, pool)
    results.append(dict(case=name, fixture_kind="synthetic", number=number,
                        expected_check_passed=expected, actual_check=check,
                        meets_expectation=check["passed"] == expected,
                        normalized_key=flow._document_number_key(number),
                        input=output, pool=pool, settled=settled))
print(json.dumps(dict(frozen_commit="fedefb92bb39576468f61e0b781f172b6d6239d2",
                     real_model_requests=0, cases=results), ensure_ascii=False, indent=2))
