"""Isolated M1 extension. No operation imports or publishes business data."""

import hashlib
import json
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from gov_mcp.core import result


TABLES = ("evidence", "cases", "materials", "graph_nodes", "graph_edges", "candidates", "versions")


class ExtensionStore:
    def __init__(self, path: Path, timeout: float = 2):
        self.path, self.timeout = path, timeout
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            # Empty structures only. Graph schema is deliberately not a claim
            # that the seven business object types have been populated/verified.
            db.executescript("""
                CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY, case_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS graph_nodes(id TEXT PRIMARY KEY, object_type TEXT NOT NULL,
                    source_id TEXT NOT NULL REFERENCES evidence(id));
                CREATE TABLE IF NOT EXISTS graph_edges(id TEXT PRIMARY KEY,
                    from_id TEXT NOT NULL REFERENCES graph_nodes(id),
                    to_id TEXT NOT NULL REFERENCES graph_nodes(id), relation TEXT NOT NULL,
                    source_id TEXT NOT NULL REFERENCES evidence(id));
                CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL);
            """)

    def counts(self):
        try:
            with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True,
                                         timeout=self.timeout)) as db:
                return {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in TABLES}
        except sqlite3.OperationalError as error:
            if getattr(error, "sqlite_errorcode", 0) & 0xff in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                raise TimeoutError("Extension storage deadline exceeded") from error
            raise


class ExtensionTools:
    def __init__(self, runtime: Path, retry_delay: float = .25, timeout: float = 2):
        self.runtime, self.retry_delay = runtime, retry_delay
        self.store = ExtensionStore(runtime / "extension.sqlite3", timeout)
        self.audit_lock = threading.Lock()

    def principal(self, tenant_id, user_id):
        mappings = json.loads((self.runtime / "role-map.json").read_text(encoding="utf-8"))
        principal = mappings.get(tenant_id, {}).get(user_id)
        if not isinstance(principal, dict) or principal.get("role") not in ("citizen", "staff"):
            return None
        if type(principal.get("knowledge_maintainer", False)) is not bool:
            raise ValueError("Invalid maintenance grant")
        return principal

    def config(self):
        config = json.loads((self.runtime / "connections.json").read_text(encoding="utf-8"))
        if not isinstance(config, dict) or any(type(config.get(k)) is not bool for k in ("evidence", "graph", "updates")):
            raise ValueError("Invalid connection configuration")
        return config

    def execute(self, name, tenant_id, user_id, **arguments):
        started = time.perf_counter()
        role = None
        try:
            principal = self.principal(tenant_id, user_id)
            if principal is None:
                value = result("BLOCKED", "ROLE_NOT_CONFIGURED")
            else:
                role = principal["role"]
                # Navigation and model supplied parameters are never grants.
                if name in ("prepare_update", "publish_update") and not principal.get("knowledge_maintainer", False):
                    value = result("BLOCKED", "FORBIDDEN", required_permission="knowledge_maintainer")
                else:
                    config = self.config()
                    for attempt in range(2):
                        try:
                            counts = self.store.counts()
                            if any(counts.values()):
                                value = result("ERROR", "BLANK_BOUNDARY_VIOLATED")
                            else:
                                value = self._dispatch(name, role, counts, config, arguments)
                            break
                        except TimeoutError:
                            if attempt == 0:
                                time.sleep(self.retry_delay)
                            else:
                                value = result("ERROR", "TOOL_TIMEOUT", attempts=2)
        except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
            value = result("ERROR", "CONFIG_OR_STORAGE_UNAVAILABLE")
        value["data"].update(request_id=secrets.token_hex(8), role=role)
        entry = {"time": datetime.now(timezone.utc).isoformat(), "tool": "govx_" + name,
                 "request_id": value["data"]["request_id"], "role": role,
                 "principal_sha256": hashlib.sha256(f"{tenant_id}\0{user_id}".encode()).hexdigest(),
                 "status": value["status"], "message_code": value["message_code"],
                 "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result": value}
        with self.audit_lock, (self.runtime / "tool-audit.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return value

    def _dispatch(self, name, role, counts, config, arguments):
        if name == "get_environment_status":
            if not all(config.values()):
                return result("ERROR", "CONNECTION_NOT_CONFIGURED", resources=config, counts=counts)
            return result("OK", "ENVIRONMENT_READY", resources=config, counts=counts)
        resource = {"search_evidence": "evidence", "query_knowledge_graph": "graph",
                    "check_case_prerequisites": "evidence", "prepare_update": "updates", "publish_update": "updates"}.get(name)
        if resource is None:
            return result("BLOCKED", "UNKNOWN_TOOL")
        if name == "check_case_prerequisites" and role != "staff":
            return result("BLOCKED", "FORBIDDEN")
        if not config[resource]:
            return result("ERROR", "CONNECTION_NOT_CONFIGURED", resource=resource)
        if name == "search_evidence":
            return result("EMPTY", "NO_KNOWLEDGE")
        if name == "query_knowledge_graph":
            return result("EMPTY", "NO_GRAPH", nodes=[], edges=[])
        if name == "check_case_prerequisites":
            return result("BLOCKED", "MISSING_CASE_AND_MATERIALS", missing=["case", "materials", "evidence"], pending_count=0)
        if name == "prepare_update":
            inputs = arguments.get("inputs", [])
            if not isinstance(inputs, list) or any(not isinstance(i, str) for i in inputs):
                return result("BLOCKED", "INVALID_INPUT")
            if inputs:
                return result("BLOCKED", "M1_INPUT_NOT_ALLOWED")
            return result("EMPTY", "NO_CHANGES", input_count=0, candidate_count=counts["candidates"],
                          scope="No processable input in this request; external policies were not checked")
        missing = []
        if not arguments.get("candidate_id"):
            missing.append("candidate")
        elif counts["candidates"] == 0:
            return result("BLOCKED", "CANDIDATE_NOT_FOUND")
        if arguments.get("confirmed") is not True:
            missing.append("human_confirmation")
        # M1 never publishes, even if a caller supplies a plausible identifier.
        return result("BLOCKED", "PUBLISH_PREREQUISITES_MISSING", missing=missing,
                      candidate_count=counts["candidates"], version_count=counts["versions"])
