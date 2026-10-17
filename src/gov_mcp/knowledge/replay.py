"""Synthetic-only engineering fixture responses. This is NOT Jev inference."""

class SyntheticReplay:
    name = "synthetic_contract_replay_not_jev"

    def evaluate(self, state, questions, model):
        answers = {}
        for key, question in questions.items():
            if question["type"] == "choice":
                answers[key] = {"type": "choice", "choice": "guide", "confidence": .99,
                    "probabilities": {k: float(k == "guide") for k in question["criteria"]}}
            else:
                answers[key] = {"type": "noul", "noul": .99 if key in {"domains:identity", "purposes:material"} or key.isdigit() else .01}
        return {"model": model, "answers": answers, "usage": {"input_tokens": 0, "output_tokens": 0}}
