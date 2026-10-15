"""One normal robots recheck after an earlier timeout, never a denial bypass.

The prior state must be a timeout without a document request. A successful
current policy check can enable the existing source for private candidates;
the standard worker checks robots again before requesting the PDF.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.robotparser import RobotFileParser

from gov_mcp.knowledge import KnowledgeStore, fingerprint
from gov_mcp.knowledge_fetch import Fetcher, AccessStopped, schedule, worker_once


ROOT = Path(__file__).resolve().parents[1]
SOURCE = "ID-NORM-01"


def main():
    store = KnowledgeStore(ROOT / ".runtime/knowledge-update")
    with store.db() as db:
        row = db.execute("SELECT * FROM sites WHERE id=?", (SOURCE,)).fetchone()
        config = json.loads(row["config"])
        if (config["origin"] != "https://www.xuchang.gov.cn" or config["enabled"]
                or "timed out" not in (row["paused_reason"] or "")
                or "no PDF requested" not in row["paused_reason"]):
            raise RuntimeError("Only the recorded, unrequested provincial timeout can be rechecked here")
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "source_id": SOURCE,
              "previous_pause": row["paused_reason"], "user_agent_changed": False,
              "proxy_or_alternate_identity_used": False, "source_enabled": False,
              "document_requested": False, "business_review_completed": False}
    try:
        status, headers, raw, final = Fetcher(store)._get(config, config["origin"]+"/robots.txt", robots=True)
        report["robots"] = {"status": status, "sha256": fingerprint(raw), "bytes": len(raw), "url": final,
                            "content_type": headers.get("content-type")}
        if status not in (200, 404, 410) or status == 200 and re.search(br"<!doctype\s+html|<html\b", raw, re.I):
            raise AccessStopped("Current robots response is not an interpretable policy or absence response")
        policy = RobotFileParser()
        if status == 200:
            policy.parse(raw.decode("utf-8-sig").splitlines())
            from gov_mcp.knowledge_fetch import BOT
            if not policy.can_fetch(BOT, config["seeds"][0]):
                raise AccessStopped("Current robots disallows this source")
        basis = ("Official administrative normative text, private candidate archive and attributed excerpts only; "
                 "Copyright Law Article 5(1) technical screen, no website design/media redistribution. "
                 "https://www.ncac.gov.cn/xxfb/flfg/flfg_532/202103/t20210309_50530.html")
        config["enabled"] = True
        config["metadata"].update(usage_basis=basis, document_number="豫公通〔2022〕77号", issuer="河南省公安厅")
        config["access_review"] = {"approved": True, "actor_kind": "machine", "reviewer": "Codex AI technical access screen",
            "checked_at": datetime.now(timezone.utc).isoformat(), "usage_basis": basis,
            "purpose": "private_engineering_candidates_only", "business_review_completed": False,
            "robots_url": final, "robots_sha256": fingerprint(raw),
            "screening_note": "Prior failure was a timeout without a PDF request; current normal robots recheck succeeded. Every worker request rechecks access rules."}
        store.register(config)
        store.resume(SOURCE, confirmed=True)
        report["source_enabled"] = True
        schedule(store)
        # One worker attempt only; later retries use the ordinary persisted queue.
        fetcher = Fetcher(store)
        original_request = fetcher._request
        def observed_request(url, headers):
            if not url.endswith("/robots.txt"):
                report["document_requested"] = True
            return original_request(url, headers)
        fetcher._request = observed_request
        worker_once(store, fetcher=fetcher, owner="provincial-access-recheck")
    except (OSError, ValueError) as error:
        report["recheck_error"] = str(error)[:300]
    with store.db() as db:
        jobs = [dict(row) for row in db.execute("SELECT url,state,attempts,error FROM jobs WHERE site_id=?", (SOURCE,))]
        revisions = [dict(row) for row in db.execute("SELECT r.id,r.sha256,r.state FROM revisions r JOIN documents d ON d.id=r.document_id WHERE d.site_id=?", (SOURCE,))]
        report["document_request_observation_scope"] = "this script only; concurrent normal workers may have separate requests"
        report.update(jobs=jobs, revisions=revisions)
        configs = [json.loads(row[0]) for row in db.execute("SELECT config FROM sites ORDER BY id")]
        db.execute("UPDATE workers SET progress='completed' WHERE id='provincial-access-recheck'")
    (store.root / "sources.json").write_text(json.dumps({"sources": configs}, ensure_ascii=False, indent=2), encoding="utf-8")
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["counts"] = store.status()["counts"]
    (ROOT / "evidence/knowledge-update/provincial-access-recheck.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
