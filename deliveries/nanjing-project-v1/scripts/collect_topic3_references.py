"""Archive public Nexent references without changing its checkout or deployment."""

import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "references/ict-topic3-2026-10-13"
COMMIT = "519b9b17c7f4cbc623256ac318ea96a20f33772e"
BASE = "https://modelengine-group.github.io/nexent/"
PAGES = {
    "zh-home": "zh/user-guide/home-page.html",
    "en-home": "en/user-guide/home-page.html",
    "docker-installation": "zh/quick-start/installation.html",
    "mcp-development": "zh/backend/tools/mcp.html",
    "skill-system": "zh/backend/skills/overview.html",
    "skill-import": "zh/integration/integration-in/skills.html",
    "data-processing": "zh/sdk/data-process.html",
    "knowledge-configuration": "zh/user-guide/agent-development/knowledge-configuration.html",
    "agent-export": "zh/integration/integration-out/agents-export.html",
    "multimodal-tools": "zh/user-guide/local-tools/multimodal-tools.html",
}


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skipped = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skipped += 1
        if tag in ("p", "div", "li", "h1", "h2", "h3", "br", "tr", "pre"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skipped = max(0, self.skipped - 1)

    def handle_data(self, data):
        if not self.skipped:
            self.parts.append(data)


def record(path, origin, kind, **metadata):
    data = path.read_bytes()
    return {"file": path.relative_to(DEST).as_posix(), "origin": origin,
            "kind": kind, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "saved_at": datetime.now(timezone.utc).isoformat(), **metadata}


def collect_page(item):
    name, relative = item
    url = BASE + relative
    try:
        with urlopen(Request(url, headers={"User-Agent": "ICT-reference-collector/1.0"}), timeout=30) as response:
            data = response.read()
            final_url = response.url
        html = data.decode("utf-8")
        if "<html" not in html.lower() or "Nexent" not in html:
            raise ValueError("Response is not a Nexent documentation page")
        path = DEST / "tutorials-online" / (name + ".html")
        path.write_bytes(data)
        parser = TextParser()
        parser.feed(html)
        text_path = path.with_suffix(".txt")
        text_path.write_text("\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip()), encoding="utf-8")
        return [record(path, url, "online_html_snapshot", final_url=final_url,
                       version="online documentation; not guaranteed to match v2.7.0"),
                record(text_path, url, "derived_reading_text", derived_from=path.name)]
    except Exception as error:
        return [{"origin": url, "kind": "download_failure", "error": str(error)}]


def main():
    (DEST / "tutorials-online").mkdir(parents=True, exist_ok=True)
    (DEST / "nexent-v2.7.0").mkdir(parents=True, exist_ok=True)
    records = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for result in pool.map(collect_page, PAGES.items()):
            records.extend(result)
    repo = ROOT / "vendor/nexent"
    actual = subprocess.check_output(["git", "-C", str(repo), "rev-parse", COMMIT + "^{commit}"], text=True).strip()
    if actual != COMMIT:
        raise RuntimeError("Pinned source commit mismatch")
    version = subprocess.check_output(["git", "-C", str(repo), "show", COMMIT + ":VERSION"], text=True).strip()
    if version != "v2.7.0":
        raise RuntimeError("Pinned source version mismatch")
    archive = DEST / "nexent-v2.7.0" / "nexent-official-source.zip"
    subprocess.run(["git", "-C", str(repo), "archive", "--format=zip", "--prefix=nexent-v2.7.0/",
                    "--output=" + str(archive), COMMIT], check=True)
    records.append(record(archive, "https://github.com/ModelEngine-Group/nexent/tree/" + COMMIT,
                          "source_snapshot_from_existing_git_object", commit=COMMIT, version=version,
                          note="git archive excludes local modified/untracked files; existing checkout preserved"))
    paths = subprocess.check_output(["git", "-C", str(repo), "ls-tree", "-r", "--name-only", COMMIT,
                                     "doc/docs/zh", "doc/docs/en"], text=True).splitlines()
    markdown_paths = [path for path in paths if path.endswith(".md")]
    for source in markdown_paths + ["README.md", "README_CN.md", "LICENSE", "VERSION"]:
        data = subprocess.check_output(["git", "-C", str(repo), "show", COMMIT + ":" + source])
        destination = DEST / "nexent-v2.7.0" / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        records.append(record(destination, "https://github.com/ModelEngine-Group/nexent/blob/" + COMMIT + "/" + source,
                              "pinned_source_file", commit=COMMIT, version=version))
    manifest = {"collected_at": datetime.now(timezone.utc).isoformat(), "client_timezone": "Asia/Shanghai",
                "records": records, "verification": "SHA-256 is a local integrity digest, not an upstream signature"}
    (DEST / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [row for row in records if row["kind"] == "download_failure"]
    print(json.dumps({"destination": str(DEST), "online_pages_saved": len(PAGES) - len(failures),
                      "pinned_markdown_files": len(markdown_paths), "source_zip_bytes": archive.stat().st_size,
                      "failures": failures}, ensure_ascii=False))


if __name__ == "__main__":
    main()
