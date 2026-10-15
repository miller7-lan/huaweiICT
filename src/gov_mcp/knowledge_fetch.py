"""Bounded public-HTTP acquisition, explicit source policies and a durable scheduler."""

import email.utils
import http.client
import ipaddress
import json
import re
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from gov_mcp.knowledge import encode, fingerprint


BOT = "GovICTKnowledgeBot"
MAX_BYTES = 5 * 1024 * 1024


class AccessStopped(ValueError):
    pass


def validate_config(config):
    required = {"id", "label", "enabled", "origin", "allowed_paths", "seeds", "interval_seconds", "delay_seconds", "metadata"}
    if not required <= config.keys() or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", config["id"]):
        raise ValueError("Incomplete source configuration")
    origin = urlsplit(config["origin"])
    if origin.scheme not in ("https", "http") or not origin.hostname or origin.username or origin.password or origin.query or origin.fragment or origin.path not in ("", "/"):
        raise ValueError("Plain HTTP(S) origin required")
    if origin.port not in (None, 80, 443) or config["interval_seconds"] < 60 or config["delay_seconds"] < 5 or type(config["enabled"]) is not bool:
        raise ValueError("Invalid source limits")
    if not config["seeds"] or not config["allowed_paths"] or any(not p.startswith("/") or ".." in p or "?" in p for p in config["allowed_paths"]):
        raise ValueError("Explicit source paths and seeds required")
    for url in config["seeds"]:
        allowed_url(config, url)
    for url in config.get("index_urls", []):
        allowed_url(config, url)
    if type(config.get("discover_links", False)) is not bool:
        raise ValueError("Explicit discovery opt-in required")
    from gov_mcp.knowledge_discovery import validate_discovery
    validate_discovery(config)
    start_title = config.get("article_start_title")
    if start_title is not None and (not isinstance(start_title, str) or not start_title.strip() or len(start_title) > 200 or not config.get("include_articles")):
        raise ValueError("Explicit bounded consolidated-text title and article selection required")
    keywords = config.get("include_paragraph_keywords")
    if keywords is not None or "paragraph_expected_count" in config:
        expected = config.get("paragraph_expected_count", 1)
        if (not isinstance(keywords, list) or not 1 <= len(keywords) <= 8
                or any(not isinstance(word, str) or not word.strip() or len(word) > 100 for word in keywords)
                or type(expected) is not int or not 1 <= expected <= 20 or config.get("include_articles")):
            raise ValueError("Explicit bounded HTML paragraph selection required")
    excluded = config.get("excluded_content_selectors", [])
    if not isinstance(excluded, list) or len(excluded) > 8:
        raise ValueError("Bounded content exclusions required")
    for selector in excluded:
        if (not isinstance(selector, dict) or len(selector) != 1
                or next(iter(selector)) not in ("id", "class")
                or not isinstance(next(iter(selector.values())), str)
                or not next(iter(selector.values())).strip() or len(next(iter(selector.values()))) > 100
                or selector == config.get("content_selector")):
            raise ValueError("Explicit nested content id/class exclusion required")
    selectors = config.get("metadata_selectors", {})
    if not isinstance(selectors, dict) or len(selectors) > 16:
        raise ValueError("Bounded metadata selectors required")
    for name, selector in selectors.items():
        if not isinstance(name, str) or not name or len(name) > 80 or not isinstance(selector, dict) or len(selector) != 1:
            raise ValueError("Named metadata id/class selector required")
        key, value = next(iter(selector.items()))
        if key not in ("id", "class") or not isinstance(value, str) or not value.strip() or len(value) > 100:
            raise ValueError("Named metadata id/class selector required")
    if config["enabled"]:
        if config["metadata"].get("collection_role") in ("excluded_from_corpus", "governance_reference"):
            raise ValueError("Excluded leads cannot be enabled as business corpus")
        review = config.get("access_review", {})
        if review.get("approved") is not True or not review.get("reviewer") or not review.get("usage_basis"):
            raise ValueError("Access and usage review required before enabling")
        checked = datetime.fromisoformat(review.get("checked_at", ""))
        age = (datetime.now(timezone.utc) - checked.astimezone(timezone.utc)).total_seconds()
        if checked.tzinfo is None or age < 0 or age > 30 * 86400:
            raise ValueError("Source access review must be within 30 days")


