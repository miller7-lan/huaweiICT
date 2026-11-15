"""Source-bound semantic candidates, opt-in and offline; v1/v2 remain unchanged.

Every semantic field cites exact source spans. Rules identify candidate context,
not legal applicability, entailment, or a replacement for human approval. Unknown
objects and conditions stay explicit. Cards are not read by the QA pipeline.
"""
from copy import deepcopy
from hashlib import sha256
import json
import re

import extract_fact_cards as v1
import extract_fact_cards_v2 as v2
from gov_mcp.frozen_retrieval import source_key, TABLE_ROW, EvidenceIndex

VERSION = "fact-cards-v3-semantic-candidate"
NUMBER = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+)"
QUANTITY = NUMBER + r"\s*(?:个工作日|工作日|个自然日|自然日|个月|日|天|年)"
CHANGE = re.compile(r"(?:从|由)(?P<prefix>[^。；;\n\d]{0,25}?)(?P<old>" + QUANTITY
                    + r")(?:内)?[，,]?\s*(?:缩减|缩短|延长)至(?P<new>" + QUANTITY + r")(?:内)?")
MODAL = re.compile(r"应当|必须|不得|不予|禁止|不能|可以|允许")
CONDITION = re.compile(
    r"(?:本省|我省)户籍居民|在居住地连续居住的|在居住证签发每满[^，,。；;\n]{1,35}"
    r"|符合[^，,。；;\n]{1,30}的申请人|(?:对)?申请材料不(?:齐)?全(?:的|时)?"
    r"|[^，,。；;\n]{1,30}仍不提交居民户口簿的|凭居民户口簿无法证明的事项"
    r"|(?:居民户口簿、居民身份证)?完全能够证明的|以本人的劳动收入为主要生活来源的"
    r"|未满\s*" + NUMBER + r"\s*周岁|" + NUMBER + r"\s*周岁(?:以上|以下)"
    r"|自授予专利权当年起|告知\s*" + QUANTITY + r"后")
OBJECT = re.compile(
    r"首次申领居民身份证工本费|证券期货行业机构监管费|专利年费(?:的减缴期限)?|专利收费"
    r"|普通护照|往来港澳通行证|往来台湾通行证|居住证|签注|居民户口簿"
    r"|户口登记项目内容|户口登记业务|居民身份证|申领(?!人|条件)|补领|换领|姓名(?:的)?变更|变更姓名|未成年人|未成年子女姓名"
    r"|便民利民[^。；;\n]{0,10}措施|本实施规定|窗口相关业务所有办理信息"
    r"|(?:需(?:要)?(?:补充|补齐)|所需补齐)的?材料|新兵入伍|公民考取军校入学|直接招收和定向培养士官入伍")
ANNOUNCEMENT = re.compile(r"[^，,；;|《》\n]{0,90}?公告\s*\d{4}\s*年第\s*\d+\s*号")
CREATED_TITLE = re.compile(r"(?P<year>\d{4}年)制定的(?P<title>《[^》]+》)")


def ref(unit, start, end, **extra):
    if not 0 <= start < end <= len(unit["text"]):
        raise ValueError("SEMANTIC_REFERENCE_OUT_OF_RANGE")
    return {"source_id": unit["source_id"], "locator": unit["locator"], "span": [start, end],
            "text": unit["text"][start:end], "unit_text_sha256": unit["text_sha256"], **extra}


def references(pattern, unit, left, right):
    return [ref(unit, m.start(), m.end()) for m in pattern.finditer(unit["text"], left, right)]


def full_document_key(number):
    text = re.sub(r"\s+", "", number)
    match = re.fullmatch(r"(?P<issuer>[\u4e00-\u9fff]+)[〔\[［(（](?P<year>\d{4})[〕\]］)）](?P<number>\d+)号", text)
    return f"{match['issuer']}〔{match['year']}〕{match['number']}号" if match else text


