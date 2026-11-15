"""Offline fake transport tests; no uploaded files, real keys or model quality claims."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from gov_mcp import multimodal_evidence as mm


def prepared(tmp_path):
    path = tmp_path / "synthetic.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic-test-fixture")
    plan = mm.upload_plan(path, "SYNTHETIC", "https://example.invalid/source.pdf", "a"*64, 5, "text_recognition")
    grant = mm.UploadGrant("synthetic test authorization, not user approval", plan["upload_sha256"],
                           plan["purpose"], True, mm.plan_fingerprint(plan))
    return path, plan, grant


def response():
    return {"id":"ocr-synthetic", "object":"ocr.result", "purpose":"text_recognition", "page_count":1,
            "pages":[{"index":0,"text":"Synthetic text","finish_reason":"stop"}]}


def test_ocr_candidates_keep_original_page_and_never_claim_human_review(tmp_path):
    _, plan, _ = prepared(tmp_path)
    payload = response()
    payload["pages"][0]["finish_reason"] = "length"
    page = mm.parse_ocr(payload, plan)[0]
    assert page["original_page"] == 5 and page["original_sha256"] == "a"*64
    assert page["complete"] is False and page["human_reviewed"] is False
    assert page["business_conclusion_status"] == "PAUSED_UNREVIEWED" and not page["formal_approval"]


@pytest.mark.parametrize("field,value", [("purpose","rich_layout_recognition"),("page_count",2),("object","file")])
def test_response_binding_and_page_counts_are_strict(tmp_path,field,value):
    _, plan, _ = prepared(tmp_path)
    payload = response();payload[field] = value
    with pytest.raises(ValueError):mm.parse_ocr(payload,plan)


def test_no_grant_or_changed_plan_cannot_call_transport(tmp_path):
    path, plan, grant = prepared(tmp_path)
    with pytest.raises(PermissionError):mm.recognize(path,plan,None,mm.Budget(1,1,.01,.01))
    changed = {**plan,"original_page":6}
    with pytest.raises(PermissionError):mm.recognize(path,changed,grant,mm.Budget(1,1,.01,.01))


def test_failures_are_reserved_no_retry_and_credentials_never_enter_errors(tmp_path):
    path, plan, grant = prepared(tmp_path)
    class Client:
        calls = 0
        def post(self,*args,**kwargs):
            self.calls += 1
            raise RuntimeError("synthetic-secret-that-must-not-leak")
    client=Client(); budget=mm.Budget(1,1,.01,.01)
    with pytest.raises(ValueError,match="POSSIBLY_BILLED") as error:
        mm.recognize(path,plan,grant,budget,client=client,environment={"DEEPSEEK_API_KEY":"synthetic-test-key"})
    assert "secret" not in str(error.value) and client.calls == 1
    with pytest.raises(ValueError,match="BUDGET_EXHAUSTED"):
        mm.recognize(path,plan,grant,budget,client=client,environment={"DEEPSEEK_API_KEY":"synthetic-test-key"})
    assert client.calls==1


def test_success_uses_only_the_official_multipart_contract(tmp_path):
    path,plan,grant=prepared(tmp_path)
    seen=[]
    class Client:
        def post(self,url,**kwargs):
            seen.append((url,kwargs));return SimpleNamespace(status_code=200,json=response)
    pages=mm.recognize(path,plan,grant,mm.Budget(1,1,.01,.01),client=Client(),environment={"DEEPSEEK_API_KEY":"synthetic-test-key"})
    assert seen[0][0]==mm.OCR_URL and seen[0][1]["data"]=={"purpose":"text_recognition"}
    assert set(seen[0][1]["files"])=={"file"} and pages[0]["complete"]


def test_unbounded_pdf_and_unknown_inputs_are_not_uploaded(tmp_path):
    path=tmp_path/'document.pdf';path.write_bytes(b'%PDF-synthetic')
    with pytest.raises(ValueError,match="PAGE_IMAGE_REQUIRED"):
        mm.upload_plan(path,"SYNTHETIC","https://example.invalid/a.pdf","a"*64,1,"text_recognition")


def test_vision_and_files_builders_have_no_network_side_effects():
    image=mm.vision_message(b'fake-fixture','image/png','Read synthetic image')
    assert image['content'][1]['type']=='image_url'
    file=mm.file_qa_message('file-api-synthetic','Read synthetic document')
    assert file['content'][0]=={'type':'file','file_id':'file-api-synthetic'}
