"""Read-only, source-bound retrieval for an isolated synthetic asset snapshot."""

import hashlib
import json
import math
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from gov_mcp.core import result


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def terms(text):
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text.lower())
    return {text[i:i+2] for i in range(len(text)-1)} - {
        "演练", "模拟", "事项", "需要", "哪些", "什么", "如何", "多少", "请问", "办理", "资料"}


def cosine(left, right):
    denominator = math.sqrt(sum(x*x for x in left) * sum(x*x for x in right))
    if not denominator:
        raise ValueError("Empty vector")
    return sum(x*y for x, y in zip(left, right)) / denominator


class AssetQuery:
    def __init__(self, runtime: Path, embedder=None):
        self.runtime = Path(runtime)
        self.embedder = embedder or self.embed

    def load(self):
        directory = self.runtime / "snapshot"
        index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
        payload = json.loads((directory / "assets.json").read_text(encoding="utf-8"))
        vectors = json.loads((directory / "vectors.json").read_text(encoding="utf-8"))
        if payload.get("data_class") != "synthetic" or not payload.get("assets"):
            raise ValueError("This unauthenticated source viewer only accepts synthetic fixtures")
        if digest({"assets": payload, "vectors": vectors}) != index["snapshot_sha256"] or index["knowledge_version"] != "docs-" + index["snapshot_sha256"][:16]:
            raise ValueError("Snapshot fingerprint mismatch")
        assets, segments, seen = [], [], set()
        for asset in payload["assets"]:
            raw = (directory / asset["snapshot_file"]).resolve()
            if not raw.is_relative_to((directory / "raw").resolve()) or not raw.is_file():
                raise ValueError("Invalid original file")
            if hashlib.sha256(raw.read_bytes()).hexdigest() != asset["sha256"]:
                raise ValueError("Original file changed")
            if not asset.get("parsed") or not asset.get("segments"):
                raise ValueError("Unparsed asset")
            assets.append(asset)
            for segment in asset["segments"]:
                if segment["asset_id"] != asset["asset_id"] or segment["segment_id"] in seen or not segment.get("content", "").strip():
                    raise ValueError("Invalid segment identity")
                seen.add(segment["segment_id"])
                segments.append({**segment, "title": asset["title"], "source_id": asset["source_id"],
                                 "modality": asset["modality"], "sha256": asset["sha256"]})
        dimension = vectors["dimension"]
        rows = vectors["rows"]
        if not isinstance(dimension, int) or dimension <= 0 or len(rows) != len(segments):
            raise ValueError("Vector shape mismatch")
        for row, segment in zip(rows, segments):
            if row["segment_id"] != segment["segment_id"] or len(row["vector"]) != dimension or not all(math.isfinite(x) for x in row["vector"]) or not any(row["vector"]):
                raise ValueError("Vector identity mismatch")
        return index, assets, segments, vectors

    def role(self, tenant_id, user_id):
        mapping = json.loads((self.runtime / "role-map.json").read_text(encoding="utf-8"))
        role = mapping.get(tenant_id, {}).get(user_id)
        return role if role in ("citizen", "staff") else None

    def embed(self, query, vectors):
        with httpx.Client(trust_env=False, timeout=45) as client:
            response = client.post("http://host.docker.internal:11434/api/embed", json={
                "model": vectors["model"], "input": vectors["query_instruction"] + query,
                "truncate": False, "keep_alive": 0})
            response.raise_for_status()
            rows = response.json()["embeddings"]
        if len(rows) != 1 or len(rows[0]) != vectors["dimension"] or not all(math.isfinite(x) for x in rows[0]) or not any(rows[0]):
            raise ValueError("Invalid query embedding")
        return rows[0]

    def search(self, tenant_id, user_id, query, limit=4):
        started = time.perf_counter()
        role = None
        try:
            role = self.role(tenant_id, user_id)
            if not role:
                value = result("BLOCKED", "ROLE_NOT_CONFIGURED")
            elif not isinstance(query, str) or not query.strip() or len(query) > 500 or type(limit) is not int or not 1 <= limit <= 6:
                value = result("BLOCKED", "INVALID_ARGUMENTS")
            else:
                index, assets, segments, vectors = self.load()
                # Conservative candidate gate, not a general proof of answerability.
                # All results remain verbatim evidence; this tool never issues a verdict.
                query_terms = terms(query)
                corpus = "\n".join(row["content"] for row in segments)
                topics = (("费用", "收费", "多少钱"), ("时限", "工作日", "几天"), ("地址", "地点"))
                uncovered_topic = any(any(word in query for word in group) and not any(word in corpus for word in group) for group in topics)
                candidates = [row for row in segments if query_terms & terms(row["content"])]
                if uncovered_topic or not candidates:
                    value = result("EMPTY", "NO_SUPPORTING_EVIDENCE", notice="资料不足，未形成办理结论。")
                    value["data"]["answer_markdown"] = "资料不足：当前模拟资料未找到足够依据。请补充资料或交由人工核对。"
                else:
                    query_vector = self.embedder(query, vectors)
                    by_id = {row["segment_id"]: row["vector"] for row in vectors["rows"]}
                    ranked = sorted(((cosine(query_vector, by_id[row["segment_id"]]), row) for row in candidates), key=lambda pair: pair[0], reverse=True)[:limit]
                    value = result("OK", "EVIDENCE_FOUND", notice="以下是模拟资料原文片段，尚未作业务推理或正式审批。")
                    value["items"] = [{"segment_id": row["segment_id"], "content": row["content"], "score": round(score, 6)} for score, row in ranked]
                    value["citations"] = [{key: row[key] for key in ("segment_id", "asset_id", "source_id", "title", "modality", "sha256", "locator")} for _, row in ranked]
                    for citation in value["citations"]:
                        citation["url"] = "http://127.0.0.1:8774/evidence/" + index["knowledge_version"] + "/" + citation["segment_id"]
                    lines = ["以下为模拟资料中的相关原文，供核对："]
                    for item, citation in zip(value["items"], value["citations"]):
                        # Escape markdown syntax in input text; preserve original text in JSON.
                        content = re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", item["content"]).replace("\n", " ")
                        lines.append("- " + content + " [查看原文](" + citation["url"] + ")")
                    lines.extend(["", "资料版本：`" + index["knowledge_version"] + "`。当前结果为检索原文，未作正式办理结论。"])
                    value["data"]["answer_markdown"] = "\n".join(lines)
                value["knowledge_version"] = index["knowledge_version"]
                value["data"]["data_class"] = "synthetic"
                value["data"]["asset_count"] = len(assets)
        except (httpx.HTTPError, TimeoutError):
            value = result("ERROR", "EMBEDDING_UNAVAILABLE")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            value = result("ERROR", "ASSET_SNAPSHOT_UNAVAILABLE")
        value["data"].update(request_id=secrets.token_hex(8), role=role)
        value["data"].setdefault("answer_markdown", "查询未完成：" + value["message_code"] + "。请检查资料或工具状态。")
        entry = {"time": datetime.now(timezone.utc).isoformat(), "tool": "govdocs_search_evidence",
                 "query_sha256": hashlib.sha256((query if isinstance(query, str) else "").encode()).hexdigest(),
                 "principal_sha256": hashlib.sha256(f"{tenant_id}\0{user_id}".encode()).hexdigest(),
                 "duration_ms": round((time.perf_counter()-started)*1000, 2), "result": value}
        try:
            with (self.runtime / "tool-audit.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            return result("ERROR", "AUDIT_UNAVAILABLE")
        return value
