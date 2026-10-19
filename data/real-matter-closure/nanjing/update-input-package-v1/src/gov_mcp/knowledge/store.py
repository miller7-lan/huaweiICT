"""SQLite state, fencing leases, immutable objects and compare-and-swap publication."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class KBError(Exception):
    pass


class Store:
    def __init__(self, root, clock=time.time):
        self.root, self.clock = Path(root), clock
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "objects").mkdir(exist_ok=True)
        self.db_path = self.root / "knowledge.sqlite3"
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, operation TEXT NOT NULL, payload TEXT NOT NULL);
            """)

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, table, key, db=None):
        if table not in {"state", "sources", "jobs", "runs", "candidates", "versions"}:
            raise ValueError("Unknown table")
        if db is None:
            with self.db() as connection:
                return self.get(table, key, connection)
        column = "value" if table == "state" else "payload"
        primary = "key" if table == "state" else "id"
        row = db.execute(f"SELECT {column} FROM {table} WHERE {primary}=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, table, key, value, db=None):
        if db is None:
            with self.db(True) as connection:
                return self.put(table, key, value, connection)
        if table not in {"state", "sources", "runs", "candidates"}:
            raise ValueError("Use explicit immutable/job operations")
        primary, column = ("key", "value") if table == "state" else ("id", "payload")
        db.execute(f"INSERT INTO {table}({primary},{column}) VALUES (?,?) ON CONFLICT({primary}) DO UPDATE SET {column}=excluded.{column}", (key, encoded(value)))

    def rows(self, table):
        if table not in {"sources", "jobs", "runs", "candidates", "versions"}:
            raise ValueError("Unknown table")
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute(f"SELECT payload FROM {table} ORDER BY rowid DESC")]

    def audit(self, operation, value, db=None):
        if db is None:
            with self.db(True) as connection:
                return self.audit(operation, value, connection)
        db.execute("INSERT INTO audit(time,operation,payload) VALUES (?,?,?)", (self.clock(), operation, encoded(value)))

    def object(self, data):
        key = hashlib.sha256(data).hexdigest()
        path = self.root / "objects" / key
        if path.exists():
            if path.read_bytes() != data:
                raise KBError("OBJECT_INTEGRITY_FAILURE")
            return key
        temporary = path.with_name(".tmp-" + uuid.uuid4().hex)
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return key

    def read_object(self, key):
        if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise KBError("INVALID_OBJECT")
        data = (self.root / "objects" / key).read_bytes()
        if hashlib.sha256(data).hexdigest() != key:
            raise KBError("OBJECT_INTEGRITY_FAILURE")
        return data

    def enqueue(self, run, source_id, assets, processing_hash):
        identity = digest({"source": source_id, "assets": assets, "processing": processing_hash})
        with self.db(True) as db:
            self.assert_lease(run, db)
            row = db.execute("SELECT id FROM jobs WHERE identity=?", (identity,)).fetchone()
            if row:
                return self.get("jobs", row[0], db), False
            job = {"id": uuid.uuid4().hex, "source_id": source_id, "assets": assets, "processing_hash": processing_hash,
                   "status": "queued", "stage": "discovered", "created_at": self.clock(), "attempts": 0,
                   "classification": None, "candidate_id": None, "validation": None}
            db.execute("INSERT INTO jobs(id,identity,payload) VALUES (?,?,?)", (job["id"], identity, encoded(job)))
            return job, True

    def claim(self, owner, lease_seconds, budget):
        now = self.clock()
        with self.db(True) as db:
            old = self.get("state", "lease", db) or {"fence": 0, "expires": 0}
            if old["expires"] > now:
                return None
            if old.get("run_id"):
                previous = self.get("runs", old["run_id"], db)
                if previous and previous["status"] == "running":
                    previous.update(status="interrupted", finished_at=now)
                    self.put("runs", previous["id"], previous, db)
            run = {"id": uuid.uuid4().hex, "owner": owner, "fence": old["fence"]+1,
                   "started_at": now, "deadline": now+budget, "status": "running", "jobs": []}
            self.put("state", "lease", {"owner": owner, "fence": run["fence"], "run_id": run["id"], "expires": now+lease_seconds}, db)
            self.put("runs", run["id"], run, db)
            return run

    def assert_lease(self, run, db):
        current = self.get("state", "lease", db) or {}
        if (current.get("run_id"), current.get("fence"), current.get("owner")) != (run["id"], run["fence"], run["owner"]) or current.get("expires", 0) <= self.clock() or run["deadline"] <= self.clock():
            raise KBError("STALE_EXECUTOR")

    def renew(self, run, seconds):
        with self.db(True) as db:
            self.assert_lease(run, db)
            lease = self.get("state", "lease", db)
            lease["expires"] = self.clock()+seconds
            self.put("state", "lease", lease, db)

    def update_job(self, run, job):
        with self.db(True) as db:
            self.assert_lease(run, db)
            db.execute("UPDATE jobs SET payload=? WHERE id=?", (encoded(job), job["id"]))

    def finish(self, run, status):
        with self.db(True) as db:
            self.assert_lease(run, db)
            current = self.get("runs", run["id"], db)
            if current["status"] != "running": return
            current.update(status=status, finished_at=self.clock())
            self.put("runs", run["id"], current, db)
            lease = self.get("state", "lease", db)
            lease["expires"] = 0
            self.put("state", "lease", lease, db)

    def abort(self, run, status):
        with self.db(True) as db:
            lease = self.get("state", "lease", db) or {}
            if lease.get("run_id") != run["id"] or lease.get("fence") != run["fence"]:
                return
            current = self.get("runs", run["id"], db)
            if current["status"] != "running": return
            current.update(status=status, finished_at=self.clock())
            self.put("runs", run["id"], current, db)
            lease["expires"] = 0
            self.put("state", "lease", lease, db)

    def version(self, version_id):
        value = self.get("versions", version_id)
        if value is None or value["id"] != digest({"kind": value["kind"], "data": value["data"]}):
            raise KBError("VERSION_INTEGRITY_FAILURE")
        return value

    def publish(self, kind, data, expected_base, actor, run=None, db=None):
        if kind not in ("documents", "rules"):
            raise ValueError("Unknown version kind")
        if db is None:
            with self.db(True) as connection:
                return self.publish(kind, data, expected_base, actor, run, connection)
        if run:
            self.assert_lease(run, db)
            if kind != "documents":
                raise KBError("HUMAN_REVIEW_REQUIRED")
        elif kind == "documents":
            raise KBError("SERVICE_LEASE_REQUIRED")
        if self.get("state", "active_"+kind, db) != expected_base:
            raise KBError("BASE_VERSION_CHANGED")
        version_id = digest({"kind": kind, "data": data})
        existing = self.get("versions", version_id, db)
        if existing is None:
            value = {"id": version_id, "kind": kind, "data": data, "base": expected_base, "created_at": self.clock()}
            db.execute("INSERT INTO versions(id,kind,payload) VALUES (?,?,?)", (version_id, kind, encoded(value)))
        self.put("state", "active_"+kind, version_id, db)
        self.audit("publish_"+kind, {"version": version_id, "base": expected_base, "actor": actor}, db)
        return version_id

    def rollback(self, kind, target, expected_base, actor):
        with self.db(True) as db:
            version = self.get("versions", target, db)
            if not version or version["kind"] != kind or target != digest({"kind": kind, "data": version["data"]}):
                raise KBError("VERSION_NOT_FOUND")
            if self.get("state", "active_"+kind, db) != expected_base:
                raise KBError("BASE_VERSION_CHANGED")
            self.put("state", "active_"+kind, target, db)
            self.audit("rollback", {"kind": kind, "from": expected_base, "to": target, "actor": actor}, db)
