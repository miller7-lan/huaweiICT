"""Publisher-bound PDF link discovery; no origin permissions inherited from a link."""

import json
import re
from urllib.parse import urldefrag, urljoin, urlsplit

from gov_mcp.knowledge import encode


def source_ids(values):
    return (isinstance(values, list) and 1 <= len(values) <= 10
            and all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) for value in values)
            and len(set(values)) == len(values))


def validate_attachments(config):
    if type(config.get("attachment_only", False)) is not bool:
        raise ValueError("Explicit boolean attachment collector mode required")
    if config.get("attachment_only"):
        meta = config["metadata"]
        if (config["seeds"] != [] or config.get("page_kind") != "document" or config.get("discover_links")
                or not source_ids(config.get("attachment_parent_sources"))
                or meta.get("region") != "unknown" or meta.get("matter") != "unclassified"
                or meta.get("scope_confirmed") is not False or meta.get("document_number")
                or any(meta.get(key) for key in ("effective_from", "effective_to", "published_at"))
                or meta.get("document_type") != "official_normative_attachment_candidate"
                or set(meta) - {"title", "region", "matter", "document_type", "scope_confirmed", "effective_from", "effective_to", "published_at",
                                "usage_basis", "business_review", "collection_role", "selected_content_scope"}
                or config.get("attachment_url_layout") != "henan_typtfile_pdf"
                or type(config.get("attachment_max_pending", 20)) is not int or not 1 <= config.get("attachment_max_pending", 20) <= 100):
            raise ValueError("Bounded attachment-only collector with unknown scope and trusted parent sources required")
    discovery = config.get("attachment_discovery")
    if discovery is None:
        return
    if (not isinstance(discovery, dict) or set(discovery) != {"sources", "owners", "max_links"}
            or not source_ids(discovery["sources"]) or config["id"] in discovery["sources"]
            or type(discovery["max_links"]) is not int or not 1 <= discovery["max_links"] <= 20
            or not isinstance(discovery["owners"], dict) or len(discovery["owners"]) > 100
            or not config.get("content_selector") or config.get("attachment_only")):
        raise ValueError("Explicit bounded HTML attachment discovery configuration required")
    if config["metadata"].get("document_type") not in {"official_policy_candidate", "provincial_normative_document", "administrative_norm", "synthetic"}:
        raise ValueError("Automatic attachment use screen is limited to declared normative-file parents")
    if config["metadata"].get("document_type") == "synthetic" and config.get("access_review", {}).get("actor_kind") != "synthetic":
        raise ValueError("Synthetic attachment parent requires an explicit engineering fixture actor")
    for url, owner in discovery["owners"].items():
        if not isinstance(url, str) or len(url) > 2048 or not isinstance(owner, dict):
            raise ValueError("Exact attachment URL owner required")
        target = urlsplit(url)
        if (target.scheme != "https" or not target.hostname or target.username or target.password or target.query or target.fragment
                or not source_ids([owner.get("source_id")]) or owner.get("source_id") == config["id"]
                or owner.get("policy") not in ("monitor", "excluded_history")
                or set(owner) != ({"source_id", "policy"} if owner["policy"] == "monitor" else {"source_id", "policy", "sha256"})
                or owner["policy"] == "excluded_history" and not re.fullmatch(r"[0-9a-f]{64}", str(owner.get("sha256")))):
            raise ValueError("Reviewed exact monitored or fingerprinted historical attachment owner required")


def dormant_collector(db, config):
    return bool(config.get("attachment_only") and not db.execute("SELECT 1 FROM jobs WHERE site_id=?", (config["id"],)).fetchone()
                and not db.execute("SELECT 1 FROM documents WHERE site_id=?", (config["id"],)).fetchone())


def dependencies(db, roots):
    result = set()
    for source_id in roots:
        row = db.execute("SELECT config FROM sites WHERE id=?", (source_id,)).fetchone()
        if row:
            discovery = json.loads(row[0]).get("attachment_discovery", {})
            result.update(discovery.get("sources", []))
            result.update(owner["source_id"] for owner in discovery.get("owners", {}).values())
    return result


