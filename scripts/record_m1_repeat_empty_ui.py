import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone

from gov_mcp.extension import ExtensionStore
from nexent_api import ROOT, RUNTIME, NexentAPI


def main():
    store=ExtensionStore(RUNTIME/'m1/mcp/extension.sqlite3')
    before=hashlib.sha256(store.path.read_bytes()).hexdigest()
    proc=subprocess.run([shutil.which('npx.cmd') or 'npx','--yes','--package','@playwright/cli','playwright-cli',
        '-s=workbench','run-code','--filename','scripts/m1_repeat_empty_ui.js'],cwd=ROOT,
        capture_output=True,text=True,encoding='utf-8',timeout=60)
    if proc.returncode or '### Error' in proc.stdout:
        raise RuntimeError('Actual repeat-update UI operations did not succeed')
    value=json.loads(proc.stdout.split('### Result',1)[1].split('### Ran Playwright code',1)[0].strip())
    api=NexentAPI()
    try:user=api.login(json.loads((RUNTIME/'m1/accounts.json').read_text(encoding='utf8'))['developer'])
    finally:api.close()
    principal=hashlib.sha256(f"{user['tenant_id']}\0{user['user_id']}".encode()).hexdigest()
    audit=[json.loads(line) for line in (RUNTIME/'m1/mcp/tool-audit.jsonl').read_text(encoding='utf8').splitlines()]
    index={r['request_id']:r for r in audit}
    for entry in value['trace']:
        result=entry['result'];entry['audit']=index[result['data']['request_id']]
        assert result['status']=='EMPTY' and result['message_code']=='NO_CHANGES' and result['data']['input_count']==0
        assert result==entry['audit']['result'] and entry['audit']['principal_sha256']==principal
    after=hashlib.sha256(store.path.read_bytes()).hexdigest()
    assert len(value['trace'])==3 and before==after and not any(store.counts().values())
    value.update(passed=True,captured_at=datetime.now(timezone.utc).isoformat(),database_sha256_before=before,
        database_sha256_after=after,counts=store.counts(),principal_sha256=principal,
        screenshot_sha256=hashlib.sha256((ROOT/value['screenshot']).read_bytes()).hexdigest())
    (ROOT/'evidence/m1-repeat-empty-ui.json').write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    print('BT14 actual UI: three identical empty updates, zero data/candidate/version changes; screenshot and audits saved.')


if __name__=='__main__':main()