def row_contexts(units):
    columns = v1.table_columns(units)
    contexts = {}
    last_root = {}
    last_subproject = {}
    for unit in sorted(units, key=lambda u: (str(source_key(u)),
                      TABLE_ROW.fullmatch(u["locator"])[1] if TABLE_ROW.fullmatch(u["locator"]) else "",
                      int(TABLE_ROW.fullmatch(u["locator"])[2]) if TABLE_ROW.fullmatch(u["locator"]) else -1)):
        match = TABLE_ROW.fullmatch(unit["locator"])
        if not match:
            continue
        key = (source_key(unit), match[1])
        if EvidenceIndex.is_header(unit):
            last_root.pop(key, None)
            last_subproject.pop(key, None)
        pairs = columns.get((unit["source_id"], unit["locator"]), [])
        spans = v2.cell_spans(unit)
        row = {name: ref(unit, *spans[address], cell=address) for name, value, address in pairs
               if address in spans and unit["text"][slice(*spans[address])] == value}
        project = row.get("项目名称")
        if project and row.get("项目序号"):
            last_root[key] = project
            last_subproject.pop(key, None)
        parent = None
        if project and not row.get("项目序号"):
            if re.match(r"^[①②③④⑤⑥⑦⑧⑨⑩]", project["text"]):
                parent = last_subproject.get(key) or last_root.get(key)
            elif re.match(r"^[（(][0-9一二三四五六七八九十]+[）)]", project["text"]):
                parent = last_root.get(key)
                last_subproject[key] = project
        contexts[unit["source_id"], unit["locator"]] = {"columns": row,
            "project": project, "parent_project": parent}
    return contexts


def date_semantics(card, unit, left, right):
    role = card["value"]["role"]
    if role != "unspecified":
        return role
    sentence = unit["text"][left:right]
    start, end = card["span"]
    prefix, suffix = unit["text"][left:start], unit["text"][end:right]
    if v1.frozen_qa.DOCUMENT_HEADING.fullmatch(unit["text"]):
        return "document_date"
    if "暂免征收" in sentence:
        if re.search(r"至\s*$", prefix):
            return "fee_exemption_end_date"
        if re.search(r"自\s*$", prefix):
            return "fee_exemption_start_date"
    if re.search(r"自\s*$", prefix) and re.match(r"\s*起", suffix):
        if "停征" in sentence:
            return "fee_suspension_start_date"
        return "rule_start_date"
    return role


