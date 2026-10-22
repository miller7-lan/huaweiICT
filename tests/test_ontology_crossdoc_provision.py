"""Isolated metadata provisioning checks; no service, platform, or model requests."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import provision_ontology_crossdoc_native as provision


class FakeAPI:
    def __init__(self, skill_text, legacy_count=0):
        self.skill_text = skill_text
        self.agents = {i: {"agent_id": i, "tenant_id": "mock-tenant", "name": "existing_" + str(i), "enabled": True,
                           "model_params_override": {"7": {"temperature": .2}}}
                       for i in range(1, legacy_count + 1)}
        self.versions = {i: {"total": 1, "data": [{"version_no": 1}]} for i in self.agents}
        self.tools = [{"tool_id": i, "name": name, "source": "mcp", "usage": provision.SERVICE,
                       "is_available": True} for i, name in enumerate(sorted(provision.TOOLS), 11)]
        self.models = [{"model_id": 7, "model_name": "deepseek-flash",
                        "base_url": "https://api.deepseek.com/v1", "connect_status": "available"}]
        self.services, self.skills, self.calls = [], {}, []
        self.next_agent = 101
        self.base, self.token = "http://127.0.0.1:5010", "mock-local-token"
        self.http = SimpleNamespace(post=self.upload)
        self.mutate_other = False

    def upload(self, url, headers, files, data):
        assert url == self.base + "/skills/upload"
        assert files["file"][1] == self.skill_text.encode()
        self.calls.append(("POST", "/skills/upload"))
        self.skills[50] = {"skill_id": 50, "name": provision.SKILL,
                           "content": self.skill_text.split("---", 2)[2].strip()}
        return SimpleNamespace(status_code=200, json=lambda: {"skill_id": 50})

    def call(self, method, path, body=None):
        self.calls.append((method, path))
        if path.startswith("/agent/list?tenant_id="):
            # Match pinned Nexent: list and list/page hide disabled drafts.
            return [{"agent_id": i, "name": value["name"]} for i, value in self.agents.items()
                    if value.get("enabled") is True]
        if path == "/agent/check_name":
            results = []
            for item in body["items"]:
                others = [agent for identifier, agent in self.agents.items()
                          if identifier != item.get("agent_id")]
                names = [agent for agent in others if agent.get("name") == item["name"]]
                displays = [agent for agent in others
                            if item.get("display_name") and agent.get("display_name") == item["display_name"]]
                results.append({"name_conflict": bool(names), "display_name_conflict": bool(displays),
                                "conflict_agents": [{"name": agent.get("name"), "display_name": agent.get("display_name")}
                                                    for agent in others if agent in names or agent in displays]})
            return results
        if path == "/agent/search_info":
            return deepcopy(self.agents[body["agent_id"]])
        if path.startswith("/agent/") and path.endswith("/versions"):
            return deepcopy(self.versions[int(path.split("/")[2])])
        if path == "/model/list":
            return {"data": deepcopy(self.models)}
        if path == "/skills":
            return {"skills": deepcopy(list(self.skills.values()))}
        if path.startswith("/skills/"):
            return deepcopy(self.skills[int(path.split("/")[2])])
        if path == "/mcp/list":
            return {"remote_mcp_server_list": deepcopy(self.services)}
        if path == "/mcp/add":
            self.services.append({"remote_mcp_server_name": body["name"],
                                  "remote_mcp_server_url": body["server_url"]})
            return {"success": True}
        if path == "/tool/scan_tool":
            return {}
        if path == "/tool/list":
            return deepcopy(self.tools)
        if path == "/agent/update":
            assert "agent_id" not in body, "Existing agents must not be rewritten"
            identifier = self.next_agent
            self.next_agent += 1
            info = deepcopy(body)
            info.update(agent_id=identifier, tenant_id="mock-tenant")
            info["tools"] = [{k: t[k] for k in ("tool_id", "name", "source", "usage")}
                             for t in self.tools if t["tool_id"] in body["enabled_tool_ids"]]
            info["skills"] = [{"skill_id": i} for i in body["enabled_skill_ids"]]
            info["sub_agent_id_list"] = body["related_agent_ids"]
            self.agents[identifier] = info
            self.versions[identifier] = {"total": 0, "data": []}
            if self.mutate_other:
                self.agents[1]["enabled"] = False
            return {"agent_id": identifier}
        pytest.fail("Unexpected endpoint (must not run/connect/publish): " + method + " " + path)


@pytest.fixture
def environment(monkeypatch, tmp_path):
    skill = (ROOT / "config/skills" / provision.SKILL / "SKILL.md").read_text("utf-8")
    monkeypatch.setattr(provision, "ROOT", tmp_path)
    monkeypatch.setattr(provision, "RUNTIME", tmp_path / ".runtime/ontology-native")
    monkeypatch.setattr(provision, "OUTPUT", tmp_path / "evidence")
    provision.write(tmp_path / ".runtime/nexent-state.json", {"tenant_id": "mock-tenant", "group_id": 2})
    source = tmp_path / "config/skills" / provision.SKILL / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_bytes(skill.encode("utf-8"))
    return skill


def test_default_plan_does_not_construct_network_client_or_touch_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(provision, "OUTPUT", tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("Default plan must not touch a platform or runtime")
    monkeypatch.setattr(provision, "NexentAPI", forbidden)
    monkeypatch.setattr(provision, "check_service", forbidden)
    monkeypatch.setattr(provision, "ensure_private_directory", forbidden)
    assert provision.main([]) == 0
    plan = json.loads((tmp_path / "provision-plan.json").read_text("utf-8"))
    assert plan["api_requests_this_plan"] == plan["inference_calls_this_plan"] == 0
    assert plan["protocol_repair_enabled"] and plan["protocol_format_repairs_max"] == 2
    assert plan["protection_scope"] == "visible_tenant_agents_plus_project_native_draft_ids"
    assert plan["port"] == 8785


@pytest.mark.parametrize("legacy_count", [0, 2])
def test_metadata_provision_preserves_every_other_agent_and_is_idempotent(environment, legacy_count):
    api = FakeAPI(environment, legacy_count)
    original = deepcopy(api.agents)
    saved = {"agents": {}, "published": False}
    result = provision.provision(api, saved, "mock-bearer")
    assert result["other_agents_protected"] == legacy_count
    assert len(saved["other_signatures"]) == legacy_count
    assert all(api.agents[i] == value for i, value in original.items())
    assert len(saved["agents"]) == 3
    assert not saved["published"] and result["inference_calls"] == 0
    assert saved == provision.read(provision.RUNTIME / "native-drafts.json")
    readonly_posts = {"/agent/search_info", "/agent/check_name"}
    mutations = len([c for c in api.calls if c[0] != "GET" and c[1] not in readonly_posts])
    provision.provision(api, saved, "mock-bearer")
    assert len([c for c in api.calls if c[0] != "GET" and c[1] not in readonly_posts]) == mutations
    assert all("connect" not in p and "publish" not in p and "/run" not in p for _, p in api.calls)
    for role, identifier in saved["agents"].items():
        info = api.agents[identifier]
        assert info["enabled"] is False and api.versions[identifier]["total"] == 0
        assert info["max_steps"] == (6 if role == "controller" else 3)
        assert info["enable_protocol_repair_retry"] is True
        assert info["requested_output_tokens"] == 1024


@pytest.mark.parametrize("collision", ["agent", "skill", "model", "service"])
def test_preflight_rejects_collisions_before_any_mutation(environment, collision):
    api = FakeAPI(environment)
    if collision == "agent":
        api.agents[5] = {"name": provision.NAMES["verifier"]}
    elif collision == "skill":
        api.skills[90] = {"skill_id": 90, "name": provision.SKILL}
    elif collision == "model":
        api.models.append(deepcopy(api.models[0]))
    else:
        api.services = [{"remote_mcp_server_name": provision.SERVICE,
                         "remote_mcp_server_url": "http://host.docker.internal:8783/sse"}]
    with pytest.raises(RuntimeError):
        provision.provision(api, {}, "mock-bearer")
    assert not any(method == "POST" and path not in {"/agent/search_info", "/agent/check_name"}
                   for method, path in api.calls)


@pytest.mark.parametrize("change", ["tools", "skills", "children", "model", "enabled", "group", "protocol", "published"])
def test_owned_draft_rejects_role_permission_or_parameter_drift(environment, change):
    api = FakeAPI(environment)
    saved = {"agents": {}}
    provision.provision(api, saved, "mock-bearer")
    identifier = saved["agents"]["controller"]
    info = api.agents[identifier]
    if change == "tools":
        info["tools"].append({"tool_id": 999, "name": "unrelated", "source": "mcp", "usage": provision.SERVICE})
    elif change == "skills":
        info["skills"].append({"skill_id": 999})
    elif change == "children":
        info["sub_agent_id_list"].append(999)
    elif change == "model":
        info["model_params_override"]["7"]["temperature"] = .1
    elif change == "enabled":
        info["enabled"] = True
    elif change == "group":
        info["ingroup_permission"] = "EDIT"
    elif change == "protocol":
        info["enable_protocol_repair_retry"] = False
    else:
        api.versions[identifier] = {"total": 1, "data": [{"version_no": 1}]}
    with pytest.raises(RuntimeError):
        provision.check_drafts(api, saved)


def test_modified_skill_and_other_agent_are_not_silently_adopted(environment):
    api = FakeAPI(environment, 1)
    saved = {"agents": {}}
    provision.provision(api, saved, "mock-bearer")
    api.skills[50]["content"] += "\nUnreviewed changed content"
    with pytest.raises(RuntimeError, match="SKILL_CONTENT_OR_HASH_CHANGED"):
        provision.check_skill(api, saved)
    api.skills[50]["content"] = environment.split("---", 2)[2].strip()
    api.agents[1]["enabled"] = False
    with pytest.raises(RuntimeError, match="OTHER_AGENT_STATE_CHANGED"):
        provision.provision(api, saved, "mock-bearer")


def test_post_write_other_agent_mutation_is_detected(environment):
    api = FakeAPI(environment, 1)
    api.mutate_other = True
    with pytest.raises(RuntimeError, match="OTHER_AGENTS_CHANGED"):
        provision.provision(api, {"agents": {}}, "mock-bearer")


@pytest.mark.parametrize("fault", [None, "hash", "unauthenticated", "service"])
def test_service_readiness_requires_source_hash_and_bearer_boundary(monkeypatch, fault):
    client_type = httpx.Client
    def handle(request):
        if request.url.path == "/health":
            health = {"service": provision.SERVICE, "status": "UP", "publication_enabled": False,
                      "startup_source_sha256": provision.code_fingerprints()}
            if fault == "hash":
                health["startup_source_sha256"] = {}
            if fault == "service":
                health["service"] = "old_preview"
            return httpx.Response(200, json=health)
        assert request.url.path == "/sse" and "authorization" not in request.headers
        return httpx.Response(200 if fault == "unauthenticated" else 401)
    def client(**kwargs):
        assert kwargs["trust_env"] is False
        return client_type(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(provision.httpx, "Client", client)
    if fault:
        with pytest.raises(RuntimeError):
            provision.check_service()
    else:
        assert provision.check_service()["service"] == provision.SERVICE


def test_native_prompt_uses_strings_and_exact_stage_tools():
    assert "JSON STRINGS" in provision.PROTOCOL
    assert "govontologycrossdoc_retriever(task=" in provision.PROTOCOL
    assert "govontologycrossdoc_verifier(task=" in provision.PROTOCOL
    assert "final_answer(result)" in provision.PROTOCOL
    assert provision.SKILL in provision.first_action()
    assert "scenario_id='nanjing_outside_old_card'" in provision.first_action()
    with pytest.raises(ValueError, match="FIXED_SCENARIO_REQUIRED"):
        provision.first_action("beijing")


def test_known_disabled_drafts_are_protected_and_deduplicated(environment):
    api = FakeAPI(environment, 3)
    api.agents[2]["enabled"] = False
    api.agents[3]["enabled"] = False
    provision.write(provision.ROOT / ".runtime/legacy-a/native-drafts.json",
                    {"agents": {"old_disabled": 2}, "ignored_private_field": "not part of signatures"})
    provision.write(provision.ROOT / ".runtime/legacy-b/native-drafts.json",
                    {"agents": {"same_old_disabled": 2, "another_disabled": 3}})
    before = provision.other_signatures(api)
    assert set(before) == {"1", "2", "3"}
    assert provision.known_project_agent_ids() == {2, 3}
    api.agents[3]["model_params_override"]["7"]["temperature"] = .9
    after = provision.other_signatures(api)
    assert before["1"] == after["1"] and before["2"] == after["2"] and before["3"] != after["3"]


@pytest.mark.parametrize("collision", ["name", "display_name"])
def test_check_name_finds_untracked_disabled_name_collisions(environment, collision):
    api = FakeAPI(environment)
    api.agents[500] = {"agent_id": 500, "tenant_id": "mock-tenant", "enabled": False,
                       "name": "unrelated_hidden", "display_name": "unrelated_hidden"}
    if collision == "name":
        api.agents[500]["name"] = provision.NAMES["verifier"]
    else:
        api.agents[500]["display_name"] = "已复核本体跨文档预览 verifier"
    with pytest.raises(RuntimeError, match="NAME_OR_DISPLAY_COLLISION"):
        provision.provision(api, {"agents": {}}, "mock-bearer")
    assert api.calls == [("POST", "/agent/check_name")]


@pytest.mark.parametrize("fault", ["tenant", "returned_id", "invalid_ledger_id"])
def test_known_agent_ids_require_current_tenant_and_exact_id(environment, fault):
    api = FakeAPI(environment, 1)
    if fault == "tenant":
        api.agents[1]["tenant_id"] = "shared-asset-owner"
    elif fault == "returned_id":
        api.agents[1]["agent_id"] = 999
    else:
        provision.write(provision.ROOT / ".runtime/invalid/native-drafts.json", {"agents": {"bad": True}})
    with pytest.raises(RuntimeError, match="TENANT_AGENT_ID|INTEGER_AGENT_ID"):
        provision.other_signatures(api)


def test_explicit_extension_preserves_original_baseline_and_is_readonly(environment):
    api = FakeAPI(environment, 2)
    saved = {"agents": {}}
    provision.provision(api, saved, "mock-bearer")
    original_signatures = deepcopy(saved["other_signatures"])
    original_saved = deepcopy(saved)
    on_disk = (provision.RUNTIME / "native-drafts.json").read_bytes()
    # A previously omitted disabled project draft becomes discoverable through
    # its existing ledger; it must be newly dated, not folded into old evidence.
    api.agents[5] = {"agent_id": 5, "tenant_id": "mock-tenant", "name": "old_disabled_5", "enabled": False}
    api.versions[5] = {"total": 0, "data": []}
    provision.write(provision.ROOT / ".runtime/old-hidden/native-drafts.json", {"agents": {"hidden": 5}})
    with pytest.raises(RuntimeError, match="PROTECTION_EXPANSION_REQUIRED"):
        provision.provision(api, saved, "mock-bearer")
    before_calls = len(api.calls)
    migrated = provision.extend_other_protection(api, saved, reason="Disabled drafts omitted by old visible-list baseline")
    assert migrated["other_signatures"] == original_signatures and saved == original_saved
    assert (provision.RUNTIME / "native-drafts.json").read_bytes() == on_disk
    extension = migrated["other_signature_extensions"][0]
    assert set(extension["added_signatures"]) == {"5"} and extension["recorded_at"]
    assert extension["newly_covered_agents_not_claimed_previously_protected"]
    assert set(provision.effective_other_signatures(migrated)) == {"1", "2", "5"}
    assert all(path != "/agent/update" and path != "/mcp/add" for _, path in api.calls[before_calls:])
    assert provision.extend_other_protection(api, migrated, reason="Already covered") == migrated
    api.agents[1]["enabled"] = False
    with pytest.raises(RuntimeError, match="OTHER_AGENT_STATE_CHANGED"):
        provision.extend_other_protection(api, saved, reason="Must reject changed original baseline")
