"""Authenticated MCP surface so a Nexent agent can answer free questions from the frozen corpus.

The agent's own model writes the answer; these tools supply the evidence and
the deterministic checks of the "proposed" method: retrieval with scope gates
before answering, and quote/number/unit verification after. Nothing is
published, and results stay PAUSED_UNREVIEWED archive explanations.
"""
import argparse
import hashlib
import json
import secrets
import socket
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from gov_mcp import frozen_qa
from gov_mcp.json_output import object_from_json, validate_contract
from gov_mcp.server import BearerGate


SERVICE = "govkb_frozen_qa"
PORT = 8786


def code_fingerprints():
    base = Path(__file__).parent
    return {name: hashlib.sha256((base / name).read_bytes()).hexdigest()
            for name in ("frozen_qa.py", "frozen_qa_server.py", "knowledge_retrieval.py")}


class FrozenQAService:
    def __init__(self, units, corpus_sha256):
        self.units, self.corpus_sha256 = units, corpus_sha256

    def retrieve(self, question):
        if type(question) is not str or not question.strip() or len(question) > 500:
            return {"status": "BLOCKED", "error": "QUESTION_REQUIRED_MAX_500_CHARS"}
        gate = frozen_qa.scope_gate(question)
        evidence = [] if gate == "PRIVATE_FACT_NOT_ACCEPTED" else frozen_qa.retrieve(self.units, question)
        result = {"status": "STOP" if gate else "OK", "gate": gate, "message": frozen_qa.GATE_MESSAGES.get(gate),
                  "evidence": [{key: item[key] for key in ("rank", "source_id", "locator", "text")}
                               for item in evidence],
                  "answer_rules": frozen_qa.COMMON_RULES + frozen_qa.PROPOSED_RULES,
                  "corpus_sha256": self.corpus_sha256, **frozen_qa.STATUS}
        return result

    def verify(self, question, answer_json):
        """Check an AnswerOutput JSON string against the same retrieval; failures become a stop."""
        try:
            output = object_from_json(answer_json)
            validate_contract(output, frozen_qa.AnswerOutput)
        except (ValueError, TypeError, RecursionError):
            return {"status": "BLOCKED", "error": "ANSWER_JSON_CONTRACT_VIOLATION"}
        gate = frozen_qa.scope_gate(question)
        if gate:
            return {"status": "STOP", "gate": gate, "final": frozen_qa._stop(gate, frozen_qa.GATE_MESSAGES[gate]),
                    **frozen_qa.STATUS}
        checks = frozen_qa.verify(output, frozen_qa.retrieve(self.units, question))
        final = output if checks["passed"] else frozen_qa._stop(
            "VERIFICATION_FAILED", "回答未通过引用与数字核验，已改为停止作答。", checks["failures"])
        return {"status": "OK", "checks": checks, "final": final, **frozen_qa.STATUS}


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
    def govkb_frozen_retrieve(question: str) -> dict:
        """Return the top five frozen Henan evidence units and answer rules, or a scope stop."""
        return execute(service.retrieve, question)

    @mcp.tool()
    def govkb_frozen_verify(question: str, answer_json: str) -> dict:
        """Verify a JSON answer's quotes, numbers and day units against the same evidence."""
        return execute(service.verify, question, answer_json)

    async def health(request):
        return JSONResponse({"service": SERVICE, "status": "UP", "publication_enabled": False,
                             "corpus_sha256": service.corpus_sha256, "startup_source_sha256": startup})
    app = mcp.sse_app()
    app.router.routes.insert(0, Route("/health", health))
    return BearerGate(app, token, TransportSecurityMiddleware(security))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path(".runtime/frozen-qa-20261030"))
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            parser.error("PORT_OCCUPIED_EXISTING_SERVICE_PRESERVED")
    corpus_path = args.runtime / "corpus.json"
    corpus = json.loads(corpus_path.read_text("utf-8"))
    access = args.runtime / "access.json"
    if not access.exists():
        # Private bearer for the Nexent MCP binding; never printed or committed.
        access.write_text(json.dumps({"mcp_token": secrets.token_urlsafe(32)}), "utf-8")
    token = json.loads(access.read_text("utf-8"))["mcp_token"]
    service = FrozenQAService(corpus["units"], corpus["manifest"]["corpus_sha256"])
    uvicorn.run(create_app(service, token, args.port), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