def enrich(card, unit, units, contexts, sources):
    result = deepcopy(card)
    result["extractor"] = VERSION
    left, right = v1.around(unit["text"], v1.sentences(unit["text"]), *card["span"])
    text = unit["text"]
    context = contexts.get((unit["source_id"], unit["locator"]))
    fields = {"objects": [], "conditions": references(CONDITION, unit, left, right),
              "modalities": references(MODAL, unit, left, right), "exceptions": [],
              "actions": [], "time_context": [], "changes": []}
    if context:
        if context["project"]:
            fields["objects"].append(context["project"])
        if context["parent_project"]:
            fields["objects"].append(context["parent_project"])
        fields["table_columns"] = context["columns"]
        containing = [(address, span) for address, span in v2.cell_spans(unit).items()
                      if span[0] <= card["span"][0] and card["span"][1] <= span[1]]
        fields["containing_cells"] = [ref(unit, *span, cell=address) for address, span in containing]
    else:
        fields["objects"] = references(OBJECT, unit, left, right)
        if card["type"] == "amount":
            fields["objects"] = [ref(unit, m.start(), m.end()) for name in card["subjects"]
                                 for m in re.compile(re.escape(name)).finditer(text, left, right)]
    if card["type"] == "count" and card["value"]["unit"] == "项" and not context:
        for pattern in (r"完全能够证明的(?P<objects>[^。；;\n]+?)等" + NUMBER + r"项",
                        r"更正或者变更(?P<objects>[^。；;\n]+?)等" + NUMBER + r"项",
                        r"“(?P<objects>[^”]+)”" + NUMBER + r"项户口登记业务"):
            for match in re.finditer(pattern, text[left:right]):
                fields["objects"].append(ref(unit, left + match.start("objects"), left + match.end("objects")))
    # A number mentioned within an announcement names that announcement, not a rule's start year.
    if card["type"] == "year":
        announcement = next((m for m in ANNOUNCEMENT.finditer(text)
                             if m.start() <= card["span"][0] < m.end()), None)
        creation = next((m for m in CREATED_TITLE.finditer(text)
                         if m.start("year") == card["span"][0]), None)
        if announcement:
            fields["objects"] = [ref(unit, announcement.start(), announcement.end())]
            result["year_role"] = "announcement_year"
        elif creation:
            fields["objects"] = [ref(unit, creation.start("title"), creation.end("title"))]
            result["year_role"] = "document_creation_year"
        elif unit.get("evidence_kind") == "source_title":
            fields["objects"] = [ref(unit, 0, len(text))]
            result["year_role"] = "title_year_unreviewed"
        else:
            result["year_role"] = "stated_context_year"
    if card["type"] == "date":
        result["value"]["role"] = date_semantics(card, unit, left, right)
        if result["value"]["role"] in ("document_date", "page_publication_date", "page_modified_date"):
            titles = [u for u in units if source_key(u) == source_key(unit) and u.get("evidence_kind") == "source_title"]
            fields["objects"] = [ref(u, 0, len(u["text"])) for u in titles]
    if card["type"] == "count" and unit.get("evidence_kind") == "source_title":
        fields["objects"] = [ref(unit, 0, len(text))]
    if card["type"] == "document_number":
        result["value"]["legacy_match_key"] = result["value"]["key"]
        result["value"]["key"] = full_document_key(result["value"]["number"])
    # Keep direct clauses carrying actions, triggers and exceptions, separately from the object.
    action_patterns = (
        r"一次性告知[^。；;\n]{0,35}", r"一次性了解[^。；;\n]{0,35}",
        r"群众在派出所申领、补领、换领居民身份证后", r"每年签注一次",
        r"不再出具证明", r"(?:停征|暂免征收)[^。；;\n]{1,100}",
        r"[^，,。；;\n]{0,40}办理签注手续")
    for pattern in action_patterns:
        fields["actions"].extend(references(re.compile(pattern), unit, left, right))
    fields["exceptions"] = references(re.compile(r"但[^。；;\n]+|[^，,。；;\n]{1,35}除外"), unit, left, right)
    fields["frequency"] = references(re.compile(r"每年|每月|每次"), unit, left, right)
    fields["starting_events"] = references(re.compile(r"群众在派出所申领、补领、换领居民身份证后|自授予专利权当年起|告知" + QUANTITY + r"后"), unit, left, right)
    fields["conditions"].extend(fields["starting_events"])
    if card["type"] == "count":
        result["quantity_interpretation"] = (
            "age" if card["value"]["unit"] in ("岁", "周岁") else
            "notification_completeness" if text[card["span"][1]:].startswith("性告知") else
            "information_bundle" if text[card["span"][1]:].startswith("性了解") else
            "frequency" if fields["frequency"] else "item_count")
    if card["type"] == "duration":
        result["duration_role"] = "waiting_after_notice" if re.search(r"告知\s*$", text[left:card["span"][0]]) else "unspecified"
    # Timeline context is explicitly contextual: quantities in other sentences are not merged.
    for a, b in v1.sentences(text):
        years = references(re.compile(r"(?<!\d)\d{4}年"), unit, a, b)
        if years and (left == a or ("居民身份证" in text[left:right] and "居民身份证" in text[a:b])):
            fields["time_context"].append({"years": years, "context": ref(unit, a, b),
                                           "relation": "source_context_not_current_applicability"})
    for m in CHANGE.finditer(text, left, right):
        fields["changes"].append({"before": ref(unit, m.start("old"), m.end("old")),
                                  "after": ref(unit, m.start("new"), m.end("new")),
                                  "context": ref(unit, m.start(), m.end()),
                                  "card_stage": "before" if m.start("old") == card["span"][0] else
                                                "after" if m.start("new") == card["span"][0] else "context_only"})
    result["field_evidence"] = fields
    result["subjects"] = list(dict.fromkeys(item["text"] for item in fields["objects"]))
    result["subjects_from"] = "explicit_source_references" if result["subjects"] else "unknown"
    # Preserve normative words in modalities; they are not applicability conditions.
    result["qualifiers"] = list(dict.fromkeys(item["text"] for item in fields["conditions"]))
    source = sources.get(unit["source_id"], {})
    result["provenance"] = {key: source[key] for key in ("original_sha256", "revision", "url", "kind") if key in source}
    result["provenance"]["unit_kind"] = unit.get("evidence_kind")
    result["review_state"] = "machine_candidate_unreviewed"
    result["current_applicability"] = "unknown"
    result["formal_approval"] = False
    result["semantic_gaps"] = (["OBJECT_NOT_BOUND"] if not result["subjects"] else [])
    if card["type"] in ("amount", "duration", "count") and not fields["conditions"]:
        result["semantic_gaps"].append("APPLICABILITY_NOT_ESTABLISHED")
    if card["type"] == "date" and result["value"]["role"] == "unspecified":
        result["semantic_gaps"].append("DATE_ROLE_NOT_ESTABLISHED")
    if not result["provenance"].get("original_sha256"):
        result["semantic_gaps"].append("ORIGINAL_BINDING_NOT_PROVIDED")
    result["semantic_id"] = sha256(json.dumps({"version": VERSION, "source": source,
        "card_id": result["card_id"]}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result


def extract(units, sources=None):
    declared = sources or []
    sources = {s["source_id"]: s for s in declared}
    if len(sources) != len(declared):
        raise ValueError("AMBIGUOUS_SOURCE_MANIFEST")
    # Refuse ambiguous serialized identities instead of silently choosing a version.
    by_unit = {}
    versions = {}
    for unit in units:
        versions.setdefault(unit["source_id"], set()).add((*source_key(unit), unit.get("revision")))
        source = sources.get(unit["source_id"], {})
        for field, declared_field in (("original_sha256", "original_sha256"), ("revision_id", "revision"), ("revision", "revision")):
            if unit.get(field) and source.get(declared_field) and unit[field] != source[declared_field]:
                raise ValueError("SOURCE_MANIFEST_BINDING_MISMATCH")
        key = unit["source_id"], unit["locator"]
        if key in by_unit and (source_key(by_unit[key]) != source_key(unit) or by_unit[key]["text"] != unit["text"]):
            raise ValueError("AMBIGUOUS_SOURCE_VERSION_OR_UNIT")
        by_unit[key] = unit
    if any(len(value) > 1 for value in versions.values()):
        raise ValueError("MULTIPLE_SOURCE_VERSIONS_REQUIRE_SEPARATE_EXTRACTION")
    contexts = row_contexts(units)
    return [enrich(card, by_unit[card["source_id"], card["locator"]], units, contexts, sources)
            for card in v2.extract(units)]


def verify_field_references(cards, units):
    by_unit = {(u["source_id"], u["locator"]): u for u in units}
    errors = []
    def visit(value, card_id):
        if isinstance(value, dict):
            if {"source_id", "locator", "span", "text"} <= set(value):
                unit = by_unit.get((value["source_id"], value["locator"]))
                a, b = value["span"]
                if not unit or unit["text"][a:b] != value["text"] or unit["text_sha256"] != value["unit_text_sha256"]:
                    errors.append(card_id)
            for child in value.values():
                visit(child, card_id)
        elif isinstance(value, list):
            for child in value:
                visit(child, card_id)
    for card in cards:
        visit(card["field_evidence"], card["card_id"])
    return errors
