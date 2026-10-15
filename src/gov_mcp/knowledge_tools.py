"""Authenticated-role, fixed-scope access to published knowledge. No maintenance tools."""

import json
import re
import sqlite3
import threading
from pathlib import Path

from gov_mcp.core import result
from gov_mcp.knowledge import KnowledgeStore, encode, fingerprint
from gov_mcp.knowledge_scope import excluded_branch


class KnowledgeTools:
    def __init__(self, runtime):
        self.root = Path(runtime)
        self.store = KnowledgeStore(self.root, read_only=True)
        self.scope = json.loads((self.root / "knowledge-scope.json").read_text(encoding="utf-8"))
        self.lock = threading.Lock()

    def execute(self, name, tenant_id, user_id, **arguments):
        try:
            roles = json.loads((self.root / "knowledge-role-map.json").read_text(encoding="utf-8"))
            principal = roles.get(tenant_id, {}).get(user_id, {})
            role = principal.get("role")
            if role not in ("citizen", "staff", "developer"):
                value = result("BLOCKED", "ROLE_NOT_CONFIGURED")
            elif name == "get_environment_status":
                status = self.store.status()
                value = result("OK", "PUBLISHED_KNOWLEDGE_READER_READY", role=role,
                    matter_id=self.scope["matter"], region=self.scope["region"],
                    published_scope_count=status["counts"]["heads"], candidate_document_count=status["counts"]["documents"],
                    publication_required=True, formal_approval=False)
            elif name == "search_evidence":
                question = arguments.get("query", "")
                if not isinstance(question, str) or not question.strip() or len(question) > 300:
                    value = result("BLOCKED", "INVALID_QUESTION")
                elif re.search(r"\d{17}[\dXx]|[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)", question):
                    value = result("BLOCKED", "PERSONAL_DATA_NOT_ALLOWED")
                elif excluded_branch(question):
                    value = result("BLOCKED", "OUT_OF_CONFIRMED_SCOPE", included_branch=self.scope["included_branch"])
                else:
                    found = self.store.search(question, self.scope["region"], self.scope["matter"],
                                              version=arguments.get("knowledge_version"), as_of=arguments.get("as_of"))
                    value = result(found["status"], found["message_code"], role=role,
                        region=self.scope["region"], matter_id=self.scope["matter"], formal_approval=False,
                        historical_snapshot=found.get("historical_snapshot", False),
                        source_freshness=found.get("source_freshness", []), update_available=found.get("update_available", False))
                    value["knowledge_version"] = found["knowledge_version"]
                    value["citations"] = found["citations"]
                    value["items"] = [{"text": c["quote"], "locator": c["locator"], "revision_id": c["revision_id"]} for c in found["citations"]]
            else:
                value = result("BLOCKED", "READ_ONLY_TOOL_REQUIRED")
        except (ValueError, OSError, TypeError, AttributeError, sqlite3.Error):
            value = result("ERROR", "KNOWLEDGE_READER_UNAVAILABLE")
        # Prove which question reached the server without persisting visitor text
        # or rejected personal data in the audit trail.
        audit = {"tool": name, "principal_sha256": fingerprint([tenant_id, user_id]),
                 "argument_sha256": fingerprint(arguments), "result": value}
        with self.lock, (self.root / "mcp-audit.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(encode(audit)+"\n")
        return value
