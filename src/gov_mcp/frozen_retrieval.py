"""Bounded, source-preserving retrieval for frozen archives; no model or network.

Uses BM25 plus literal rank fusion, subquestion coverage and source-local windows.
Design references and comparison limits are recorded in the competition review.
Retrieved context is evidence, never a semantic approval or current-policy claim.
"""
from collections import Counter
import math
import re

from gov_mcp.knowledge_retrieval import query_grams, score_literal


VERSION = "frozen-bm25-rrf-context-v2"
METADATA_KINDS = {"source_title", "page_publication_date", "page_modified_date"}
YEAR = re.compile(r"(?<!\d)\d{4}(?=\s*年)")
HEADING = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日\s*\S+[〔\[]\d{4}[〕\]]\d+号")
COMPARISON = re.compile(r"对比|比较|区别|差异|相比|分别|新旧|先后|跨文档")
QUESTION_CUE = re.compile(r"能否|能不能|可不可以|是否|要不要|凭什么|凭哪|多久|几[天次种项]|多少|什么|怎么|如何|哪[里个些种项]|(?:由|该)谁|[吗么]$")
SUBJECT = re.compile(r"临时(?:居民)?身份证|临时证|居民身份证|身份证")
TEMPORARY_ID = re.compile(r"临时(?:居民)?身份证|临时证")
ALIASES = (("凭什么", "申领 凭证"), ("多久", "时限 制作 发放"), ("拿到", "领取"),
           ("照相费", "照相费 收费"), ("重拍", "重新拍照"))
TABLE_ROW = re.compile(r"Excel工作表(.+)!第(\d+)行")
PARAGRAPH = re.compile(r"正文段落(\d+)")
DATE_QUESTION = re.compile(r"发布|公布|修改|更新|生效|施行|实施")
TABLE_QUESTION = re.compile(r"表格|目录|设立依据|这[两几]列|哪一列|表头")
MAX_QUERIES = 4
MAX_LINKS = 12
POSTAL_COLLECTION = re.compile(r"邮费|邮寄|寄到|寄送")
CERTIFICATE_COLLECTION = re.compile(r"领证|取证|领取|代领|身份证")
FIRST_ISSUE_FEE = ("MOF-FEE-2007", "正文段落4")
FIRST_ISSUE_WAIVER = ("MOF-FIRST-ISSUE-2018", "正文段落4")
FIRST_ISSUE_EFFECTIVE_DATE = "2018年4月1日"


def identity(unit):
    return unit["source_id"], unit["locator"]


def source_key(unit):
    # Never join explicitly different revisions of the same source.
    return unit["source_id"], unit.get("revision_id"), unit.get("original_sha256")


def compound_queries(question):
    parts = re.split(r"[？?；;。！!]+", question)
    if sum(bool(QUESTION_CUE.search(p.strip())) for p in parts) < 2:
        parts = re.split(r"[，,、]|以及|并且|另外|和(?=能否|是否|要不要|多久|怎么|如何)", question)
    if sum(bool(QUESTION_CUE.search(p.strip())) for p in parts) < 2:
        return
    subject = ""
    for part in parts:
        part = part.strip()
        mentions = SUBJECT.findall(part)
        if mentions:
            subject = next((term for term in mentions if term.startswith("临时")), mentions[-1])
        if not QUESTION_CUE.search(part):
            continue
        tail = re.split(r"[，,]", part)[-1].strip()
        if QUESTION_CUE.search(tail):
            part = tail
        aliases = [alias for phrase, alias in ALIASES if phrase in part]
        if subject and not SUBJECT.search(part):
            aliases.append(TEMPORARY_ID.sub("临时居民身份证", subject))
        yield " ".join([TEMPORARY_ID.sub("临时居民身份证", part), *aliases])


def terms(text):
    result = []
    for run in re.findall(r"[\u3400-\u9fff]+|[a-z0-9]+", text.lower()):
        if re.fullmatch(r"[a-z0-9]+", run):
            result.append(run)
        else:
            result.extend(run[i:i + 2] for i in range(len(run) - 1))
    return result


