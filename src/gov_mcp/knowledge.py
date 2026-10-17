"""Isolated, revisioned public-document candidates. Publication is explicit local maintenance."""

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encode(value).encode()).hexdigest()


class KnowledgeStore:
    """Never opens the original/M1/M2 databases; all mutations use a dedicated marker."""

    def __init__(self, runtime, read_only=False):
        self.root = Path(runtime).resolve()
        marker = json.loads((self.root / "phase.json").read_text(encoding="utf-8"))
        if marker != {"phase": "knowledge_update", "schema_version": 1}:
            raise ValueError("Dedicated knowledge_update runtime required")
        self.path = self.root / "knowledge.sqlite3"
        self.read_only = read_only
        if read_only:
            if not self.path.is_file():
                raise ValueError("Prepared knowledge database required")
            return
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sites(
                    id TEXT PRIMARY KEY, config TEXT NOT NULL, next_check REAL NOT NULL DEFAULT 0,
                    last_success REAL, last_request REAL, failures INTEGER NOT NULL DEFAULT 0,
                    paused_reason TEXT);
                CREATE TABLE IF NOT EXISTS documents(
                    id TEXT PRIMARY KEY, site_id TEXT NOT NULL REFERENCES sites(id),
                    url TEXT NOT NULL, title TEXT NOT NULL, UNIQUE(site_id,url));
                CREATE TABLE IF NOT EXISTS revisions(
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    sha256 TEXT NOT NULL, body_sha256 TEXT NOT NULL, metadata TEXT NOT NULL,
                    created_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending_review',
                    UNIQUE(document_id,body_sha256,metadata));
                CREATE TABLE IF NOT EXISTS chunks(
                    revision_id TEXT NOT NULL REFERENCES revisions(id), ordinal INTEGER NOT NULL,
                    locator TEXT NOT NULL, category TEXT NOT NULL, text TEXT NOT NULL,
                    PRIMARY KEY(revision_id,ordinal));
                CREATE TABLE IF NOT EXISTS reviews(
                    revision_id TEXT PRIMARY KEY REFERENCES revisions(id), reviewer TEXT NOT NULL,
                    digest TEXT NOT NULL, reviewed_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS exclusions(
                    revision_id TEXT PRIMARY KEY REFERENCES revisions(id), reviewer TEXT NOT NULL,
                    reason TEXT NOT NULL, digest TEXT NOT NULL, reviewed_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS versions(
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, members TEXT NOT NULL,
                    parent TEXT, created_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'candidate');
                CREATE TABLE IF NOT EXISTS heads(scope TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id));
                CREATE TABLE IF NOT EXISTS snapshot_evaluations(
                    id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id),
                    report TEXT NOT NULL, report_sha256 TEXT NOT NULL, reviewer TEXT NOT NULL,
                    recorded_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS evaluation_development_questions(
                    question_sha256 TEXT PRIMARY KEY, source TEXT NOT NULL, registered_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS classification_proposals(
                    revision_id TEXT NOT NULL REFERENCES revisions(id), taxonomy_version TEXT NOT NULL,
                    suggestions TEXT NOT NULL, sha256 TEXT NOT NULL, created_at REAL NOT NULL,
                    PRIMARY KEY(revision_id,taxonomy_version));
                CREATE TABLE IF NOT EXISTS jobs(
                    id INTEGER PRIMARY KEY, site_id TEXT NOT NULL REFERENCES sites(id),
                    url TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                    due REAL NOT NULL DEFAULT 0, lease_until REAL, owner TEXT,
                    error TEXT, UNIQUE(site_id,url));
                CREATE TABLE IF NOT EXISTS resources(
                    site_id TEXT NOT NULL, url TEXT NOT NULL, etag TEXT, modified TEXT,
                    checked_at REAL NOT NULL, PRIMARY KEY(site_id,url));
                CREATE TABLE IF NOT EXISTS discovery_cycles(
                    id INTEGER PRIMARY KEY, site_id TEXT NOT NULL REFERENCES sites(id),
                    started_at REAL NOT NULL, limited INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS discovery_targets(
                    site_id TEXT NOT NULL REFERENCES sites(id), url TEXT NOT NULL,
                    kind TEXT NOT NULL, cycle_id INTEGER NOT NULL REFERENCES discovery_cycles(id),
                    depth INTEGER NOT NULL, PRIMARY KEY(site_id,url));
                CREATE TABLE IF NOT EXISTS discovery_visits(
                    cycle_id INTEGER NOT NULL REFERENCES discovery_cycles(id), url TEXT NOT NULL,
                    kind TEXT NOT NULL, depth INTEGER NOT NULL, is_new INTEGER NOT NULL,
                    PRIMARY KEY(cycle_id,url));
                CREATE TABLE IF NOT EXISTS discovery_edges(
                    site_id TEXT NOT NULL REFERENCES sites(id), parent_url TEXT NOT NULL,
                    target_url TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
                    hints TEXT NOT NULL, ordinal INTEGER NOT NULL, observed_at REAL NOT NULL,
                    PRIMARY KEY(site_id,parent_url,target_url));
                CREATE TABLE IF NOT EXISTS discovery_index_state(
                    site_id TEXT NOT NULL REFERENCES sites(id), url TEXT NOT NULL,
                    parser_truncated INTEGER NOT NULL, entry_count INTEGER NOT NULL,
                    checked_at REAL NOT NULL, PRIMARY KEY(site_id,url));
                CREATE TABLE IF NOT EXISTS attachment_edges(
                    parent_document_id TEXT NOT NULL REFERENCES documents(id), target_url TEXT NOT NULL,
                    parent_revision TEXT NOT NULL REFERENCES revisions(id), target_site_id TEXT REFERENCES sites(id),
                    link_title TEXT NOT NULL, status TEXT NOT NULL, observed_at REAL NOT NULL, error TEXT,
                    PRIMARY KEY(parent_document_id,target_url));
                CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY, heartbeat REAL NOT NULL, progress TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(
                    id INTEGER PRIMARY KEY, at REAL NOT NULL, action TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS due_jobs ON jobs(state,due,lease_until);
                CREATE INDEX IF NOT EXISTS source_check_failures ON audit(json_extract(data,'$.site'),id DESC)
                    WHERE action='check_failed';
            """)

    @classmethod
    def initialize(cls, runtime):
        root = Path(runtime).resolve()
        if root.exists() and any(root.iterdir()) and not (root / "phase.json").exists():
            raise ValueError("Refusing to initialize a nonempty unrelated directory")
        root.mkdir(parents=True, exist_ok=True)
        marker = root / "phase.json"
        if not marker.exists():
            marker.write_text(encode({"phase": "knowledge_update", "schema_version": 1}), encoding="utf-8")
        policy = root / "publication-policy.json"
        if not policy.exists():
            from gov_mcp.knowledge_quality import BUSINESS_POLICY
            policy.write_text(encode(BUSINESS_POLICY), encoding="utf-8")
        return cls(root)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path.as_uri()+"?mode=ro", uri=True, timeout=5) if self.read_only else sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def log(db, action, data):
        db.execute("INSERT INTO audit(at,action,data) VALUES(?,?,?)", (time.time(), action, encode(data)))

    def scope_regions(self, region):
        path = self.root / "scope-regions.json"
        configured = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return set(configured.get(region, [])) | {region, "national"}

    def resume(self, site_id, confirmed=False):
        if confirmed is not True:
            raise ValueError("Explicit source-resume confirmation required")
        from gov_mcp.knowledge_fetch import validate_config
        with self.db() as db:
            row = db.execute("SELECT config FROM sites WHERE id=?", (site_id,)).fetchone()
            if not row:
                raise ValueError("Source not found")
            config = json.loads(row[0])
            validate_config(config)
            if not config["enabled"]:
                raise ValueError("Enable a reviewed source configuration first")
            db.execute("UPDATE sites SET paused_reason=NULL,next_check=0 WHERE id=?", (site_id,))
            self.log(db, "source_resumed", {"id": site_id})

    def register(self, config):
        from gov_mcp.knowledge_fetch import validate_config
        validate_config(config)
        with self.db() as db:
            from gov_mcp.knowledge_discovery import configured_kind
            for url in set(config["seeds"]) | set(config.get("index_urls", [])):
                target = db.execute("SELECT kind FROM discovery_targets WHERE site_id=? AND url=?", (config["id"], url)).fetchone()
                if target and target[0] != configured_kind(config, url):
                    raise ValueError("Changing an observed target role requires a separately reviewed source id")
            previous = db.execute("SELECT config FROM sites WHERE id=?", (config["id"],)).fetchone()
            db.execute("INSERT INTO sites(id,config) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config",
                       (config["id"], encode(config)))
            if previous and previous["config"] != encode(config):
                # Reconfiguration cannot reuse conditional validators or completed discovery jobs.
                db.execute("DELETE FROM resources WHERE site_id=?", (config["id"],))
                db.execute("UPDATE sites SET next_check=0 WHERE id=?", (config["id"],))
            self.log(db, "source_registered", {"id": config["id"], "enabled": config["enabled"]})

    def ingest(self, site_id, url, raw, metadata, chunks, preserve_metadata_if_unchanged=False):
        required = {"title", "region", "matter", "document_type", "effective_from", "effective_to", "usage_basis"}
        if not required <= metadata.keys() or not chunks or not metadata["title"].strip():
            raise ValueError("Incomplete document")
        for field in ("published_at", "effective_from", "effective_to"):
            if metadata.get(field):
                date.fromisoformat(metadata[field])
        if metadata["effective_to"] and metadata["effective_from"] and metadata["effective_to"] <= metadata["effective_from"]:
            raise ValueError("Invalid effective period")
        if any(not c["text"].strip() or not c["locator"].strip() or not c["category"] for c in chunks):
            raise ValueError("Empty or unlocated chunk")
        content = [{k: c[k] for k in ("locator", "category", "text")} for c in chunks]
        sha, body = fingerprint(raw), fingerprint(content)
        document_id = "doc-" + fingerprint([site_id, url])[:24]
        revision_id = "rev-" + fingerprint([document_id, body, metadata])[:24]
        directory = self.root / "originals" / sha[:2]
        directory.mkdir(parents=True, exist_ok=True)
        original = directory / sha
        # Exclusive content-addressed write; never replace an existing original.
        try:
            with original.open("xb") as stream:
                stream.write(raw)
        except FileExistsError:
            if fingerprint(original.read_bytes()) != sha:
                raise ValueError("Stored original is corrupted")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM sites WHERE id=?", (site_id,)).fetchone():
                raise ValueError("Unregistered source")
            db.execute("INSERT OR IGNORE INTO documents VALUES(?,?,?,?)", (document_id, site_id, url, metadata["title"]))
            latest = db.execute("SELECT * FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (document_id,)).fetchone()
            if latest and latest["body_sha256"] == body:
                previous_metadata = json.loads(latest["metadata"])
                previous_metadata.pop("_observed_after_revision", None)
                observations_match = previous_metadata.get("source_observation") == metadata.get("source_observation")
                if preserve_metadata_if_unchanged and observations_match or previous_metadata == metadata:
                    self.log(db, "unchanged_body", {"revision": latest["id"], "observed_raw_sha256": sha})
                    return latest["id"]
            existing = db.execute("SELECT id FROM revisions WHERE id=?", (revision_id,)).fetchone()
            if existing and latest and latest["id"] != revision_id:
                # A website can revert to earlier text; preserve that new observation in chronology.
                metadata = dict(metadata, _observed_after_revision=latest["id"])
                revision_id = "rev-" + fingerprint([document_id, body, metadata])[:24]
                existing = db.execute("SELECT id FROM revisions WHERE id=?", (revision_id,)).fetchone()
            if existing:
                self.log(db, "unchanged_body", {"revision": revision_id, "observed_raw_sha256": sha})
                return revision_id
            db.execute("INSERT INTO revisions(id,document_id,sha256,body_sha256,metadata,created_at) VALUES(?,?,?,?,?,?)",
                       (revision_id, document_id, sha, body, encode(metadata), time.time()))
            db.executemany("INSERT INTO chunks VALUES(?,?,?,?,?)", [(revision_id, i, c["locator"], c["category"], c["text"])
                                                                    for i, c in enumerate(content)])
            self.log(db, "revision_ingested", {"revision": revision_id, "sha256": sha, "chunks": len(chunks)})
        return revision_id

    def _content(self, db, revision_id):
        row = db.execute("SELECT * FROM revisions WHERE id=?", (revision_id,)).fetchone()
        if not row:
            raise ValueError("Revision not found")
        chunks = [dict(c) for c in db.execute("SELECT locator,category,text FROM chunks WHERE revision_id=? ORDER BY ordinal", (revision_id,))]
        return dict(row), chunks

    def _verify(self, db, revision_id, require_review=True):
        row, chunks = self._content(db, revision_id)
        path = self.root / "originals" / row["sha256"][:2] / row["sha256"]
        if fingerprint(path.read_bytes()) != row["sha256"] or fingerprint(chunks) != row["body_sha256"]:
            raise ValueError("Content fingerprint mismatch")
        digest = fingerprint([row["sha256"], row["metadata"], chunks])
        if require_review:
            review = db.execute("SELECT digest FROM reviews WHERE revision_id=?", (revision_id,)).fetchone()
            if not review or review["digest"] != digest or row["state"] != "reviewed":
                raise ValueError("Unreviewed or changed content")
        return row, digest

    def review(self, revision_id, reviewer, confirmed=False):
        if confirmed is not True or not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("Explicit confirmation and real reviewer required")
        with self.db() as db:
            row, digest = self._verify(db, revision_id, require_review=False)
            if row["state"] == "excluded":
                raise ValueError("Excluded candidate must be explicitly reopened before review")
            meta = json.loads(row["metadata"])
            if meta["region"] in ("", "unknown", "unclassified") or meta["matter"] in ("", "unknown", "unclassified") or meta.get("scope_confirmed") is False:
                raise ValueError("Known region/matter and confirmed branch scope required before review")
            if not meta["usage_basis"] or meta.get("conflict", False):
                raise ValueError("Usage basis, effective date and resolved conflicts required before review")
            from gov_mcp.knowledge_dates import reviewed_start
            reviewed_start(self, db, row, meta)
            db.execute("INSERT INTO reviews VALUES(?,?,?,?) ON CONFLICT(revision_id) DO UPDATE SET reviewer=excluded.reviewer,digest=excluded.digest,reviewed_at=excluded.reviewed_at",
                       (revision_id, reviewer.strip(), digest, time.time()))
            db.execute("UPDATE revisions SET state='reviewed' WHERE id=?", (revision_id,))
            self.log(db, "revision_reviewed", {"revision": revision_id, "reviewer": reviewer.strip(), "digest": digest})

    def exclude(self, revision_id, reviewer, reason, confirmed=False):
        """Record a new disposition revision; published historical citations stay intact."""
        if confirmed is not True or not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("Explicit reviewer and exclusion reason required")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row, _ = self._verify(db, revision_id, require_review=False)
            latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()
            if latest[0] != revision_id or row["state"] == "excluded":
                raise ValueError("Exclude only the latest non-excluded revision")
            metadata = json.loads(row["metadata"])
            metadata["_excluded_after_revision"] = revision_id
            target = "rev-" + fingerprint([row["document_id"], row["body_sha256"], metadata])[:24]
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?)", (target, row["document_id"], row["sha256"], row["body_sha256"], encode(metadata), time.time(), "excluded"))
            db.execute("INSERT INTO chunks SELECT ?,ordinal,locator,category,text FROM chunks WHERE revision_id=?", (target, revision_id))
            _, digest = self._verify(db, target, require_review=False)
            db.execute("INSERT INTO exclusions VALUES(?,?,?,?,?)", (target, reviewer.strip(), reason.strip(), digest, time.time()))
            self.log(db, "revision_excluded", {"revision": target, "previous": revision_id, "reviewer": reviewer.strip(), "reason": reason.strip(), "digest": digest})
        return target

    def _verify_exclusion(self, db, revision):
        row, digest = self._verify(db, revision, require_review=False)
        decision = db.execute("SELECT * FROM exclusions WHERE revision_id=?", (revision,)).fetchone()
        if row["state"] != "excluded" or not decision or decision["digest"] != digest or not decision["reason"].strip():
            raise ValueError("Missing or changed exclusion decision")

    def reopen(self, revision_id, reviewer, confirmed=False):
        if confirmed is not True or not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("Explicit reviewer confirmation required")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_exclusion(db, revision_id)
            row, _ = self._content(db, revision_id)
            latest = db.execute("SELECT id FROM revisions WHERE document_id=? ORDER BY rowid DESC LIMIT 1", (row["document_id"],)).fetchone()
            if latest[0] != revision_id:
                raise ValueError("Reopen only the latest exclusion")
            metadata = json.loads(row["metadata"])
            metadata.pop("_excluded_after_revision", None)
            metadata["_reopened_after_revision"] = revision_id
            # Reopening does not restore the previous business approval or its effective dates.
            metadata.update(effective_from=None, effective_to=None, published_at=None)
            target = "rev-" + fingerprint([row["document_id"], row["body_sha256"], metadata])[:24]
            db.execute("INSERT INTO revisions VALUES(?,?,?,?,?,?,?)", (target, row["document_id"], row["sha256"], row["body_sha256"], encode(metadata), time.time(), "pending_review"))
            db.execute("INSERT INTO chunks SELECT ?,ordinal,locator,category,text FROM chunks WHERE revision_id=?", (target, revision_id))
            self.log(db, "revision_reopened", {"revision": target, "previous": revision_id, "reviewer": reviewer.strip()})
        return target

    def amend(self, revision_id, metadata):
        """Correct candidate metadata by making a new revision, never changing an old citation."""
        with self.db() as db:
            row, chunks = self._content(db, revision_id)
            if row["state"] == "excluded":
                raise ValueError("Excluded candidate must be explicitly reopened before amendment")
            doc = db.execute("SELECT site_id,url FROM documents WHERE id=?", (row["document_id"],)).fetchone()
        previous = json.loads(row["metadata"])
        if previous.get("source_observation") != metadata.get("source_observation"):
            raise ValueError("Source observation is immutable; correct interpreted fields without changing reported evidence")
        raw = (self.root / "originals" / row["sha256"][:2] / row["sha256"]).read_bytes()
        if fingerprint(raw) != row["sha256"]:
            raise ValueError("Stored original is corrupted")
        return self.ingest(doc["site_id"], doc["url"], raw, metadata, chunks)

    def _scope_members(self, db, region, matter):
        from gov_mcp.knowledge_discovery import watches_unclassified
        latest, grouped = {}, {}
        source_configs = {row["id"]: json.loads(row["config"]) for row in db.execute("SELECT d.id,s.config FROM documents d JOIN sites s ON s.id=d.site_id")}
        for row in db.execute("SELECT * FROM revisions ORDER BY rowid"):
            latest[row["document_id"]] = row
            grouped.setdefault(row["document_id"], []).append(row)
        def matches(row):
            meta = json.loads(row["metadata"])
            return meta["region"] in self.scope_regions(region) and meta["matter"] == matter
        members = []
        for document_id, row in latest.items():
            # Moving an existing scoped document away also requires a verified decision.
            watched = watches_unclassified(source_configs[document_id], json.loads(row["metadata"]), region, matter)
            if not watched and not any(matches(previous) for previous in grouped[document_id]):
                continue
            if row["state"] == "excluded":
                self._verify_exclusion(db, row["id"])
                continue
            if watched:
                raise ValueError("Potentially relevant discovery still needs scope classification or an exclusion decision")
            self._verify(db, row["id"])
            if matches(row):
                for previous in grouped[document_id]:
                    if previous["state"] == "reviewed" and matches(previous):
                        self._verify(db, previous["id"])
                        members.append(previous["id"])
        return sorted(members)

    def candidate(self, region, matter):
        scope = encode([region, matter])
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            members = self._scope_members(db, region, matter)
            head = db.execute("SELECT version_id FROM heads WHERE scope=?", (scope,)).fetchone()
            parent = head[0] if head else None
            if not members and not parent:
                raise ValueError("No reviewed knowledge for scope")
            if parent and db.execute("SELECT members FROM versions WHERE id=?", (parent,)).fetchone()[0] == encode(members):
                return parent
            version = "kb-" + fingerprint([scope, members, parent])[:24]
            db.execute("INSERT OR IGNORE INTO versions(id,scope,members,parent,created_at) VALUES(?,?,?,?,?)",
                       (version, scope, encode(members), parent, time.time()))
            self.log(db, "candidate_created", {"version": version, "member_count": len(members)})
            return version

    def publish(self, version, reviewer, confirmed=False):
        if confirmed is not True or not reviewer.strip():
            raise ValueError("Explicit maintainer confirmation required")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            candidate = db.execute("SELECT * FROM versions WHERE id=?", (version,)).fetchone()
            if not candidate:
                raise ValueError("Version not found")
            head = db.execute("SELECT version_id FROM heads WHERE scope=?", (candidate["scope"],)).fetchone()
            current = head[0] if head else None
            if current == version:
                return version
            if candidate["state"] != "candidate" or current != candidate["parent"]:
                raise ValueError("Stale candidate; rebuild against current publication")
            members = json.loads(candidate["members"])
            region, matter = json.loads(candidate["scope"])
            if members != self._scope_members(db, region, matter):
                raise ValueError("Scope changed after candidate preparation")
            from gov_mcp.knowledge_quality import verify_publication
            quality = verify_publication(self, db, version)
            db.execute("INSERT INTO heads VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET version_id=excluded.version_id", (candidate["scope"], version))
            db.execute("UPDATE versions SET state='published' WHERE id=?", (version,))
            self.log(db, "published", {"version": version, "previous": current, "maintainer": reviewer, "quality": quality})
        return version

    def evaluation_binding(self, version):
        from gov_mcp.knowledge_quality import binding
        with self.db() as db:
            return binding(self, db, version)

    def propose_classification(self, revision):
        from gov_mcp.knowledge_classify import suggest
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row, _ = self._verify(db, revision, require_review=False)
            _, chunks = self._content(db, revision)
            proposal = suggest(chunks, json.loads(row["metadata"]))
            sha = fingerprint(proposal)
            existing = db.execute("SELECT sha256 FROM classification_proposals WHERE revision_id=? AND taxonomy_version=?", (revision, proposal["taxonomy_version"])).fetchone()
            if existing and existing[0] != sha:
                raise ValueError("Changed classifier output requires a new taxonomy version")
            db.execute("INSERT OR IGNORE INTO classification_proposals VALUES(?,?,?,?,?)", (revision, proposal["taxonomy_version"], encode(proposal), sha, time.time()))
            if not existing:
                self.log(db, "classification_proposed", {"revision": revision, "taxonomy_version": proposal["taxonomy_version"], "sha256": sha, "business_review": False})
        return proposal

    def record_evaluation(self, version, report, reviewer, confirmed=False):
        if confirmed is not True or not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("Explicit real evaluator confirmation required")
        from gov_mcp.knowledge_quality import validate_report
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            metrics = validate_report(self, db, version, report)
            if metrics.get("review_contract") == "attributed_v2" and reviewer.strip() != report["reviewer"]["name"].strip():
                raise ValueError("Recorded evaluator must match the attributed reviewer")
            sha = fingerprint(report)
            identifier = "eval-" + fingerprint([version, sha, reviewer.strip()])[:24]
            db.execute("INSERT OR IGNORE INTO snapshot_evaluations VALUES(?,?,?,?,?,?)", (identifier, version, encode(report), sha, reviewer.strip(), time.time()))
            self.log(db, "evaluation_recorded", {"evaluation": identifier, "version": version, "reviewer": reviewer.strip(), "report_sha256": sha, "metrics": metrics})
        return {"evaluation": identifier, "metrics": metrics}

    def publication_check(self, version):
        from gov_mcp.knowledge_quality import verify_publication
        with self.db() as db:
            db.execute("BEGIN")
            candidate = db.execute("SELECT * FROM versions WHERE id=?", (version,)).fetchone()
            if not candidate:
                raise ValueError("Version not found")
            region, matter = json.loads(candidate["scope"])
            if json.loads(candidate["members"]) != self._scope_members(db, region, matter):
                raise ValueError("Scope changed after candidate preparation")
            return verify_publication(self, db, version)

    def rollback(self, version, maintainer, confirmed=False):
        if confirmed is not True or not maintainer.strip():
            raise ValueError("Explicit rollback confirmation required")
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM versions WHERE id=? AND state='published'", (version,)).fetchone()
            if not row:
                raise ValueError("Only a previously published version can be restored")
            for revision in json.loads(row["members"]):
                self._verify(db, revision)
            db.execute("UPDATE heads SET version_id=? WHERE scope=?", (version, row["scope"]))
            self.log(db, "rollback", {"version": version, "maintainer": maintainer})

    def search(self, query, region, matter, version=None, as_of=None, category=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise ValueError("Question must contain 1-300 characters")
        current_date = date.today()
        today = date.fromisoformat(as_of) if as_of else current_date
        date_view = {"query_date": today.isoformat(),
                     "query_view": "past" if today < current_date else "future" if today > current_date else "current"}
        scope = encode([region, matter])
        with self.db() as db:
            db.execute("BEGIN")
            head = db.execute("SELECT version_id FROM heads WHERE scope=?", (scope,)).fetchone()
            selected = version or (head[0] if head else None)
            snapshot = db.execute("SELECT * FROM versions WHERE id=? AND scope=? AND state='published'", (selected, scope)).fetchone()
            if not snapshot:
                return {"status": "EMPTY", "message_code": "NO_PUBLISHED_KNOWLEDGE", "knowledge_version": None, "citations": [], **date_view}
            from gov_mcp.knowledge_retrieval import query_grams, score_literal, top_five
            from gov_mcp.knowledge_answers import bind_citation
            from gov_mcp.knowledge_dates import reviewed_start, citation_date_fields
            grams = query_grams(query)
            ranked = []
            applicable = {}
            for revision in json.loads(snapshot["members"]):
                row, _ = self._verify(db, revision)
                meta = json.loads(row["metadata"])
                start = reviewed_start(self, db, row, meta)
                if date.fromisoformat(start) > today:
                    continue
                # Prefer the latest established temporal bound, then revision insertion.
                ordinal = db.execute("SELECT rowid FROM revisions WHERE id=?", (revision,)).fetchone()[0]
                order = (start, ordinal)
                if row["document_id"] not in applicable or order > applicable[row["document_id"]][0]:
                    applicable[row["document_id"]] = (order, revision, row, meta)
            for _, revision, row, meta in applicable.values():
                if meta["effective_to"] and today >= date.fromisoformat(meta["effective_to"]):
                    continue
                doc = db.execute("SELECT url,site_id FROM documents WHERE id=?", (row["document_id"],)).fetchone()
                for chunk in db.execute("SELECT * FROM chunks WHERE revision_id=?", (revision,)):
                    score = score_literal(grams, chunk["text"])
                    if score and (not category or chunk["category"] == category):
                        ranked.append((score, bind_citation({"revision_id": revision, "title": meta["title"], "url": doc["url"],
                            "locator": chunk["locator"], "category": chunk["category"], "quote": chunk["text"],
                            "published_at": meta.get("published_at"), "effective_from": meta["effective_from"],
                            "effective_to": meta["effective_to"], "original_sha256": row["sha256"], "chunk_ordinal": chunk["ordinal"],
                            "knowledge_version": selected, **citation_date_fields(meta)})))
            members = set(json.loads(snapshot["members"]))
            documents = {r[0] for r in db.execute("SELECT document_id FROM revisions WHERE id IN (" + ",".join("?" for _ in members) + ")", tuple(members))}
            pending, awaiting_publication = 0, 0
            from gov_mcp.knowledge_discovery import watches_unclassified
            for latest in db.execute("SELECT r.*,s.config AS source_config FROM revisions r JOIN documents d ON d.id=r.document_id JOIN sites s ON s.id=d.site_id WHERE r.rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id)"):
                meta = json.loads(latest["metadata"])
                matches = meta["region"] in self.scope_regions(region) and meta["matter"] == matter
                included = latest["document_id"] in documents
                watched = watches_unclassified(json.loads(latest["source_config"]), meta, region, matter)
                if not matches and not included and not watched:
                    continue
                if latest["state"] == "pending_review" or watched and latest["state"] != "excluded":
                    pending += 1
                elif latest["state"] == "excluded":
                    self._verify_exclusion(db, latest["id"])
                    awaiting_publication += int(included)
                else:
                    awaiting_publication += int(matches and latest["id"] not in members or not matches and included)
            citations = top_five(ranked)
            site_ids = {r[0] for r in db.execute("SELECT DISTINCT d.site_id FROM documents d JOIN revisions r ON r.document_id=d.id WHERE r.id IN (" + ",".join("?" for _ in json.loads(snapshot["members"])) + ")", json.loads(snapshot["members"]))}
            from gov_mcp.knowledge_discovery import watched_source_ids
            monitors = watched_source_ids(db, region, matter)
            site_ids.update(monitors)
            freshness = []
            for site_id in sorted(site_ids):
                site = db.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
                conf = json.loads(site["config"])
                freshness.append({"source_id": site_id, "last_successful_check": site["last_success"],
                    "stale": not site["last_success"] or time.time()-site["last_success"] > conf["interval_seconds"]+900,
                    "enabled": conf["enabled"], "paused": bool(site["paused_reason"]),
                    "failed_check": site["failures"] > 0, "potential_impact_monitor": site_id in monitors})
            historical = bool(head and selected != head[0])
            freshness_failed = any(source["stale"] or source["paused"] or source["failed_check"] or not source["enabled"] for source in freshness)
            blocked = bool((pending or awaiting_publication or freshness_failed) and not historical)
            return {"status": "BLOCKED" if blocked else "OK" if citations else "EMPTY", "knowledge_version": selected, "citations": [] if blocked else citations,
                    "update_available": bool(head and head[0] != selected), "pending_review_count": pending,
                    "awaiting_publication_count": awaiting_publication,
                    "historical_snapshot": historical, **date_view,
                    "source_freshness": freshness,
                    "message_code": ("UPDATE_PENDING_REVIEW" if pending else "UPDATE_AWAITING_PUBLICATION" if awaiting_publication else "SOURCE_CHECK_REQUIRED") if blocked else "EVIDENCE_FOUND" if citations else "NO_SUPPORTING_EVIDENCE"}

    def status(self):
        from gov_mcp.knowledge_discovery import discovery_status
        with self.db() as db:
            startup = {}
            for key, name in (("registration", "startup-registration.json"), ("recovery", "startup-recovery-status.json"), ("task", "startup-task-status.json")):
                path = self.root / name
                try:
                    startup[key] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
                    if startup[key] is not None and not isinstance(startup[key], dict):
                        raise ValueError("Startup observation must be an object")
                except (OSError, ValueError):
                    startup[key] = {"status": "unreadable"}
            from gov_mcp.knowledge_startup import startup_health
            startup["manual_pause"] = (self.root / "service-paused.json").exists()
            startup["health"] = startup_health(startup)
            counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                      for t in ("sites", "documents", "revisions", "chunks", "versions", "heads", "jobs")}
            counts["latest_chunks"] = db.execute("SELECT COUNT(*) FROM chunks WHERE revision_id IN (SELECT id FROM revisions WHERE rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id))").fetchone()[0]
            sites = []
            from gov_mcp.knowledge_attachments import attachment_status, dormant_collector
            for row in db.execute("SELECT * FROM sites ORDER BY id"):
                item = dict(row)
                config = json.loads(item.pop("config"))
                waiting = dormant_collector(db, config)
                item.update(enabled=config["enabled"], label=config["label"],
                            waiting_for_attachment=waiting,
                            stale=bool(config["enabled"] and not waiting and (not row["last_success"] or time.time()-row["last_success"] > config["interval_seconds"]+900)))
                item["access_stop"] = None
                if row["paused_reason"]:
                    failure = db.execute("SELECT at,data FROM audit WHERE action='check_failed' "
                                         "AND json_extract(data,'$.site')=? ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
                    detail = json.loads(failure["data"]).get("access_stop") if failure else None
                    if isinstance(detail, dict):
                        from gov_mcp.knowledge_fetch import AccessStopped
                        item["access_stop"] = AccessStopped("", phase=detail.get("phase"), http_status=detail.get("http_status")).detail()
                        item["access_stop"]["recorded_at"] = failure["at"]
                sites.append(item)
            return {"mode": "knowledge_update", "counts": counts, "sites": sites,
                    "startup": startup,
                    "discovery": discovery_status(db),
                    "attachments": attachment_status(db),
                    "pending_review": [dict(r) | {"metadata": json.loads(r["metadata"])} for r in db.execute("SELECT id,metadata,state FROM revisions WHERE state='pending_review' AND rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id)")],
                    "excluded_candidates": [dict(r) for r in db.execute("SELECT r.id,e.reviewer,e.reason,e.reviewed_at FROM revisions r JOIN exclusions e ON e.revision_id=r.id WHERE r.state='excluded' AND r.rowid IN (SELECT MAX(rowid) FROM revisions GROUP BY document_id)")],
                    "publications": [dict(r) for r in db.execute("SELECT * FROM heads")],
                    "workers": [dict(r) | {"stale": r["progress"] != "completed" and time.time()-r["heartbeat"] > (3600 if r["id"] == "knowledge-maintenance" else 300)} for r in db.execute("SELECT * FROM workers")],
                    "maintenance": json.loads((self.root / "maintenance-status.json").read_text(encoding="utf-8")) if (self.root / "maintenance-status.json").exists() else None,
                    "jobs": [dict(r) for r in db.execute("SELECT id,site_id,url,state,attempts,error FROM jobs ORDER BY id DESC LIMIT 50")]}

    def backup(self, target):
        target = Path(target).resolve()
        if target.exists() or target == self.path:
            raise ValueError("Backup target must be a new file")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        return {"database": str(target), "note": "Also retain originals/; database backup alone is not a full restore."}
