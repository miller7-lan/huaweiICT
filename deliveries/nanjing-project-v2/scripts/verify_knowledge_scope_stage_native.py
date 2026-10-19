"""Actual authenticated two-role scope-stage stops; no candidate policy answers."""

from verify_knowledge_consultation import main as verify


QUESTIONS = (
    ("collection", "本人已申请到期换领，委托家属代领新证需要什么？", "NO_PUBLISHED_KNOWLEDGE"),
    ("application", "本地成年本人不能到场，想委托他人申请换领，并代领新证。", "OUT_OF_CONFIRMED_SCOPE"),
    ("clarification", "本人到期换领，可以让家属代办吗？", "SCOPE_CLARIFICATION_REQUIRED"),
)
CASES = [(role, "scope-"+scenario, question, expected) for role in ("citizen", "staff") for scenario, question, expected in QUESTIONS]


if __name__ == "__main__":
    verify(CASES, evidence_prefix="scope-stage-native",
        scope_description="Three stage questions through each of the two existing dynamic-question preview agents, with real authenticated role accounts. "
            "Checks actual original-question fingerprints, tool order, empty/blocked scope outcomes and exact final tool-result copying. "
            "No new agent definition, candidate exposure, policy publication, substantive policy accuracy or independent business acceptance claim.")