def allowed_url(config, url, robots=False):
    if not isinstance(url, str) or re.search(r"[\x00-\x1f\x7f]", url):
        raise AccessStopped("Control characters in URL refused")
    target, origin = urlsplit(url), urlsplit(config["origin"])
    if target.username or target.password or target.fragment or target.scheme != origin.scheme or target.netloc.lower() != origin.netloc.lower():
        raise AccessStopped("URL outside approved origin")
    # Decode and normalize for the boundary check, rejecting ambiguous traversal encodings.
    from urllib.parse import unquote
    path = unquote(target.path)
    if "\\" in path or any(p in (".", "..") for p in path.split("/")) or "%" in path:
        raise AccessStopped("Ambiguous URL path")
    paths = [unquote(p) for p in config["allowed_paths"]]
    if not (robots and path == "/robots.txt") and not any(path == p or (p.endswith("/") and path.startswith(p)) for p in paths):
        raise AccessStopped("URL outside approved paths")
    return urlunsplit((target.scheme, target.netloc, target.path or "/", target.query, ""))


class Fetcher:
    def __init__(self, store):
        self.store = store

    def _request(self, url, headers):
        """Connect to the checked public IP, retaining the original TLS hostname and Host."""
        target = urlsplit(url)
        port = target.port or (443 if target.scheme == "https" else 80)
        addresses = socket.getaddrinfo(target.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise AccessStopped("Non-public or mixed DNS address refused")
        address = addresses[0][4][0]
        connection = http.client.HTTPConnection(target.hostname, port, timeout=15)
        try:
            connection.sock = socket.create_connection((address, port), timeout=15)
            if target.scheme == "https":
                connection.sock = ssl.create_default_context().wrap_socket(connection.sock, server_hostname=target.hostname)
            wire_path = quote(target.path or "/", safe="/%:@!$&'()*+,;=-._~")
            wire_query = quote(target.query, safe="/?%:@!$&'()*+,;=-._~")
            connection.request("GET", urlunsplit(("", "", wire_path, wire_query, "")),
                               headers={"User-Agent": BOT + "/0.1 (public-document research)", "Accept-Encoding": "identity", **headers})
            response = connection.getresponse()
            if response.length and response.length > MAX_BYTES:
                raise AccessStopped("Response too large")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise AccessStopped("Response too large")
            return response.status, {k.lower(): v for k, v in response.getheaders()}, raw
        finally:
            connection.close()

    def _get(self, config, url, headers=None, robots=False):
        for _ in range(4):
            url = allowed_url(config, url, robots=robots)
            # Per-origin reservation is persisted across worker restarts and sources on that origin.
            with self.store.db() as db:
                db.execute("BEGIN IMMEDIATE")
                previous = max((r[0] or 0 for r in db.execute("SELECT last_request FROM sites WHERE json_extract(config,'$.origin')=?", (config["origin"],))), default=0)
                reserved = max(time.time(), previous + config["delay_seconds"])
                db.execute("UPDATE sites SET last_request=? WHERE json_extract(config,'$.origin')=?", (reserved, config["origin"]))
            delay = reserved - time.time()
            if delay > 30:
                raise RetryLater(str(delay))
            if delay > 0:
                time.sleep(delay)
            status, response_headers, raw = self._request(url, headers or {})
            if status in (301, 302, 303, 307, 308):
                url = urljoin(url, response_headers.get("location", ""))
                continue
            if status in (401, 403):
                raise AccessStopped("Access denied; source paused")
            if status == 429:
                raise RetryLater(response_headers.get("retry-after", "60"))
            if status >= 500:
                raise OSError("Upstream unavailable")
            return status, response_headers, raw, url
        raise AccessStopped("Redirect limit exceeded")

    def fetch(self, config, url):
        validate_config(config)
        if not config["enabled"]:
            raise AccessStopped("Source disabled")
        status, _, robots, _ = self._get(config, config["origin"].rstrip("/") + "/robots.txt", robots=True)
        if status not in (200, 404, 410) or status == 200 and re.search(br"<!doctype\s+html|<html\b", robots, re.I):
            raise AccessStopped("Robots policy could not be checked")
        policy = RobotFileParser()
        policy.parse(robots.decode("utf-8", errors="strict").splitlines() if status == 200 else [])
        delay = policy.crawl_delay(BOT) if status == 200 else None
        rate = policy.request_rate(BOT) if status == 200 else None
        config = dict(config)
        config["delay_seconds"] = max(config["delay_seconds"], delay or 0, rate.seconds/rate.requests if rate else 0)
        url = allowed_url(config, url)
        if status == 200 and not policy.can_fetch(BOT, url):
            raise AccessStopped("Disallowed by robots policy")
        with self.store.db() as db:
            cache = db.execute("SELECT * FROM resources WHERE site_id=? AND url=?", (config["id"], url)).fetchone()
        headers = {}
        if cache and cache["etag"]:
            headers["If-None-Match"] = cache["etag"]
        elif cache and cache["modified"]:
            headers["If-Modified-Since"] = cache["modified"]
        return self._get_document(config, url, headers, policy, status == 200)

    def _get_document(self, config, url, headers, policy, has_robots):
        # _get's redirects are disabled for document requests by checking each destination first.
        original_request = self._request
        def guarded(target, request_headers):
            if has_robots and not policy.can_fetch(BOT, target):
                raise AccessStopped("Redirect disallowed by robots policy")
            return original_request(target, request_headers)
        self._request = guarded
        try:
            return self._get(config, url, headers)
        finally:
            self._request = original_request


class RetryLater(OSError):
    def __init__(self, value):
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = email.utils.parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                seconds = 60
        self.delay = max(60, min(seconds, 86400))
        super().__init__("Rate limited")


class ArticleParser(HTMLParser):
    def __init__(self, selector, excluded_selectors=None):
        super().__init__()
        self.selector, self.stack, self.parts, self.links = selector, [], [], []
        self.excluded_selectors = excluded_selectors or []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        active = any(v[1] for v in self.stack) or bool(self.selector and (
            attrs.get("id") == self.selector.get("id") and "id" in self.selector or
            self.selector.get("class") in classes))
        hidden = tag in ("script", "style", "nav", "footer", "noscript") or any(v[2] for v in self.stack)
        hidden = hidden or any(("id" in selector and attrs.get("id") == selector["id"])
                               or selector.get("class") in classes
                               for selector in self.excluded_selectors)
        if tag not in ("br", "img", "input", "meta", "link", "hr", "source", "wbr", "area", "base", "embed", "param", "col", "track"):
            self.stack.append((tag, active, hidden))
        if active and tag in ("p", "div", "li", "tr", "br", "section", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                active = self.stack[index][1]
                del self.stack[index:]
                if active and tag in ("p", "div", "li", "tr", "section", "h1", "h2", "h3"):
                    self.parts.append("\n")
                break

    def handle_data(self, text):
        if self.stack and self.stack[-1][1] and not self.stack[-1][2]:
            self.parts.append(text)


class PageSignals(HTMLParser):
    """Preserve reported fields as observations, never infer legal dates from them."""

    META_KEYS = frozenset({"articletitle", "pubdate", "publishdate", "publisheddate", "contentsource",
        "author", "documentnumber", "docno", "effectdate", "effectivedate", "expirydate", "validity",
        "article:published_time", "article:modified_time", "dc.title", "dc.date", "dc.creator"})

    def __init__(self):
        super().__init__()
        self.fields, self.in_title, self.title = {}, False, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            key = (attrs.get("name") or attrs.get("property") or "").strip().lower()
            if key in self.META_KEYS:
                self.fields.setdefault("meta:" + key, []).append(attrs.get("content", ""))

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
            self.fields.setdefault("html:title", []).append("".join(self.title))
            self.title = []

    def handle_data(self, text):
        if self.in_title:
            self.title.append(text)


def observe_html(raw, selectors=None):
    text = raw.decode("utf-8", errors="strict")
    parser = PageSignals()
    parser.feed(text)
    fields = parser.fields
    for name, selector in (selectors or {}).items():
        block = ArticleParser(selector)
        block.feed(text)
        # An empty observation is significant too: a removed validity/date block needs review.
        fields["block:" + name] = ["".join(block.parts)]
    normalized = {key: sorted({re.sub(r"\s+", " ", value).strip() for value in values})
                  for key, values in sorted(fields.items())}
    if len(encode(normalized)) > 32768 or any(len(value) > 8000 for values in normalized.values() for value in values):
        raise ValueError("Oversized page metadata; requires a narrower selector")
    return {"schema": 1, "media_type": "text/html", "fields": normalized}


class IndexLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current, self.links = None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.current = [attrs["href"], [], attrs.get("title", "")]

    def handle_data(self, text):
        if self.current:
            self.current[1].append(text)

    def handle_endtag(self, tag):
        if tag == "a" and self.current:
            self.links.append((self.current[0], re.sub(r"\s+", " ", "".join(self.current[1]) or self.current[2]).strip()))
            self.current = None


def discover_links(raw, keywords=None, limit=25):
    parser = IndexLinks()
    parser.feed(raw.decode("utf-8", errors="strict"))
    results, seen = [], set()
    for href, title in parser.links:
        if href in seen or keywords and not any(keyword in title for keyword in keywords):
            continue
        seen.add(href)
        results.append((href, title[:300]))
        if len(results) >= limit:
            break
    return results


def parse_html(raw, selector, min_text_length=8, excluded_selectors=None):
    parser = ArticleParser(selector, excluded_selectors)
    parser.feed(raw.decode("utf-8", errors="strict"))
    lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    chunks = []
    categories = (("identity", "身份证"), ("household", "户口"), ("eligibility", "条件"), ("procedure", "流程"))
    for line in lines:
        if len(line) < min_text_length:
            continue
        if re.search(r"\d{17}[\dXx]", line):
            raise ValueError("Potential applicant identifier; quarantined")
        category = next((name for name, keyword in categories if keyword in line), "procedure")
        # Long table rows remain intact; oversized sections require a reviewed parser.
        if len(line) > 8000:
            raise ValueError("Oversized unstructured section")
        chunks.append({"locator": f"正文段落{len(chunks)+1}", "category": category, "text": line})
    return chunks, parser.links


def select_paragraph_keywords(chunks, keywords, expected=1):
    """Keep complete selected paragraphs and original locators; stop on drift."""
    selected = [chunk for chunk in chunks if all(word in chunk["text"] for word in keywords)]
    if len(selected) != expected:
        raise ValueError("Selected HTML paragraph count changed; requires review")
    return selected


def article_section(chunks, start_title=None):
    """Separate a consolidated act from a preceding amending decision by an exact title."""
    if start_title is None:
        return chunks, None
    wanted = re.sub(r"\s+", "", start_title)
    matches = [i for i, chunk in enumerate(chunks) if re.sub(r"\s+", "", chunk["text"]) == wanted]
    if len(matches) != 1:
        raise ValueError("Unique configured consolidated-text title required")
    selected = chunks[matches[0]+1:]
    preface = [chunks[matches[0]]["text"]]
    for chunk in selected:
        if re.match(r"^第[一二三四五六七八九十百零〇ー]+条", chunk["text"]):
            break
        preface.append(chunk["text"])
    if len(preface) > 32 or sum(len(text) for text in preface) > 8000:
        raise ValueError("Consolidated-text preface exceeds bounds")
    return selected, preface


def select_articles(chunks, included, categories=None):
    """Keep complete, explicitly selected statutory clauses with their enumerated subparagraphs."""
    categories = categories or {}
    grouped, current = [], None
    for chunk in chunks:
        text = chunk["text"]
        match = re.match(r"^(第[一二三四五六七八九十百零〇ー]+条)\s*", text)
        if match:
            # Some official HTML uses U+30FC in place of the glyph 一. Keep the raw heading
            # and text; normalize only the configured selector, never the quotation.
            current = {"locator": match[1], "category": categories.get(match[1].replace("ー", "一"), chunk["category"]), "text": text}
            grouped.append(current)
        elif re.match(r"^第[一二三四五六七八九十百零〇]+[章节]", text):
            current = None
        elif current:
            current["text"] += "\n" + text
    selected = [chunk for chunk in grouped if chunk["locator"].replace("ー", "一") in included]
    if {chunk["locator"].replace("ー", "一") for chunk in selected} != set(included) or len(selected) != len(set(included)):
        raise ValueError("Expected legal article missing; parser/scope review required")
    return selected


def schedule(store, now=None):
    now = time.time() if now is None else now
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE jobs SET state='failed',error='LEASE_EXPIRED_AFTER_FINAL_ATTEMPT',lease_until=NULL WHERE state='running' AND lease_until<? AND attempts>=3", (now,))
        for row in db.execute("SELECT * FROM sites WHERE next_check<=? AND paused_reason IS NULL", (now,)).fetchall():
            config = json.loads(row["config"])
            if not config["enabled"]:
                continue
            try:
                validate_config(config)
            except ValueError as error:
                db.execute("UPDATE sites SET paused_reason=? WHERE id=?", (str(error), row["id"]))
                continue
            if config.get("discover_links"):
                from gov_mcp.knowledge_discovery import enqueue_cycle
                if not enqueue_cycle(db, config, now):
                    continue
                db.execute("UPDATE sites SET next_check=? WHERE id=?", (now+config["interval_seconds"], row["id"]))
                continue
            urls = set(config["seeds"]) | {r[0] for r in db.execute("SELECT url FROM jobs WHERE site_id=?", (row["id"],))}
            for url in sorted(urls):
                db.execute("INSERT INTO jobs(site_id,url,due) VALUES(?,?,?) ON CONFLICT(site_id,url) DO UPDATE SET state='queued',attempts=0,due=excluded.due,error=NULL WHERE jobs.state IN ('done','failed')",
                           (row["id"], url, now))
            db.execute("UPDATE sites SET next_check=? WHERE id=?", (now+config["interval_seconds"], row["id"]))


def worker_once(store, fetcher=None, owner=None, now=None):
    now, owner = time.time() if now is None else now, owner or "worker-" + secrets.token_hex(4)
    fetcher = fetcher or Fetcher(store)
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO workers VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,progress=excluded.progress", (owner, now, "polling"))
        job = db.execute("SELECT j.*,s.config,s.paused_reason FROM jobs j JOIN sites s ON s.id=j.site_id LEFT JOIN discovery_targets t ON t.site_id=j.site_id AND t.url=j.url WHERE ((j.state='queued' AND j.due<=?) OR (j.state='running' AND j.lease_until<?)) AND j.attempts<3 AND s.paused_reason IS NULL AND NOT EXISTS(SELECT 1 FROM jobs other WHERE other.id!=j.id AND other.site_id=j.site_id AND other.state='running' AND other.lease_until>=?) ORDER BY CASE WHEN t.kind IN ('index','rss','atom','sitemap') THEN 0 WHEN EXISTS(SELECT 1 FROM discovery_visits v WHERE v.cycle_id=t.cycle_id AND v.url=j.url AND v.is_new=1) THEN 1 ELSE 2 END,j.id LIMIT 1", (now, now, now)).fetchone()
        if not job:
            return False
        config = json.loads(job["config"])
        if not config["enabled"]:
            db.execute("UPDATE jobs SET state='failed',error='SOURCE_DISABLED' WHERE id=?", (job["id"],))
            return True
        db.execute("UPDATE jobs SET state='running',attempts=attempts+1,lease_until=?,owner=? WHERE id=?", (now+180, owner, job["id"]))
    try:
        from gov_mcp.knowledge_discovery import INDEX_KINDS, XML_TYPES, record_index, target_kind
        with store.db() as db:
            kind = target_kind(db, config, job["url"])
        is_index = kind in INDEX_KINDS
        status, headers, raw, final_url = fetcher.fetch(config, job["url"])
        if status not in (200, 304):
            raise ValueError(f"HTTP_{status}; unavailable does not imply revoked")
        if is_index and config.get("discover_links"):
            content_type = headers.get("content-type", "").split(";")[0].strip().lower()
            if status == 200 and (kind == "index" and content_type not in ("text/html", "application/xhtml+xml") or kind != "index" and content_type not in XML_TYPES):
                raise ValueError("Discovery format does not match approved index type")
            record_index(store, config, job["url"], kind, raw if status == 200 else None, final_url, now)
        elif status == 304 and not is_index:
            from gov_mcp.knowledge_discovery import refresh_unchanged_document
            refresh_unchanged_document(store, config, job["url"])
        elif status == 200 and not is_index:
            article_preface = None
            content_type = headers.get("content-type", "").split(";")[0].strip()
            if content_type not in ("text/html", "application/xhtml+xml", "application/pdf"):
                raise ValueError("Unsupported format; requires dedicated parser")
            if content_type == "application/pdf":
                if config.get("include_articles") or config.get("include_paragraph_keywords"):
                    raise ValueError("Configured HTML selection cannot be applied to PDF")
                with tempfile.TemporaryDirectory(prefix="parse-", dir=store.root) as directory:
                    from pathlib import Path
                    path = Path(directory) / "input.pdf"
                    path.write_bytes(raw)
                    try:
                        parsed = subprocess.run([sys.executable, "-m", "gov_mcp.knowledge_pdf", str(path)],
                            capture_output=True, timeout=30, check=False)
                    except subprocess.TimeoutExpired as error:
                        raise ValueError("PDF parsing timed out") from error
                    if parsed.returncode or len(parsed.stdout) > MAX_BYTES:
                        raise ValueError("PDF parser failed; inspect isolated input")
                    chunks, links = json.loads(parsed.stdout), []
            else:
                chunks, links = parse_html(raw, config.get("content_selector"), min_text_length=1 if config.get("include_articles") else 8,
                                           excluded_selectors=config.get("excluded_content_selectors"))
                if config.get("include_paragraph_keywords"):
                    chunks = select_paragraph_keywords(chunks, config["include_paragraph_keywords"], config.get("paragraph_expected_count", 1))
                if config.get("include_articles"):
                    chunks, article_preface = article_section(chunks, config.get("article_start_title"))
                    chunks = select_articles(chunks, config["include_articles"], config.get("article_categories"))
            if not is_index:
                if not chunks:
                    raise ValueError("No selected article body; quarantined")
                metadata = dict(config["metadata"])
                # A fetched change cannot inherit the old revision's effective/publication dates.
                metadata.update(effective_from=None, effective_to=None, published_at=None)
                metadata["source_observation"] = (
                    {"schema": 1, "media_type": "application/pdf", "raw_sha256": fingerprint(raw)}
                    if content_type == "application/pdf" else observe_html(raw, config.get("metadata_selectors")))
                if article_preface is not None:
                    metadata["source_observation"]["selected_article_preface"] = article_preface
                if job["url"] not in config["seeds"]:
                    metadata["title"] = "待复核文件：" + final_url
                    with store.db() as db:
                        edge = db.execute("SELECT e.*,t.kind AS discovery_format FROM discovery_edges e LEFT JOIN discovery_targets t ON t.site_id=e.site_id AND t.url=e.parent_url WHERE e.site_id=? AND e.target_url=? ORDER BY e.observed_at DESC,e.ordinal LIMIT 1", (config["id"], job["url"])).fetchone()
                        anchor = db.execute("SELECT data FROM audit WHERE action='document_link_discovered' AND json_extract(data,'$.site')=? AND json_extract(data,'$.url')=? ORDER BY id DESC LIMIT 1", (config["id"], job["url"])).fetchone() if not edge else None
                    observed = ({"title": edge["title"], "discovered_from": edge["parent_url"], "discovery_format": edge["discovery_format"], "hints": json.loads(edge["hints"])} if edge else json.loads(anchor[0]) if anchor else None)
                    if observed:
                        metadata["title"] = observed["title"] or metadata["title"]
                        metadata["title_source"] = "source_"+str(observed.get("discovery_format", "index"))+"_entry_pending_document_verification"
                        metadata["discovered_from"] = observed.get("discovered_from")
                        metadata["source_observation"]["discovery_signal"] = {key: observed.get(key) for key in ("title", "hints", "discovery_format")}
                revision = store.ingest(config["id"], job["url"], raw, metadata, chunks, preserve_metadata_if_unchanged=True)
                store.propose_classification(revision)
        with store.db() as db:
            db.execute("INSERT INTO resources VALUES(?,?,?,?,?) ON CONFLICT(site_id,url) DO UPDATE SET etag=COALESCE(excluded.etag,resources.etag),modified=COALESCE(excluded.modified,resources.modified),checked_at=excluded.checked_at",
                       (config["id"], job["url"], headers.get("etag"), headers.get("last-modified"), now))
            db.execute("UPDATE jobs SET state='done',error=NULL,lease_until=NULL WHERE id=? AND owner=?", (job["id"], owner))
            unfinished = db.execute("SELECT 1 FROM jobs WHERE site_id=? AND state!='done' LIMIT 1", (config["id"],)).fetchone()
            if not unfinished:
                db.execute("UPDATE sites SET last_success=?,failures=0 WHERE id=?", (now, config["id"]))
            store.log(db, "check_succeeded", {"site": config["id"], "status": status})
    except (OSError, ValueError, UnicodeError, http.client.HTTPException) as error:
        with store.db() as db:
            stopped = isinstance(error, AccessStopped)
            state = "failed" if stopped or job["attempts"]+1 >= 3 else "queued"
            delay = error.delay if isinstance(error, RetryLater) else (60, 300, 1800)[job["attempts"]]
            db.execute("UPDATE jobs SET state=?,due=?,lease_until=NULL,error=? WHERE id=? AND owner=?", (state, now+delay, str(error)[:300], job["id"], owner))
            db.execute("UPDATE sites SET failures=failures+1,paused_reason=CASE WHEN ? THEN ? ELSE paused_reason END WHERE id=?", (stopped, str(error)[:300], config["id"]))
            store.log(db, "check_failed", {"site": config["id"], "reason": str(error)[:300]})
    with store.db() as db:
        db.execute("UPDATE workers SET heartbeat=?,progress='idle' WHERE id=?", (time.time(), owner))
    return True
