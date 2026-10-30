"""Render a reviewed-ontology workflow Skill and controller protocol from a profile.

The template fixes the controller -> retriever -> verifier order, the five
shared ontology MCP tools and the stop rules. A profile only supplies its
scenario, agent names and evidence wording. Rendering and the provisioner
factory read local files only: no Nexent request, model call or network.
"""
import argparse
import importlib
import importlib.util
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "config/skill-templates/reviewed-ontology-workflow"
SKILLS = ROOT / "config/skills"
ROLES = ("controller", "retriever", "verifier")
SKILL_SLOTS = ("admission", "retrieve_detail", "verify_detail", "evidence_notes", "output_invariants")
KEYS = {"schema_version", "profile", "skill_name", "description", "title", "scenario_id", "agents",
        "display_prefix", "fingerprints", "skill", "protocol_notes"}
PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
PATTERNS = {"profile": r"[a-z0-9][a-z0-9-]{2,63}", "skill_name": r"[a-z0-9][a-z0-9-]{2,63}",
            "scenario_id": r"[a-z][a-z0-9_]{2,63}", "agent": r"[a-z][a-z0-9_]{2,63}",
            "fingerprints": r"gov_mcp\.[a-z_]+:[a-z_]+"}


def _unfilled(text):
    return "TODO" in text or PLACEHOLDER.search(text) is not None


def _lines(value, name):
    if (not isinstance(value, list) or not value
            or any(type(line) is not str or "\n" in line or "\r" in line for line in value)):
        raise ValueError("INVALID_PROFILE_SLOT: " + name)
    if any(_unfilled(line) for line in value):
        raise ValueError("UNFILLED_PROFILE_SLOT: " + name)
    return value


def validate(profile):
    """Reject unknown keys, unsafe names and unfilled wording before rendering."""
    if not isinstance(profile, dict) or set(profile) != KEYS or profile.get("schema_version") != 1:
        raise ValueError("INVALID_PROFILE_KEYS")
    for key in ("profile", "skill_name", "scenario_id", "fingerprints"):
        if type(profile[key]) is not str or not re.fullmatch(PATTERNS[key], profile[key]):
            raise ValueError("INVALID_PROFILE_FIELD: " + key)
    for key in ("description", "title", "display_prefix"):
        value = profile[key]
        if type(value) is not str or not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("INVALID_PROFILE_FIELD: " + key)
        if _unfilled(value):
            raise ValueError("UNFILLED_PROFILE_SLOT: " + key)
    agents = profile["agents"]
    if (not isinstance(agents, dict) or set(agents) != set(ROLES) or len(set(agents.values())) != len(ROLES)
            or any(type(name) is not str or not re.fullmatch(PATTERNS["agent"], name) for name in agents.values())):
        raise ValueError("INVALID_PROFILE_AGENTS")
    slots = profile["skill"]
    if not isinstance(slots, dict) or set(slots) != set(SKILL_SLOTS):
        raise ValueError("INVALID_PROFILE_SKILL_SLOTS")
    for slot in SKILL_SLOTS:
        _lines(slots[slot], slot)
    # The admission text continues the fixed "is admitted" sentence.
    if slots["admission"][0][:1] not in (":", "."):
        raise ValueError("INVALID_PROFILE_SLOT: admission")
    _lines(profile["protocol_notes"], "protocol_notes")
    return profile


def load_profile(path):
    return validate(json.loads(Path(path).read_text("utf-8")))


def _template(name):
    return (TEMPLATE / name).read_text("utf-8").replace("\r\n", "\n")


def _fill(text, values):
    if set(PLACEHOLDER.findall(text)) != set(values):
        raise ValueError("TEMPLATE_SLOTS_MISMATCH")
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], text)


def _continued(lines, indent):
    return lines[0] + "".join("\n" + indent + line for line in lines[1:])


def render_skill(profile):
    validate(profile)
    slots = profile["skill"]
    return _fill(_template("SKILL.template.md"), {
        "skill_name": profile["skill_name"], "description": profile["description"],
        "title": profile["title"], "scenario_id": profile["scenario_id"],
        "retriever_name": profile["agents"]["retriever"], "verifier_name": profile["agents"]["verifier"],
        "admission": _continued(slots["admission"], ""),
        "retrieve_detail": " " + _continued(slots["retrieve_detail"], "   "),
        "verify_detail": " " + _continued(slots["verify_detail"], "   "),
        "evidence_notes": "\n".join(slots["evidence_notes"]),
        "output_invariants": "\n".join(slots["output_invariants"])})


def render_protocol(profile):
    validate(profile)
    return _fill(_template("PROTOCOL.template.txt").rstrip("\n"), {
        "skill_name": profile["skill_name"], "scenario_id": profile["scenario_id"],
        "retriever_name": profile["agents"]["retriever"], "verifier_name": profile["agents"]["verifier"],
        "protocol_notes": "\n".join(profile["protocol_notes"])})


def fingerprint_function(profile):
    module_name, attribute = validate(profile)["fingerprints"].split(":")
    return getattr(importlib.import_module(module_name), attribute)


def profile_provisioner(profile, runtime, output):
    """Return a fresh provisioner module bound to this profile.

    This constructs configuration only, like the Henan profile loader: callers
    still invoke the module's service, ownership, collision, Skill and draft
    checks. The shared module's globals and records stay unchanged.
    """
    validate(profile)
    path = ROOT / "scripts/provision_ontology_crossdoc_native.py"
    name = "_workflow_profile_" + profile["profile"].replace("-", "_")
    specification = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    module.RUNTIME, module.OUTPUT = Path(runtime), Path(output)
    module.SCENARIO, module.SKILL = profile["scenario_id"], profile["skill_name"]
    module.NAMES = dict(profile["agents"])
    module.DISPLAY_PREFIX = profile["display_prefix"]
    module.PROTOCOL = render_protocol(profile)
    module.code_fingerprints = fingerprint_function(profile)
    # The original default was bound when the module executed.
    original_first_action, scenario = module.first_action, profile["scenario_id"]

    def first_action(scenario_id=scenario):
        return original_first_action(scenario_id)

    module.first_action = first_action
    return module


def skill_status(profile, skills_root=SKILLS):
    path = Path(skills_root) / profile["skill_name"] / "SKILL.md"
    if not path.is_file():
        return path, "missing"
    current = path.read_text("utf-8").replace("\r\n", "\n")
    return path, "identical" if current == render_skill(profile) else "differs"


def write_skill(profile, skills_root=SKILLS):
    """Write a new Skill; an existing different Skill is never overwritten."""
    path, status = skill_status(profile, skills_root)
    if status == "differs":
        raise ValueError("SKILL_EXISTS_WITH_DIFFERENT_CONTENT: " + profile["skill_name"])
    if status == "missing":
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(render_skill(profile))
    return path, "unchanged" if status == "identical" else "written"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "render"))
    parser.add_argument("--profile", required=True, help="Profile JSON path")
    parser.add_argument("--skills-root", default=str(SKILLS))
    args = parser.parse_args()
    profile = load_profile(args.profile)
    if args.action == "check":
        path, status = skill_status(profile, args.skills_root)
    else:
        path, status = write_skill(profile, args.skills_root)
    print(json.dumps({"profile": profile["profile"], "skill": profile["skill_name"], "skill_path": str(path),
                      "skill_status": status, "protocol_lines": len(render_protocol(profile).split("\n")),
                      "network_calls": 0, "model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
