"""Offline test of the checked-out Nexent v2.8.2 import function with fake dependencies.

The actual function AST is compiled unchanged except for removing annotations.
All database, model, publish and network dependencies are replaced with in-memory
stubs. The test creates no agents or services and consumes no real credentials.
"""
import ast
import asyncio
import argparse
import hashlib
from pathlib import Path
from types import SimpleNamespace


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path, help="Path to Nexent v2.8.2 backend/management/services/agent/transfer.py")
SOURCE = parser.parse_args().source
source = SOURCE.read_text(encoding="utf-8")
tree = ast.parse(source)
fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "import_agent_by_agent_id")
fn.returns = None
for arg in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs):
    arg.annotation = None
if fn.args.vararg:
    fn.args.vararg.annotation = None
if fn.args.kwarg:
    fn.args.kwarg.annotation = None
module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))


def run_case(order, expected):
    recorded = []
    records = [dict(tool_id=n, class_name="govkb_ontology_crossdoc_retrieve", source="mcp",
                    usage=usage, params=[]) for n, usage in order]
    imported = SimpleNamespace(
        name="test_agent", display_name="test_agent", description="", author="test",
        tools=[SimpleNamespace(class_name="govkb_ontology_crossdoc_retrieve", source="mcp",
                               usage="nanjing_service", params={})],
        max_steps=3, model_ids=[], model_names=[], business_logic_model_id=None,
        business_logic_model_name=None, requested_output_tokens=100, provide_run_summary=False,
        allow_chat_metadata=False, prompt="test", enabled=False,
    )

    def validate(_class, _source, params, catalog_tool):
        if catalog_tool is None:
            raise ValueError("missing tool")
        return params

    env = {
        "validate_agent_name": lambda name: None,
        "query_all_tools": lambda **kw: records,
        "validate_import_tool_params": validate,
        "ToolInstanceInfoRequest": lambda **kw: SimpleNamespace(**kw),
        "_resolve_model_ids_with_fallback": lambda **kw: [],
        "_get_user_group_ids": lambda *args: [],
        "create_agent": lambda **kw: {"agent_id": 999},
        "create_or_update_tool_by_tool_info": lambda **kw: recorded.append(kw["tool_info"].tool_id),
        "publish_version_impl": lambda **kw: None,
    }
    exec(compile(module, str(SOURCE), "exec"), env)
    result = asyncio.run(env["import_agent_by_agent_id"](imported, "fake_tenant", "fake_user"))
    assert result == 999 and recorded == [expected], (result, recorded, expected)
    return recorded[0]


if __name__ == "__main__":
    first = run_case([(101, "nanjing_service"), (202, "henan_service")], 202)
    reversed_order = run_case([(202, "henan_service"), (101, "nanjing_service")], 101)
    print("normalized_source_sha256=" + hashlib.sha256(source.encode("utf-8")).hexdigest())
    print("input_usage=nanjing_service; catalog_order=nanjing,henan; selected_tool_id=" + str(first))
    print("input_usage=nanjing_service; catalog_order=henan,nanjing; selected_tool_id=" + str(reversed_order))
    print("scope=synthetic records; unchanged import function AST; stubbed external dependencies; no DB/network/model")

