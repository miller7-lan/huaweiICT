"""Cross-machine reproduction helpers: originals archive and deterministic fingerprint (synthetic data)."""
import json
from hashlib import sha256
from zipfile import ZipFile

import pytest

import pack_frozen_originals as packer
import reproduction_fingerprint as fp
from test_frozen_qa_flow_v3 import QUESTION, RENEW, UNITS, WRONG, answer


def test_fingerprint_is_stable_and_names_what_differs():
    questions = {"dev": [{"id": "Q1", "question": QUESTION}, {"id": "Q2", "question": "现在换领多少钱"}]}
    first = fp.fingerprint(UNITS, questions)
    assert first == fp.fingerprint(UNITS, questions) and fp.compare(first, first) == []
    assert first["question_sets"]["dev"]["rows"][1]["gate"] == "CURRENT_VALIDITY_NOT_ESTABLISHED"
    assert QUESTION not in json.dumps(first, ensure_ascii=False)
    changed = [dict(u, text=u["text"] + "。") if u["locator"] == "正文段落1" and u["source_id"] == "DOC-RENEW" else u
               for u in UNITS]
    changed = [dict(u, text_sha256=sha256(u["text"].encode()).hexdigest()) for u in changed]
    differ = fp.compare(first, fp.fingerprint(changed, questions))
    assert "corpus_unit_sha256" in differ and "question:dev:Q1" in differ and "question:dev:Q2" not in differ


def test_replay_rows_are_part_of_the_fingerprint():
    cited_unit = next(unit for unit in UNITS if (unit["source_id"], unit["locator"]) == RENEW[1:3])
    record = {"question_id": "Q1", "compared_as": "flow_v3", "question": QUESTION,
              "model_output": answer("到期换领工本费20元。", RENEW),
              "retrieval": {"evidence": [{"rank": 1, "source_id": cited_unit["source_id"],
                                          "locator": cited_unit["locator"], "text_sha256": cited_unit["text_sha256"]}]}}
    good = fp.fingerprint(UNITS, {}, [record])
    assert good["replay"]["rows"][0]["replayed"] == "answer"
    bad = fp.fingerprint(UNITS, {}, [{**record, "model_output": answer("到期换领工本费60元。", WRONG)}])
    assert fp.compare(good, bad) == ["replay"]


def test_unpack_checks_every_member_against_the_catalogue(tmp_path, monkeypatch):
    raw = b"<html>original</html>"
    digest = sha256(raw).hexdigest()
    monkeypatch.setattr(packer, "expected_originals", lambda: {digest: "DOC-A"})
    archive = tmp_path / "originals.zip"
    with ZipFile(archive, "w") as handle:
        handle.writestr(digest, raw)
    placed = packer.unpack(archive, tmp_path / "originals")
    assert placed == [{"source_id": "DOC-A", "sha256": digest, "bytes": len(raw)}]
    assert (tmp_path / "originals" / digest).read_bytes() == raw
    forged = tmp_path / "forged.zip"
    with ZipFile(forged, "w") as handle:
        handle.writestr(digest, b"changed")
    with pytest.raises(ValueError, match="MEMBER_HASH_MISMATCH"):
        packer.unpack(forged, tmp_path / "other")
    extra = tmp_path / "extra.zip"
    with ZipFile(extra, "w") as handle:
        handle.writestr(digest, raw)
        handle.writestr("0" * 64, b"x")
    with pytest.raises(ValueError, match="ARCHIVE_MEMBERS_DO_NOT_MATCH_CATALOGUE"):
        packer.unpack(extra, tmp_path / "third")


def test_the_catalogue_names_one_original_per_non_scan_source():
    expected = packer.expected_originals()
    assert len(expected) == 6 and "HN-MEASURES-2021-PDF" not in expected.values()
