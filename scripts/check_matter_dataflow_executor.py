"""Check the installed adapter/executor with explicit tool fixtures, never a model."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gov_mcp.matter_native import native_profile
from provision_matter_native import dataflow_action

CONTAINER_CHECK = r'''
import json,sys,uuid
from mcp.types import Tool,CallToolResult,TextContent
from mcpadapt.smolagents_adapter import SmolAgentsAdapter
from smolagents.local_python_executor import LocalPythonExecutor
from nexent.core.agents.nexent_agent import get_local_python_authorized_imports
payload=json.load(sys.stdin);code=payload['code'];rows=[]
for failure in (None,'blocked_open','child_prose_only','pause_before_final'):
    task_id='task-'+uuid.uuid4().hex;calls=[];stage={'value':'new'}
    expected=json.dumps({'task_id':task_id,'status':'DRAFT_PROOF_READY','human_review':'pending','knowledge_published':False,'formal_business_answer':False})
    def adapted(name,operation,argument):
        def invoke(args):
            calls.append(name);value=operation(args[argument])
            return CallToolResult(content=[TextContent(type='text',text=json.dumps(value))],structuredContent=value)
        return SmolAgentsAdapter(structured_output=False).adapt(invoke,Tool(name=name,inputSchema={'type':'object','properties':{argument:{'type':'string'}},'required':[argument]}))
    def open_task(scenario):
        assert scenario=='nanjing-adult-valid-old-card'
        if failure=='blocked_open':return {'status':'BLOCKED'}
        stage['value']='opened';return {'task_id':task_id,'status':'opened'}
    def retrieve(task):
        calls.append('delegate_retriever');assert json.loads(task)['task_id']==task_id and stage['value']=='opened';stage['value']='retrieved';return 'fixture child result'
    def verify(task):
        calls.append('delegate_verifier');assert json.loads(task)['task_id']==task_id and stage['value']=='retrieved'
        if failure!='child_prose_only':stage['value']='verified'
        return 'fixture child result'
    def state(identifier):
        assert identifier==task_id
        if stage['value']!='verified':return {'status':'BLOCKED'}
        stage['value']='paused' if failure=='pause_before_final' else 'acknowledged'
        return {'task_id':task_id,'can_finalize':True}
    def finish(identifier):
        assert identifier==task_id
        return json.loads(expected) if stage['value']=='acknowledged' else {'status':'BLOCKED'}
    tools={'read_skill_md':lambda **kw:calls.append('skill'),'govmatter_open':adapted('govmatter_open',open_task,'scenario_id'),
        'govmatterflow_retriever':retrieve,'govmatterflow_verifier':verify,'govmatter_state':adapted('govmatter_state',state,'task_id'),
        'govmatter_finalize':adapted('govmatter_finalize',finish,'task_id'),'final_answer':lambda result:result}
    executor=LocalPythonExecutor(additional_authorized_imports=get_local_python_authorized_imports());executor.send_tools(tools)
    rejected=False
    try: result=executor(code)
    except Exception:rejected=True
    if failure is None:
        assert not rejected and result.is_final_answer and result.output==expected
        assert calls==['skill','govmatter_open','delegate_retriever','delegate_verifier','govmatter_state','govmatter_finalize']
    else:
        assert rejected
        if failure=='blocked_open':assert calls==['skill','govmatter_open']
        if failure=='child_prose_only':assert 'govmatter_finalize' not in calls
    rows.append({'fixture':failure or 'valid_json_text_dataflow','passed':True,'calls':calls})
print(json.dumps({'status':'PASSED','explicit_tool_fixtures':True,'real_model_calls':0,'real_mcp_network_calls':0,'real_child_agent_invocations':0,'installed_adapter_executor_used':True,'checks':rows}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="nexent-runtime")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.receipt.exists(): parser.error("Existing diagnostic receipt preserved")
    action = dataflow_action(native_profile("dataflow-v4"))
    code = action.removeprefix("<code>\n").removesuffix("\n</code>")
    completed = subprocess.run(["docker", "exec", "-i", args.container, "python", "-c", CONTAINER_CHECK],
        input=json.dumps({"code": code}).encode(), capture_output=True, timeout=60)
    if completed.returncode:
        sys.stderr.write(completed.stderr.decode(errors="replace")[-3000:])
        return 1
    report = json.loads(completed.stdout.decode().splitlines()[-1])
    report.update(action_sha256=hashlib.sha256(action.encode()).hexdigest(), human_business_review=False,
        formal_publication=False, independent_evaluation=False)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_bytes((json.dumps(report, indent=2) + "\n").encode())
    print(json.dumps(report))
    return 0


if __name__ == "__main__": raise SystemExit(main())