class EvidenceIndex:
    """One immutable corpus snapshot. Cached statistics contain no user answers."""

    def __init__(self, units):
        self.units = tuple(dict(unit) for unit in units)
        if len({identity(u) for u in self.units}) != len(self.units):
            raise ValueError("AMBIGUOUS_SOURCE_LOCATOR")
        self.body = tuple(u for u in self.units if u.get("evidence_kind") not in METADATA_KINDS
                          and not self.is_header(u) and not HEADING.fullmatch(u["text"]))
        self.counts = [Counter(terms(u["text"])) for u in self.body]
        self.lengths = [sum(count.values()) for count in self.counts]
        self.average = sum(self.lengths) / max(1, len(self.lengths)) or 1
        self.df = Counter(term for count in self.counts for term in count)

    @staticmethod
    def is_header(unit):
        return unit.get("evidence_kind") == "xlsx_row" and all(
            label in unit["text"] for label in ("项目名称", "设立依据", "收费标准"))

    def rank(self, query, sources=None, kind=None):
        grams, tokens = query_grams(query), set(terms(query))
        literal, bm25 = [], []
        for i, (unit, counts, length) in enumerate(zip(self.body, self.counts, self.lengths)):
            if sources is not None and source_key(unit) not in sources:
                continue
            if kind is not None and unit.get("evidence_kind") != kind:
                continue
            overlap = score_literal(grams, unit["text"])
            score = 0.0
            for term in tokens & counts.keys():
                freq = counts[term]
                idf = math.log(1 + (len(self.body) - self.df[term] + .5) / (self.df[term] + .5))
                score += idf * freq * 2.2 / (freq + 1.2 * (.25 + .75 * length / self.average))
            if overlap > 0:
                literal.append((overlap, i))
            if score > 0:
                bm25.append((score, i))
        fused = Counter()
        for ranking in (literal, bm25):
            for rank, (_, i) in enumerate(sorted(ranking, key=lambda x: (-x[0], x[1]))[:20], 1):
                fused[i] += 1 / (60 + rank)
        return [(self.body[i], score) for i, score in sorted(fused.items(), key=lambda x: (-x[1], x[0]))]

    def retrieve(self, question, k=5):
        if type(k) is not int or not 1 <= k <= 20:
            raise ValueError("RETRIEVAL_K_OUT_OF_RANGE")
        years = sorted(set(YEAR.findall(question)))[:2]
        comparison = bool(COMPARISON.search(question) or len(years) > 1)
        queries = list(dict.fromkeys(compound_queries(question)))[:MAX_QUERIES]
        if not queries and comparison:
            clauses = [p.strip() for p in re.split(r"[，；、。？！]|以及|与|和|及", question)
                       if len(terms(p)) >= 3]
            queries = list(dict.fromkeys(clauses))[:MAX_QUERIES]
        lanes = [("question", question, self.rank(question))]
        lanes.extend((f"part-{i}", query, self.rank(query)) for i, query in enumerate(queries, 1))
        if POSTAL_COLLECTION.search(question) and CERTIFICATE_COLLECTION.search(question):
            postal_query = "居民身份证 邮寄领证 邮费 自费 邮寄服务 领取证件"
            lanes.append(("postal-collection", postal_query, self.rank(postal_query)))
        year_sources = {}
        for unit in self.units:
            if unit.get("evidence_kind") == "page_publication_date":
                values = [unit.get("calendar_date", "")[:4]]
            elif unit.get("evidence_kind") == "source_title" or HEADING.fullmatch(unit["text"]):
                values = YEAR.findall(unit["text"])
            else:
                continue
            for year in values:
                year_sources.setdefault(year, set()).add(source_key(unit))
        if comparison:
            lanes.extend((f"year-{year}", question, self.rank(question, year_sources[year]))
                         for year in years if year_sources.get(year))
        table_query = next((q for q in queries if TABLE_QUESTION.search(q)), question)
        if TABLE_QUESTION.search(table_query):
            lanes.append(("table", table_query, self.rank(table_query, kind="xlsx_row")))
        body_limit = k + 2
        evidence, seen, reasons = [], set(), {}

        def include(unit, reason, score=0):
            key = identity(unit)
            if key in seen:
                if reason not in reasons[key]:
                    reasons[key].append(reason)
                return False
            seen.add(key)
            reasons[key] = [reason]
            evidence.append(dict(unit, rank=len(evidence) + 1, score=score))
            return True

        # Reserve a result for each intent/year before one verbose source can fill the packet.
        priority = [lane for lane in lanes
                    if lane[0].startswith("year-") or lane[0] in {"table", "postal-collection"}]
        priority += lanes[1:1 + len(queries)]
        for name, _, ranking in priority:
            if ranking and len(evidence) < body_limit:
                unit, score = ranking[0]
                include(unit, name, score)
        for depth in range(20):
            for name, _, ranking in lanes:
                if len(evidence) < body_limit and depth < len(ranking):
                    unit, score = ranking[depth]
                    include(unit, name, score)
        anchors = list(evidence)
        linked = []
        if re.search(r"首次(?:申请)?(?:申领|领取|办理)|停征", question):
            fee_sources = {identity(u): u for u in self.body
                           if identity(u) in (FIRST_ISSUE_FEE, FIRST_ISSUE_WAIVER)}
            if seen.intersection(fee_sources) and len(fee_sources) == 2:
                linked.extend((unit, "first-issue-fee-history") for unit in fee_sources.values())
                fee_source_keys = {source_key(unit) for unit in fee_sources.values()}
                linked.extend((unit, "first-issue-fee-document-date") for unit in self.units
                              if source_key(unit) in fee_source_keys and HEADING.fullmatch(unit["text"]))
        # Exact table headers stay separately citable with their cells; never concatenate rows.
        neighbour_count = 0
        for hit in anchors:
            row = TABLE_ROW.fullmatch(hit["locator"]) if hit.get("evidence_kind") == "xlsx_row" else None
            if row:
                headers = [u for u in self.units if source_key(u) == source_key(hit) and self.is_header(u)
                           and (match := TABLE_ROW.fullmatch(u["locator"]))
                           and match[1] == row[1] and int(match[2]) < int(row[2])]
                if headers:
                    linked.append((max(headers, key=lambda u: int(TABLE_ROW.fullmatch(u["locator"])[2])), "table-header"))
        sources = {source_key(u) for u in anchors}
        if DATE_QUESTION.search(question) and years:
            sources.update(source_key(u) for u in self.units if u.get("evidence_kind") == "source_title"
                           and set(years).intersection(YEAR.findall(u["text"])))
        if comparison or years or DATE_QUESTION.search(question):
            linked.extend((u, "source-metadata") for u in self.units if source_key(u) in sources and (
                u.get("evidence_kind") in METADATA_KINDS or HEADING.fullmatch(u["text"])))
        # Expand only explicit paragraph neighbours within the same source/revision.
        # Complete articles and tables are never joined by corpus array position.
        by_location = {(source_key(u), u["locator"]): u for u in self.body}
        for hit in anchors:
            match = PARAGRAPH.fullmatch(hit["locator"]) if hit.get("evidence_kind") == "html_paragraph" else None
            if match:
                for offset in (1, -1):
                    neighbour = by_location.get((source_key(hit), f"正文段落{int(match[1]) + offset}"))
                    if neighbour is not None and identity(neighbour) not in seen and neighbour_count < 4:
                        linked.append((neighbour, "adjacent-paragraph"))
                        neighbour_count += 1
        for unit, reason in linked:
            if identity(unit) in seen or len(evidence) < len(anchors) + MAX_LINKS:
                include(unit, reason)
        plan = {"version": VERSION, "body_limit": body_limit, "link_limit": MAX_LINKS,
                "primary_count": len(anchors), "linked_count": len(evidence) - len(anchors),
                "semantic_coverage_verified": False,
                "queries": [{"id": name, "query": query, "candidate_count": len(ranking),
                             "top_evidence": [{"source_id": u["source_id"], "locator": u["locator"]}
                                              for u, _ in ranking[:2] if identity(u) in seen]}
                            for name, query, ranking in lanes],
                "reasons": [{"source_id": u["source_id"], "locator": u["locator"],
                             "reasons": reasons[identity(u)]} for u in evidence]}
        return {"evidence": evidence, "plan": plan}


def retrieve_with_plan(units, question, k=5):
    return EvidenceIndex(units).retrieve(question, k)


def retrieve_evidence(units, question, k=5):
    return retrieve_with_plan(units, question, k)["evidence"]


def query_hints(plan, evidence):
    """Compact routing hints for the model; full provenance stays in the audit plan."""
    ranks = {identity(u): f"E{u['rank']}" for u in evidence}
    return [{"id": lane["id"], "query": lane["query"],
             "evidence": [ranks[identity(u)] for u in lane["top_evidence"]]}
            for lane in plan["queries"] if lane["id"] != "question"]
