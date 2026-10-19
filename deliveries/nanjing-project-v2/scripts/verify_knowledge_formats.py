"""Actual Linux parser/OCR verification using the existing synthetic source image."""

import json
import subprocess

from nexent_api import ROOT, RUNTIME


def main():
    import shutil
    image=RUNTIME/'preparation/demo/notice.png'
    if not image.exists(): raise RuntimeError('Run setup_topic3_preparation.ps1 to create the synthetic image first')
    shutil.copyfile(image,RUNTIME/'knowledge-demo/ocr-fixture.png')
    code='''import json
from pathlib import Path
from gov_mcp.knowledge.store import Store
from gov_mcp.knowledge.sources import Fetcher
store=Store('/runtime')
f=Fetcher(store,synthetic=True)
s={'id':'fixture','title':'模拟附件','url':'http://fixture.test/attachment','data_class':'synthetic','body_selector':{'tag':'article'}}
report={'scope':'actual Linux text, CSV and RapidOCR parser checks on synthetic originals','formats':{}}
for fmt,raw in [('txt','模拟申请表需要签字。'.encode()),('csv','材料,要求\\n申请表,本人签字\\n'.encode()),('png',Path('/runtime/ocr-fixture.png').read_bytes())]:
    body,assets,sha=f.parse(s,{'url':s['url'],'format':fmt},raw,'image/png' if fmt=='png' else 'text/plain')
    texts=[row['content'] for a in assets for row in a['segments']]
    report['formats'][fmt]={'segments':len(texts),'contains_expected_text':any('申请表' in t for t in texts),'raw_sha256':sha,'all_locators_preserved':all(row['locator'] for a in assets for row in a['segments'])}
report['passed']=all(r['contains_expected_text'] and r['all_locators_preserved'] for r in report['formats'].values())
print(json.dumps(report,ensure_ascii=True))
'''
    response=subprocess.run(['docker','exec','govkb-demo','python','-c',code],capture_output=True,text=True,encoding='utf-8',timeout=90)
    if response.returncode:
        print(json.dumps({'passed':False,'error':'PARSER_EXECUTION_FAILED'}))
        return 1
    report=json.loads(response.stdout)
    destination=ROOT/'evidence/knowledge-heartbeat/parser-verification.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