def choose_source(store, db, parent_config, url):
    from gov_mcp.knowledge_fetch import allowed_url, validate_config
    discovery = parent_config["attachment_discovery"]
    owner = discovery["owners"].get(url)
    ids = [owner["source_id"]] if owner else discovery["sources"]
    candidates = []
    for source_id in ids:
        row = db.execute("SELECT * FROM sites WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise ValueError("Declared attachment source missing")
        config = json.loads(row["config"])
        if urlsplit(url).scheme != urlsplit(config["origin"]).scheme or urlsplit(url).netloc.lower() != urlsplit(config["origin"]).netloc.lower():
            continue
        allowed_url(config, url)
        if owner and owner["policy"] == "excluded_history":
            latest = db.execute("SELECT r.* FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=? AND d.url=? ORDER BY r.rowid DESC LIMIT 1", (source_id, url)).fetchone()
            if (config["enabled"] or config["metadata"].get("collection_role") != "governance_reference"
                    or not latest or latest["state"] != "excluded" or latest["sha256"] != owner["sha256"]):
                raise ValueError("Historical attachment exclusion no longer matches its explicit archive binding")
            store._verify(db, latest["id"], require_review=False)
            return config, "excluded_history_not_monitored"
        validate_config(config)
        if not config["enabled"] or row["paused_reason"]:
            raise ValueError("Attachment source disabled or paused; discovery cannot resume it")
        if any(scope not in config.get("watch_scopes", []) for scope in parent_config.get("watch_scopes", [])):
            raise ValueError("Attachment source must watch the parent's potential-impact scopes")
        if owner:
            from gov_mcp.knowledge_discovery import configured_kind
            if (url not in config["seeds"] or configured_kind(config, url) != "document"
                    or not db.execute("SELECT 1 FROM documents WHERE site_id=? AND url=?", (source_id, url)).fetchone()):
                raise ValueError("Existing attachment owner requires its exact monitored seed and archive")
        elif (not config.get("attachment_only") or parent_config["id"] not in config["attachment_parent_sources"]
                or not re.fullmatch(r"/typtfile/[0-9]{8}/[0-9a-fA-F]{32}\.pdf", urlsplit(url).path)):
            raise ValueError("Attachment parent or PDF storage layout outside reviewed policy")
        candidates.append((config, "monitored_owner" if owner else "collector"))
    if len(candidates) != 1:
        raise ValueError("Exactly one reviewed attachment source required; origin is not inherited from the publisher")
    return candidates[0]


def stable_parent_signal(store, db, parent, signal, previous):
    """Keep the observed parent binding across contiguous internal dispositions.

    The current edge/audit still names the actual processed revision. A previous
    observation is reusable only through verified, source-identical history;
    an intervening different original, body or page observation ends the run.
    """
    if not isinstance(previous, dict) or not isinstance(previous.get("hints"), dict):
        return signal
    previous_revision = previous["hints"].get("parent_revision")
    if not isinstance(previous_revision, str) or not previous_revision or len(previous_revision) > 80:
        return signal
    candidate = dict(signal, hints=dict(signal["hints"], parent_revision=previous_revision))
    if candidate != previous or candidate == signal:
        return signal
    observation = json.loads(parent["metadata"]).get("source_observation")
    history = db.execute("SELECT * FROM revisions WHERE document_id=? AND rowid<=(SELECT rowid FROM revisions WHERE id=?) "
                         "ORDER BY rowid DESC", (parent["document_id"], parent["id"]))
    for row in history:
        if (row["sha256"] != parent["sha256"] or row["body_sha256"] != parent["body_sha256"]
                or json.loads(row["metadata"]).get("source_observation") != observation):
            break
        store._verify(db, row["id"], require_review=False)
        if row["id"] == previous_revision:
            return candidate
    return signal


def discover_attachments(store, config, revision, raw, now):
    from gov_mcp.knowledge_fetch import ArticleParser
    from gov_mcp.knowledge_discovery import document_discovery_signal
    parser = ArticleParser(config["content_selector"], config.get("excluded_content_selectors"))
    parser.feed(raw.decode("utf-8", errors="strict"))
    with store.db() as db:
        parent, _ = store._verify(db, revision, require_review=False)
        document = db.execute("SELECT * FROM documents WHERE id=?", (parent["document_id"],)).fetchone()
        if document["site_id"] != config["id"] or json.loads(parent["metadata"])["source_observation"]["media_type"] != "text/html":
            raise ValueError("Verified HTML parent required for attachment discovery")
    entries = {}
    for link in parser.selected_link_records:
        url = urldefrag(urljoin(document["url"], link["url"]))[0]
        if urlsplit(url).path.lower().endswith(".pdf"):
            entries.setdefault(url, link["title"][:300] or json.loads(parent["metadata"])["title"])
    if len(entries) > config["attachment_discovery"]["max_links"]:
        with store.db() as db:
            store.log(db, "attachment_discovery_limited", {"site": config["id"], "revision": revision, "links": len(entries)})
        raise ValueError("PDF links exceed the reviewed per-parent limit; do not silently omit attachments")
    errors, outcomes = [], {}
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        # Omission is a signal for review, never an automatic repeal of a file.
        db.execute("UPDATE attachment_edges SET status='not_listed' WHERE parent_document_id=?", (parent["document_id"],))
        for url, title in entries.items():
            target_id, error = None, None
            try:
                target = urlsplit(url)
                if len(url) > 2048 or target.query or target.username or target.password:
                    raise ValueError("Parameterized or credential-bearing attachment needs a separate review")
                child, status = choose_source(store, db, config, url)
                target_id = child["id"]
                if status != "excluded_history_not_monitored":
                    signal = {"site": target_id, "url": url, "title": title, "discovered_from": document["url"], "discovery_format": "attachment",
                        "hints": {"attachment_parent_source": config["id"], "parent_revision": revision, "parent_original_sha256": parent["sha256"],
                            "parent_date_observations": {key: values for key, values in json.loads(parent["metadata"])["source_observation"].get("fields", {}).items()
                                                         if key in ("meta:pubdate", "block:page_date")}}}
                    previous = document_discovery_signal(db, child, url)
                    # Keep the first publisher binding stable across equivalent
                    # links from multiple parents; every edge remains recorded.
                    canonical = not previous or previous.get("discovered_from") == document["url"]
                    if canonical:
                        signal = stable_parent_signal(store, db, parent, signal, previous)
                    changed = canonical and previous != signal
                    known = db.execute("SELECT state FROM jobs WHERE site_id=? AND url=?", (target_id, url)).fetchone()
                    pending = db.execute("SELECT COUNT(*) FROM jobs WHERE site_id=? AND state IN ('queued','running')", (target_id,)).fetchone()[0]
                    if not known and pending >= child.get("attachment_max_pending", 20):
                        raise ValueError("Attachment pending-job budget reached; persisted link needs a later retry")
                    if changed:
                        store.log(db, "document_link_discovered", signal)
                        db.execute("UPDATE sites SET last_success=NULL WHERE id=?", (target_id,))
                    if not known or changed:
                        db.execute("INSERT INTO jobs(site_id,url,due) VALUES(?,?,?) ON CONFLICT(site_id,url) DO UPDATE SET state='queued',attempts=0,due=excluded.due,error=NULL WHERE jobs.state IN ('done','failed')", (target_id, url, now))
                    status = status if known else "new_pdf_queued"
            except ValueError as failure:
                status, error = "needs_source_review", str(failure)[:250]
                errors.append(error)
            db.execute("INSERT INTO attachment_edges VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(parent_document_id,target_url) DO UPDATE SET parent_revision=excluded.parent_revision,target_site_id=excluded.target_site_id,link_title=excluded.link_title,status=excluded.status,observed_at=excluded.observed_at,error=excluded.error",
                (parent["document_id"], url, revision, target_id, title, status, now, error))
            outcomes[status] = outcomes.get(status, 0)+1
        store.log(db, "attachment_links_checked", {"site": config["id"], "revision": revision, "parent_url": document["url"], "links": len(entries), "outcomes": outcomes})
    if errors:
        raise ValueError("Attachment discovery incomplete: "+errors[0])


def attachment_status(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='attachment_edges'").fetchone():
        return []
    return [{"source_id": row["site_id"], "parent_url": row["url"], "statuses": json.loads(row["statuses"]),
             "active_jobs": db.execute("SELECT COUNT(DISTINCT j.id) FROM attachment_edges e JOIN jobs j ON j.site_id=e.target_site_id AND j.url=e.target_url WHERE e.parent_document_id=? AND j.state IN ('queued','running')", (row["document_id"],)).fetchone()[0],
             "failed_jobs": db.execute("SELECT COUNT(DISTINCT j.id) FROM attachment_edges e JOIN jobs j ON j.site_id=e.target_site_id AND j.url=e.target_url WHERE e.parent_document_id=? AND j.state='failed'", (row["document_id"],)).fetchone()[0]}
            for row in db.execute("SELECT d.id AS document_id,d.site_id,d.url,json_group_object(a.status,a.total) AS statuses FROM (SELECT parent_document_id,status,COUNT(*) AS total FROM attachment_edges GROUP BY parent_document_id,status) a JOIN documents d ON d.id=a.parent_document_id GROUP BY d.id ORDER BY d.site_id,d.url")]
