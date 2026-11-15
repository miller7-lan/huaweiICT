"""Synthetic isolated fixtures only; never records the user's actual approval."""
import pytest

from test_ontology_evolution import workspace, seed, ACTOR, approve


def test_rejection_is_durable_and_never_activates_or_resubmits(workspace):
    engine, source = workspace
    candidate = engine.submit()
    head = engine.status()["head"]
    result = engine.reject(candidate, head, candidate, ACTOR, "Synthetic fixture: incompatible candidate", confirmed=True)
    assert result["status"] == "rejected" and result["head"] == head
    assert engine.status()["head"] == head and not result["formally_published"]
    assert result["business_conclusion_status"] == "PAUSED_UNREVIEWED"
    assert engine.submit() == candidate
    assert engine.status()["candidates"][0]["status"] == "rejected"
    with pytest.raises(ValueError, match="NOT_PENDING"):
        engine.review(candidate, head, candidate, ACTOR, confirmed=True)


@pytest.mark.parametrize("bad", ["actor", "confirmation", "head", "digest", "reason"])
def test_rejection_requires_explicit_human_digest_head_and_reason(workspace, bad):
    engine, _ = workspace
    candidate = engine.submit()
    head = engine.status()["head"]
    args = dict(candidate_id=candidate, expected_head=head, candidate_sha256=candidate, actor=ACTOR,
                reason="Synthetic fixture reason", confirmed=True)
    args.update({"actor":{"kind":"model","name":"fixture"}} if bad == "actor" else
                {"confirmed":False} if bad == "confirmation" else {"expected_head":"0"*64} if bad == "head" else
                {"candidate_sha256":"0"*64} if bad == "digest" else {"reason":" "})
    with pytest.raises(ValueError):
        engine.reject(**args)
    assert engine.status()["head"] == head and engine.status()["candidates"][0]["status"] == "pending_review"


def test_stale_source_can_be_rejected_without_reviving_or_reading_it(workspace):
    engine, source = workspace
    candidate = engine.submit()
    head = engine.status()["head"]
    source.error = "SOURCE_PAUSED"
    assert engine.reject(candidate, head, candidate, ACTOR, "Synthetic fixture: source paused", confirmed=True)["status"] == "rejected"


def test_accepted_candidate_cannot_be_rejected_as_if_pending(workspace):
    engine, _ = workspace
    _, candidate, active = approve(engine)
    with pytest.raises(ValueError, match="NOT_PENDING"):
        engine.reject(candidate, active, candidate, ACTOR, "Synthetic fixture", confirmed=True)
    assert engine.status()["head"] == active


def test_incomplete_cli_rejection_cannot_even_open_the_workspace(monkeypatch):
    import run_ontology_evolution as cli
    monkeypatch.setattr("sys.argv", ["run_ontology_evolution.py", "reject"])
    monkeypatch.setattr(cli, "open_workspace", lambda: pytest.fail("Workspace must stay unopened"))
    with pytest.raises(ValueError, match="HUMAN_CONFIRMATION"):
        cli.main()
