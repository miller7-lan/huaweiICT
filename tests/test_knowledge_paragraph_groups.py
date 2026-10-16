"""Contracts for discontiguous complete paragraphs in actual policy notifications."""
import time

import pytest

from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.knowledge_fetch import parse_html, schedule, select_paragraph_groups, validate_config, worker_once


def config():
    return {"id":"groups", "label":"模拟收费通知", "enabled":False, "origin":"https://fixture.invalid",
        "allowed_paths":["/notice"], "seeds":["https://fixture.invalid/notice"], "interval_seconds":3600,
        "delay_seconds":5, "metadata":{"title":"模拟通知","region":"national","matter":"demo-id-renewal",
            "document_type":"administrative_norm","effective_from":None,"effective_to":None,"scope_confirmed":False,"usage_basis":"synthetic test"},
        "content_selector":{"id":"text"}, "include_paragraph_keyword_groups":[["一、","换领"],["临时证"],["四、"],["五、"]]}


def html(amount="20", extra="", date="2004年1月1日"):
    return ('<article id="text"><p>一、换领费用每证'+amount+'元。</p><p>临时证费用每证10元。</p>'
        '<p>二、旧证历史分支不选入。</p><p>三、旧许可证程序不选入。</p>'
        '<p>四、不得擅自增加收费。</p><p>五、自'+date+'起执行。</p>'+extra+'</article>').encode()


def test_groups_keep_complete_paragraphs_and_original_order_even_if_config_order_differs():
    chunks,_=parse_html(html(),{"id":"text"})
    groups=config()["include_paragraph_keyword_groups"]
    selected=select_paragraph_groups(chunks,list(reversed(groups)))
    assert selected==[chunks[i] for i in [0,1,4,5]]
    assert [r["locator"] for r in selected]==["正文段落1","正文段落2","正文段落5","正文段落6"]


@pytest.mark.parametrize("groups",[[["不存在"]],[["换领"],["一、"]],[["费用"]]])
def test_missing_overlapping_or_ambiguous_groups_are_rejected(groups):
    chunks,_=parse_html(html(),{"id":"text"})
    with pytest.raises(ValueError,match="requires review"):select_paragraph_groups(chunks,groups)


@pytest.mark.parametrize("update",[
    {"include_paragraph_keyword_groups":[]}, {"include_paragraph_keyword_groups":[[]]},
    {"include_paragraph_keyword_groups":[[""]]}, {"include_paragraph_keyword_groups":[[1]]},
    {"include_paragraph_keyword_groups":[["x"*101]]}, {"include_paragraph_keyword_groups":[["x"]]*9},
    {"include_paragraph_keywords":["一、"]}, {"include_articles":["第一条"]},
    {"paragraph_expected_count":3}, {"paragraph_expected_count":True},
    {"pdf_ocr":{"engine":"windows.media.ocr","language":"zh-Hans-CN"}},
])
def test_group_configuration_is_bounded_and_cannot_mix_modes(update):
    c=config();c.update(update)
    with pytest.raises(ValueError):validate_config(c)


def test_worker_tracks_amount_and_date_changes_without_using_unselected_legacy_clauses(tmp_path):
    (tmp_path/"phase.json").write_text('{"phase":"knowledge_update","schema_version":1}',encoding="utf-8")
    store=KnowledgeStore(tmp_path);c=config();c["enabled"]=True
    from datetime import datetime,timezone
    c["access_review"]={"approved":True,"reviewer":"fixture","usage_basis":"synthetic fixture","checked_at":datetime.now(timezone.utc).isoformat()}
    store.register(c)
    class Fixture:
        payload=html()
        def fetch(self,source,url):return 200,{"content-type":"text/html"},self.payload,url
    fixture=Fixture();clock=time.time();schedule(store,now=clock);worker_once(store,fixture,now=clock)
    first=store.status()["pending_review"][0]
    with store.db() as db:_,chunks=store._content(db,first["id"])
    assert len(chunks)==4 and all("历史分支" not in r["text"] and "许可证" not in r["text"] for r in chunks)
    fixture.payload=html("21",date="2004年2月1日");schedule(store,now=clock+3700);worker_once(store,fixture,now=clock+3700)
    latest=store.status()["pending_review"][0]
    assert latest["id"]!=first["id"] and latest["metadata"]["effective_from"] is None
    with store.db() as db:_,chunks=store._content(db,latest["id"])
    assert "21元" in chunks[0]["text"] and "2004年2月1日" in chunks[-1]["text"]
    fixture.payload=html("21",extra="<p>一、换领其他收费每证22元。</p>");schedule(store,now=clock+7400);worker_once(store,fixture,now=clock+7400)
    assert store.status()["counts"]["revisions"]==2
    assert "ambiguous" in store.status()["jobs"][0]["error"]
