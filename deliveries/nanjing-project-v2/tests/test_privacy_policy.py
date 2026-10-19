from copy import deepcopy

import pytest

from gov_mcp.privacy import data_classes_allowed


@pytest.mark.parametrize("label", ["private", "unknown", None, [], {}, True, "PUBLIC"])
@pytest.mark.parametrize("location", ["root", "child", "opaque", "tuple"])
def test_explicit_disallowed_labels_fail_closed(label, location):
    payload = {"data_class": "synthetic"}
    restricted = {"data_class": label, "content": "fixture"}
    if location == "root": payload = restricted
    elif location == "child": payload["evidence"] = [restricted]
    elif location == "opaque": payload["locator"] = {"source_ids": [restricted]}
    else: payload["evidence"] = (restricted,)
    before = deepcopy(payload)
    assert not data_classes_allowed(payload, {"synthetic", "public"})
    assert payload == before


def test_unlabeled_children_inherit_but_cannot_override_private_ancestor():
    child = {"text": "fixture"}
    payload = {"data_class": "synthetic", "evidence": [child, child]}
    assert data_classes_allowed(payload, {"synthetic"})
    payload["evidence"] = [{"data_class": "private", "nested": {"data_class": "synthetic"}}]
    assert not data_classes_allowed(payload, {"synthetic"})


def test_public_requires_explicit_opt_in_and_root_class_is_mandatory():
    payload = {"data_class": "synthetic", "evidence": [{"data_class": "public"}]}
    assert not data_classes_allowed(payload, {"synthetic"})
    assert data_classes_allowed(payload, {"synthetic", "public"})
    assert not data_classes_allowed({"evidence": [{"data_class": "synthetic"}]}, {"synthetic"})
    assert not data_classes_allowed([], {"synthetic"})
