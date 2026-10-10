"""Authorize callers and inspect the actual empty business store."""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Callable


def result(status: str, code: str, **data) -> dict:
    return {
        "status": status,
        "items": [],
        "citations": [],
        "knowledge_version": None,
        "message_code": code,
        "data": data,
    }


class BlankStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, content TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY);"
                "CREATE TABLE IF NOT EXISTS materials (id TEXT PRIMARY KEY, case_id TEXT NOT NULL);"
            )

    def counts(self) -> dict:
        # Runtime tools never mutate business tables.
        with closing(sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=2)) as db:
            return {
                name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in ("evidence", "cases", "materials")
            }


class GovernmentTools:
    def __init__(self, store: BlankStore, role_map_path: Path, retry_delay: float = 0.25):
        self.store = store
        self.role_map_path = role_map_path
        self.retry_delay = retry_delay

    def role(self, tenant_id: str, user_id: str) -> str | None:
        if not tenant_id or not user_id:
            return None
        # Read each time so account provisioning does not require a restart.
        roles = json.loads(self.role_map_path.read_text(encoding="utf-8"))
        if not isinstance(roles, dict) or any(not isinstance(value, dict) for value in roles.values()):
            raise ValueError("Role map must contain tenant-to-user mappings")
        role = roles.get(tenant_id, {}).get(user_id)
        return role if role in ("citizen", "staff") else None

    def _run(self, tenant_id: str, user_id: str, fn: Callable) -> dict:
        try:
            role = self.role(tenant_id, user_id)
            if not role:
                return result("BLOCKED", "ROLE_NOT_CONFIGURED")
            for attempt in range(2):
                try:
                    counts = self.store.counts()
                    # Refuse to use any accidentally populated data in this phase.
                    if any(counts.values()):
                        return result("ERROR", "BLANK_BOUNDARY_VIOLATED")
                    return fn(role, counts)
                except TimeoutError:
                    if attempt == 0:
                        time.sleep(self.retry_delay)
                    else:
                        return result("ERROR", "TOOL_TIMEOUT", attempts=2)
        except (OSError, ValueError, sqlite3.Error):
            return result("ERROR", "CONFIG_OR_STORAGE_UNAVAILABLE")
        return result("ERROR", "UNEXPECTED_STOP")

    def get_environment_status(self, tenant_id: str, user_id: str) -> dict:
        return self._run(
            tenant_id, user_id,
            lambda role, counts: result(
                "OK", "ENVIRONMENT_READY", role=role,
                resources={name: {"configured": True, "count": count} for name, count in counts.items()},
            ),
        )

    def search_evidence(self, tenant_id: str, user_id: str) -> dict:
        return self._run(tenant_id, user_id, lambda role, counts: result("EMPTY", "NO_KNOWLEDGE"))

    def check_case_prerequisites(self, tenant_id: str, user_id: str) -> dict:
        return self._run(
            tenant_id, user_id,
            lambda role, counts: result("BLOCKED", "FORBIDDEN") if role != "staff" else
            result("BLOCKED", "MISSING_CASE_AND_MATERIALS", missing=["case", "materials", "evidence"]),
        )
