"""Generate readable developer diagrams from source; no business module imports."""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / '开发者流程图-2026-10-19'
RUNTIME = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
D = 'src/gov_mcp/decision/'
K = 'src/gov_mcp/'
GRAPHS = []
SOURCES = {}
COLORS = {'code': ('#edf4ff', '#3d6fa6'), 'gate': ('#fff3d9', '#b8852b'), 'data': ('#eaf6f1', '#478570'), 'error': ('#fff0ef', '#b9625c')}


def reference(value):
    if value in SOURCES:
        return SOURCES[value]
    path, symbol = value.split(':', 1)
    raw = (ROOT / path).read_bytes()
    source = raw.decode('utf-8-sig')
    tree = ast.parse(source)
    found = []
    def visit(parent, prefix=''):
        for node in ast.iter_child_nodes(parent):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = prefix + node.name
                if name == symbol: found.append(node)
                visit(node, name + '.')
            else:
                visit(node, prefix)
    visit(tree)
    assert len(found) == 1, value
    node = found[0]
    result = {'file': path, 'symbol': symbol, 'line': node.lineno, 'end': node.end_lineno,
              'sha256': hashlib.sha256(raw).hexdigest(),
              'source': '\n'.join(source.splitlines()[node.lineno-1:node.end_lineno])}
    SOURCES[value] = result
    return result


def n(key, name, detail, ref, kind='code'):
    return {'id': key, 'name': name, 'detail': detail, 'ref': ref, 'kind': kind}


def flow(key, title, intent, nodes, edges, direction='TB', groups=None):
    GRAPHS.append({'id': key, 'title': title, 'intent': intent, 'kind': 'flow', 'nodes': nodes,
                   'edges': edges, 'direction': direction, 'groups': groups or []})


def sequence(key, title, intent, actors, steps):
    GRAPHS.append({'id': key, 'title': title, 'intent': intent, 'kind': 'sequence', 'actors': actors, 'steps': steps})


def step(a, b, label, ref, kind='call'):
    return {'from': a, 'to': b, 'label': label, 'ref': ref, 'kind': kind}


def define():
    flow('01-modules', '01 · 代码模块与调用关系', '先看执行入口如何连到代码和存储。虚线表示独立链路，不能理解成已自动互通。', [
        n('web', 'decision.server.create_app', '8780网页与MCP入口；认证、参数检查、响应封装。', D+'server.py:create_app'),
        n('form', 'intake.GuidedForm', '本机会话草稿；版本、AI格式核查、人工确认后进入Workflow.query。', D+'intake.py:GuidedForm'),
        n('wf', 'workflow.Workflow', '咨询编排、分阶段MCP状态、模拟更新、逐项复核和发布。', D+'workflow.py:Workflow'),
        n('service', 'service.DecisionService', 'prepare固定事实与依赖；finalize重读当前暂停并分流结果。', D+'service.py:DecisionService'),
        n('logic', 'logic.evaluate', '严格类型及true / false / unknown规则；不靠模型猜未知事实。', D+'logic.py:evaluate'),
        n('wired', 'connections.WiredRoles.invoke', '当前调用prepare登记路由，再进入OfflineRoles；这里不会自动发模型请求。', D+'connections.py:WiredRoles.invoke'),
        n('offline', 'adapters.OfflineRoles.invoke', '白名单检查后执行action()；remote_calls=0。', D+'adapters.py:OfflineRoles.invoke'),
        n('db', 'knowledge.store.Store', 'SQLite事务、KV记录、租约fence、版本CAS；Workflow复用这层。', K+'knowledge/store.py:Store', 'data'),
        n('preview', 'Preview / RegionalPreview', '8781/8782独立public预览；预算与来源绑定后才调用专用LiveVerifier。', K+'public_preview.py:Preview'),
        n('cloud', 'EnglishJevProvider.evaluate', 'DeepSeek英译→Jev；保留原文和回执；独立于工作台离线角色。', K+'jev_translation.py:EnglishJevProvider.evaluate'),
        n('registry', 'registry.KnowledgeStore', '公开资料原件、修订、复核、评测与scope发布；独立数据库。', K+'knowledge/registry.py:KnowledgeStore', 'data'),
        n('fetch', 'knowledge_fetch.worker_once', '自然调度的采集任务；robots、正文解析、去重及重试。', K+'knowledge_fetch.py:worker_once'),
        n('engine', 'knowledge.Engine + Worker', '8775维护引擎，另一套Agent候选/文档/规则发布流程。', K+'knowledge/engine.py:Engine')
    ], [('web','wf','api / MCP',''),('web','form','form_api',''),('form','wf','submit → query',''),('wf','service','_service → prepare / finalize',''),('service','logic','_condition → evaluate',''),('wf','wired','roles.invoke',''),('wired','offline','offline.invoke(action)',''),('wf','db','get / put / db(True)',''),('preview','cloud','LiveVerifier',''),('fetch','registry','ingest / propose_classification',''),('engine','db','独立runtime复用Store','dashed')], 'TB',
       [('8780 咨询工作台',['web','form','wf','service','logic','wired','offline']),('独立公开预览',['preview','cloud']),('独立公开修订库',['fetch','registry'])])

    sequence('02-http-query', '02 · 一次HTTP咨询的调用时序', '必要参数：facts、branch_id、question、可选request_id/parent_task。数据库锁不会贯穿整段检索。',
        ['浏览器', 'server.api', 'Workflow', 'DecisionService', 'SQLite'], [
        step(0,1,'POST /api/query {facts, branch_id, request_id}',D+'server.py:create_app.api'),
        step(1,1,'session + task归属 + request_id输入哈希检查',D+'server.py:create_app.api','gate'),
        step(1,2,'持会话query_lock；await to_thread(workflow.query)',D+'server.py:create_app.api'),
        step(2,4,'TX1: BEGIN IMMEDIATE；读取wf_head / wf_versions / block',D+'workflow.py:Workflow.query','transaction'),
        step(2,3,'service.prepare(facts, branch_id, as_of) → task_id + draft',D+'service.py:DecisionService.prepare'),
        step(2,4,'保存wf_records(kind=query, status=retrieving)及trace；COMMIT',D+'workflow.py:Workflow.query','transaction'),
        step(2,2,'最多3轮：_evidence_request → retrieve → _verify_retrieval',D+'workflow.py:Workflow._execute_query','loop'),
        step(2,4,'每轮短事务：保存rounds、found及两个角色trace',D+'workflow.py:Workflow._execute_query'),
        step(2,2,'缺证据条目降为unknown；不是重问模型补全',D+'workflow.py:Workflow._execute_query','gate'),
        step(2,4,'TX2: BEGIN；若传expected_head则对比wf_head；重读block',D+'workflow.py:Workflow._execute_query','transaction'),
        step(2,3,'service.finalize(task_id) → 确定 / 待核 / 排除 / 暂停',D+'service.py:DecisionService.finalize'),
        step(2,4,'保存complete + answer + documents_version + trace；COMMIT',D+'workflow.py:Workflow._execute_query','transaction'),
        step(2,1,'返回持久化answer；不调用正式政务提交',D+'workflow.py:Workflow.query','return'),
        step(1,0,'JSONResponse(answer)',D+'server.py:create_app.api','return'),
        step(2,4,'异常分支：当前事务ROLLBACK；另开事务写failed与审计，再抛异常',D+'workflow.py:Workflow.query','error'),
        step(1,0,'已知校验异常409／权限403；未知异常由ASGI处理',D+'server.py:create_app.Gate.__call__','error'),
        step(1,2,'同request_id同输入：history重读暂停；不同输入拒绝',D+'server.py:create_app.api','note')
    ])

    flow('03-mcp-state', '03 · 分阶段MCP查询状态机', '工具调用本身不推理。上游Nexent负责调度；程序以数据库状态决定是否允许下一步。', [
        n('start','govkb_open_query → open_query','固定快照与事实，创建staged任务；没有证据需求可直接can_finalize。',D+'workflow.py:Workflow.open_query'),
        n('retrieve','needs_retrieval','retrieve_query只在此状态且rounds<3取登记的所需证据。',D+'workflow.py:Workflow.retrieve_query'),
        n('verify','needs_verification','verify_query精确核对ID、span及quote；累计found_ids与rounds。',D+'workflow.py:Workflow.verify_query'),
        n('ready','can_finalize','无缺口，或已到第3轮；剩余缺证据项仍unknown。',D+'workflow.py:Workflow._verify_retrieval','gate'),
        n('complete','complete','finalize_query每次重读live gate；首次持久化answer，重试不重复创建任务。',D+'workflow.py:Workflow.finalize_query'),
        n('deny','拒绝不合法调用','STAGED_QUERY_REQUIRED / RETRIEVAL_NOT_ALLOWED / VERIFICATION_NOT_ALLOWED / QUERY_NOT_VALIDATED。',D+'workflow.py:Workflow.finalize_query','error'),
        n('state','govkb_query_state','只读持久化status、轮数和version；主控不用子Agent自然语言冒充已执行。',D+'server.py:create_app.govkb_query_state','data')
    ], [('start','retrieve','需要证据',''),('start','ready','无证据需求',''),('retrieve','verify','retrieve_query',''),('verify','retrieve','有缺口且round<3',''),('verify','ready','齐备或第3轮',''),('ready','complete','finalize_query + 当前暂停检查',''),('verify','verify','重复retrieve：回同一retrieval','dashed'),('complete','complete','重复finalize：再检查当前暂停','dashed'),('verify','state','只读状态，不产生迁移','dashed'),('start','deny','非法任务/状态调用均拒绝','dashed')])

    flow('04-update-publish','04 · 变更发现、暂停、复核与原子发布','此图对应8780模拟更新实现。关键点是先暂停受影响项；文档可检索不等于业务规则已发布。',[
        n('tick','heartbeat(force=False)','30秒ticker；paused且非force则返回；单写租约120秒、轮预算600秒。',D+'workflow.py:Workflow.heartbeat'),
        n('claim','Store.claim','租约有效→already_running；接管时fence递增，旧running标interrupted。',K+'knowledge/store.py:Store.claim','gate'),
        n('check','_check(source, run, interval)','比较来源＋内容＋配置指纹；unchanged只更新检查时间；失败记来源错误。',D+'workflow.py:Workflow._check'),
        n('block','立即落库package + block','新包processing/discovered；旧未发布包superseded；受影响requirement即刻暂停。',D+'workflow.py:Workflow._check','data'),
        n('process','_process的持久化阶段','discovered→classified→extracted→validated→awaiting_review；租约失效拒绝写入。',D+'workflow.py:Workflow._process'),
        n('docs','写documents与本地稀疏索引','wf_documents推进；候选可供查看，但不切换wf_head业务知识。',D+'workflow.py:Workflow._process','data'),
        n('review','review(package_id, object_ids)','专业角色选择accepted/rejected，记录actor＋basis_hash；拒绝项继续暂停。',D+'workflow.py:Workflow.review'),
        n('tx','publish：BEGIN IMMEDIATE','expected_head必须等于当前头；包仍awaiting_review，source_guard仍有效。',D+'workflow.py:Workflow.publish','gate'),
        n('basis','复核与依赖是否仍一致？','同actor已accepted、basis_hash匹配；候选依赖相对base未变。',D+'workflow.py:Workflow.publish','gate'),
        n('snapshot','创建并校验新快照','合并所选要求/别名；本体提案executable=false；完整图、review及span验证。',D+'workflow.py:Workflow._version'),
        n('switch','原子切换wf_head','写wf_versions、ontology_version、accepted_hash、package状态及审计；COMMIT。',D+'workflow.py:Workflow.publish','data'),
        n('rollback','任一验证失败：ROLLBACK','不留下半发布；expected_head错误返回BASE_VERSION_CHANGED。',K+'knowledge/store.py:Store.db','error'),
        n('history','显式rollback(target, expected_head)','仅切换历史头；旧block不自动清除，返回前继续检查当前暂停。',D+'workflow.py:Workflow.rollback')
    ], [('tick','claim','允许工作',''),('claim','check','获租约；每轮最多3包/来源',''),('check','block','指纹改变',''),('block','process','已持久化',''),('process','docs','完成核验',''),('docs','review','awaiting_review',''),('review','tx','人工发起发布',''),('tx','basis','版本与来源通过',''),('basis','snapshot','通过',''),('snapshot','switch','校验成功',''),('basis','rollback','不一致',''),('tx','rollback','版本/来源失败',''),('snapshot','rollback','结构或引用失败',''),('switch','history','需要历史回退时','dashed')])

    flow('05-workflow-data','05 · 决策工作台的数据结构','实线是代码读写关系；虚线是JSON里的逻辑引用，不是数据库声明的外键。',[
        n('workflow','Workflow读写层','所有业务记录按kind＋id存在wf_records；短事务与版本指纹由程序控制。',D+'workflow.py:Workflow.put'),
        n('state','state(key PK, value JSON)','wf_head=业务快照ID；wf_documents=当前文档；wf_control=暂停/间隔；lease=单写租约。',K+'knowledge/store.py:Store.__init__','data'),
        n('records','wf_records(kind, id, payload)','PRIMARY KEY(kind,id)。query / source / package / block / trace / documents / ontology_version / audit_gate。',D+'workflow.py:Workflow.__init__','data'),
        n('versions','wf_versions(id PK, payload)','id=规范化snapshot的SHA256；payload包含base、actor、created_at、snapshot。',D+'workflow.py:Workflow._version','data'),
        n('query','query payload','version_id、facts、branch_id、draft、rounds、found_ids、status、answer；staged查询分步骤推进。',D+'workflow.py:Workflow.open_query'),
        n('package','package payload','source_id、fingerprint、base、stage、candidates、reviews、published；basis_hash绑定逐项复核。',D+'workflow.py:Workflow._check'),
        n('blocks','block payload','object_id、package_id、reason、accepted_hash；仅已接受新内容的哈希解除对应暂停。',D+'workflow.py:Workflow._service'),
        n('runs','runs / jobs','Store的租约run使用owner＋fence＋deadline；工作台复用run，公开采集器另有自己的jobs表。',K+'knowledge/store.py:Store.claim','data'),
        n('audit','audit(id, time, operation, payload)','记录人工复核、发布、回退、失败；wf_records的trace另记录角色/工具及输入输出哈希。',D+'workflow.py:Workflow._event','data')
    ], [('workflow','state','get / put',''),('workflow','records','get / put / rows',''),('workflow','versions','_version / version',''),('workflow','audit','audit / _event',''),('workflow','runs','claim / finish / abort',''),('records','query','kind=query','dashed'),('records','package','kind=package','dashed'),('records','blocks','kind=block','dashed'),('query','versions','version_id','dashed'),('package','versions','base','dashed'),('state','versions','wf_head','dashed'),('blocks','package','package_id','dashed')], 'LR')

    # Real schema and FK declarations are extracted from the constructor, without opening a DB.
    schema=reference(K+'knowledge/registry.py:KnowledgeStore.__init__')['source']
    tables={}
    for match in re.finditer(r'CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\);',schema,re.S):
        tables[match[1]]=' '.join(match[2].split())
    selected=['sites','documents','revisions','chunks','reviews','exclusions','versions','heads','snapshot_evaluations','jobs']
    dbnodes=[]; dbedges=[]
    for name in selected:
        ddl=tables[name]
        dbnodes.append(n(name,name,'\n'.join(re.split(r',\s*(?![^()]*\))',ddl)),K+'knowledge/registry.py:KnowledgeStore.__init__','data'))
        for target in re.findall(r'REFERENCES (\w+)',ddl):
            if target in selected: dbedges.append((target,name,'FK',''))
    flow('06-registry-data','06 · 公开知识库：真实表关系','仅画声明在DDL中的外键；versions.members/scope/parent等由程序校验。此库与图05使用不同runtime。',dbnodes,dbedges,'LR')

    sequence('07-cloud-preview','07 · 原生预览的模型调用与事务边界','以Preview.verify为例：先预留预算，释放SQLite事务，再调用网络；失败不会退回已预留次数。',
        ['Nexent核验子Agent','Preview.verify','SQLite / 回执','DeepSeek英译','Jev官方'],[
        step(0,1,'MCP verify(task_id)，Bearer权限由服务入口检查',K+'public_preview.py:Preview.verify'),
        step(1,2,'TX1 BEGIN：重查来源绑定、facts、活动run与预算',K+'public_preview.py:Preview.verify','transaction'),
        step(1,1,'已有verification → 同task直接返回；不重复调用模型',K+'public_preview.py:Preview.verify','gate'),
        step(1,2,'jev_reserved += 1；task.status=verifying；COMMIT',K+'public_preview.py:Preview.verify','transaction'),
        step(1,1,'构建public原文陈述＋专属负例；调用LiveVerifier',K+'public_preview.py:LiveVerifier.__call__'),
        step(1,3,'EnglishJevProvider：只英译语义槽，ID/引用保持',K+'jev_translation.py:EnglishJevProvider.evaluate'),
        step(3,1,'返回英文；核对数字、字段覆盖、残留中文及字节限额',K+'jev_translation.py:EnglishJevProvider.evaluate','return'),
        step(1,4,'evaluate(state_en, questions_en, jev-1.13.0)',K+'public_preview.py:LiveVerifier.__call__'),
        step(4,1,'返回逐项支持概率与usage',K+'public_preview.py:LiveVerifier.__call__','return'),
        step(1,2,'LiveVerifier保存实际请求/英文/供应商回执',K+'public_preview.py:LiveVerifier.__call__'),
        step(1,2,'TX2：重验context；保存概率、threshold=0.8、回执SHA；can_finalize',K+'public_preview.py:Preview.verify','transaction'),
        step(0,1,'后续finalize：重验context、回执SHA与已存分数',K+'public_preview.py:Preview.finalize'),
        step(1,0,'来源支持逐项返回；业务待核仍paused；published=false',K+'public_preview.py:Preview.finalize','return'),
        step(1,2,'失败分支：task=failed；鉴权/余额/访问/传输等写external_block',K+'public_preview.py:Preview.verify','error'),
        step(1,0,'受控错误，整轮停止；不清预算、不自动换供应商',K+'public_preview.py:Preview.verify','error')
    ])

    sequence('08-guided-form','08 · 填表核查、编辑失效与咨询提交','表单原回答留本机会话；外发只含选项代码。DeepSeek仅核对格式，无工具和提交权限。',
        ['intake.js','server.form_api','GuidedForm','DeepSeekFormVerifier','Workflow'],[
        step(0,1,'POST /api/forms/start {}',D+'server.py:create_app.form_api'),
        step(1,2,'GuidedForm(workflow)：固定字段与版本；有效期30分钟',D+'intake.py:GuidedForm.__init__'),
        step(0,1,'POST answer：form_id、revision、field_id、answer',D+'server.py:create_app.form_api'),
        step(1,2,'会话归属＋revision校验；answer按顺序唯一匹配选项',D+'intake.py:GuidedForm.answer'),
        step(2,2,'编辑→revision++；reviewed=None；原确认立即失效',D+'intake.py:GuidedForm.answer','gate'),
        step(0,1,'POST review：consent=true',D+'server.py:create_app.form_api'),
        step(1,1,'完整表单＋开关＋预算：全局20/会话3；并发槽2',D+'server.py:create_app.form_api','gate'),
        step(1,2,'to_thread(form.review)：校验字段schema与当前快照',D+'intake.py:GuidedForm.review'),
        step(2,3,'同草稿/版本/规范值缓存可复用；否则verify(template, values)',D+'intake.py:GuidedForm.review'),
        step(3,2,'返回JSON；value类型和值、display、unknown_fields均必须精确一致',D+'intake.py:GuidedForm.review','return'),
        step(2,1,'human_review：review_id + revision + 允许分支',D+'intake.py:GuidedForm.review','return'),
        step(1,0,'显示核查结果；仍需人工逐项确认',D+'server.py:create_app.form_api','return'),
        step(0,1,'POST submit：acknowledge=true、review_id、revision、branch_id',D+'server.py:create_app.form_api'),
        step(1,2,'submit：复核ID/版本/分支有效；重复同分支复用回执',D+'intake.py:GuidedForm.submit','gate'),
        step(2,4,'query(facts, branch_id, version_id, expected_head)',D+'intake.py:GuidedForm.submit'),
        step(2,1,'本地咨询回执；清原回答和缓存；official_submission=false',D+'intake.py:GuidedForm.submit','return'),
        step(1,0,'返回receipt＋当前可用性答复',D+'server.py:create_app.form_api','return'),
        step(1,0,'失败分支：过期/改值/换版本/无确认/预算耗尽→拒绝；不自动补事实',D+'server.py:create_app.form_api','error')
    ])


def q(text):
    return json.dumps(text,ensure_ascii=False)


def lines(text, limit=52):
    out=[]; part=''; width=0
    for c in text:
        if c=='\n':
            out.append(part); part=''; width=0
            continue
        w=2 if ord(c)>255 else 1
        if width+w>limit: out.append(part); part=''; width=0
        part+=c; width+=w
    if part: out.append(part)
    return out


def dot(g):
    out=['digraph G {', f'graph [rankdir={g["direction"]}, bgcolor="white", pad="0.35", nodesep="0.5", ranksep="0.6", splines=polyline, fontname="Microsoft YaHei"];',
         f'node [shape=box, style="rounded,filled", width={5.8 if g["id"]=="06-registry-data" else 4.5}, fontname="Microsoft YaHei", fontsize=12, margin="0.18,0.16"];',
         'edge [fontname="Microsoft YaHei", fontsize=10, color="#708299", arrowsize=0.75];']
    for item in g['nodes']:
        source=reference(item['ref']); fill,stroke=COLORS[item['kind']]
        short=item['detail'] if g['id']=='06-registry-data' else item['detail']
        # All essential information is visible on the diagram; full code is in node details.
        body='<BR ALIGN="LEFT"/>'.join(html.escape(s) for s in lines(short,64 if g['id']=='06-registry-data' else 48))
        label=f'<B>{html.escape(item["name"])}</B><BR/><FONT POINT-SIZE="10">{body}</FONT><BR/><FONT POINT-SIZE="9" COLOR="#65758b">{Path(source["file"]).name}:{source["line"]}</FONT>'
        out.append(f'{item["id"]} [id={q(g["id"]+"-"+item["id"])}, label=<{label}>, fillcolor={q(fill)}, color={q(stroke)}, tooltip={q(item["ref"])}];')
    for a,b,label,style in g['edges']:
        out.append(f'{a} -> {b} [label={q(label)}, style={q(style or "solid")}];')
    for i,(title,items) in enumerate(g['groups']):
        out.append(f'subgraph cluster{i} {{label={q(title)}; color="#bfcddd"; style="rounded,dashed"; '+ ';'.join(items)+';}')
    return '\n'.join(out+['}'])


def sequence_svg(g):
    actors=g['actors']; width=1280; left=120; dx=(width-2*left)/(len(actors)-1)
    y=115; rendered=[]; positions=[left+i*dx for i in range(len(actors))]
    for i, event in enumerate(g['steps']):
        source=reference(event['ref']); a,b=event['from'],event['to']
        label_lines=lines(str(i+1)+'. '+event['label'],96)
        h=max(80,32+len(label_lines)*19)
        color='#b75a50' if event['kind']=='error' else '#9b7221' if event['kind'] in {'gate','transaction','loop'} else '#476f99'
        rendered.append(f'<g id="{g["id"]}-s{i}" class="node" tabindex="0"><title>{html.escape(event["ref"])}</title>')
        if event['kind'] in {'transaction','error','loop','note'}:
            fill='#fff1ef' if event['kind']=='error' else '#fff8e9'
            rendered.append(f'<rect x="20" y="{y-17}" width="1240" height="{h-10}" rx="6" fill="{fill}" opacity=".8"/>')
        x1,x2=positions[a],positions[b]
        start=min(x1,x2)+10 if a!=b else x1+10
        if a==b: start=max(40,min(start,width-620))
        for j,label in enumerate(label_lines):
            rendered.append(f'<text x="{start}" y="{y+j*19}" font-size="13" fill="{color}">{html.escape(label)}</text>')
        ey=y+len(label_lines)*19+7
        dash='stroke-dasharray="6 4"' if event['kind']=='return' else ''
        path=f'M {x1} {ey} L {x2} {ey}' if a!=b else f'M {x1} {ey-7} L {x1+40} {ey-7} L {x1+40} {ey+9} L {x1} {ey+9}'
        rendered.append(f'<path d="{path}" fill="none" stroke="{color}" {dash} stroke-width="1.6" marker-end="url(#seqArrow)"/></g>')
        y+=h
    height=y+50
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
         '<defs><marker id="seqArrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="#6a7e94"/></marker></defs>',
         '<style>text{font-family:Microsoft YaHei,Segoe UI,sans-serif}.node{cursor:pointer}.node:hover{opacity:.8}</style>',
         f'<rect width="{width}" height="{height}" fill="white"/>']
    for actor,x in zip(actors,positions):
        out.append(f'<path d="M {x} 75 L {x} {height-20}" stroke="#bdc9d7" stroke-dasharray="6 5"/><rect x="{x-110}" y="20" width="220" height="54" rx="7" fill="#eaf1fb" stroke="#6790ba"/><text x="{x}" y="52" text-anchor="middle" font-size="14" font-weight="600" fill="#244768">{html.escape(actor)}</text>')
    return ''.join(out+rendered)+ '</svg>'


def main():
    OUT.mkdir(exist_ok=True); (OUT/'diagrams').mkdir(exist_ok=True)
    define()
    jobs=[]
    for g in GRAPHS:
        if g['kind']=='flow':
            (OUT/'diagrams'/f'{g["id"]}.dot').write_text(dot(g),encoding='utf-8')
            jobs.append(g['id'])
        else:
            (OUT/'diagrams'/f'{g["id"]}.svg').write_text(sequence_svg(g),encoding='utf-8')
    renderer="""const fs=require('node:fs'),p=require('node:path'); const {instance}=require(process.argv[1]); (async()=>{const viz=await instance();const root=process.argv[2];for(const name of JSON.parse(process.argv[3])){const dot=fs.readFileSync(p.join(root,name+'.dot'),'utf8');fs.writeFileSync(p.join(root,name+'.svg'),viz.renderString(dot,{format:'svg'}));}console.log('Graphviz diagrams rendered:',JSON.parse(process.argv[3]).length)})().catch(e=>{console.error(e);process.exit(1)});"""
    subprocess.run(['node','-e',renderer,str(RUNTIME/'@viz-js/viz'),str(OUT/'diagrams'),json.dumps(jobs)],check=True)
    for g in GRAPHS:
        svg=(OUT/'diagrams'/f'{g["id"]}.svg').read_text('utf-8')
        ET.fromstring(svg)
        g['svg']=re.sub(r'<\?xml.*?\?>|<!DOCTYPE[\s\S]*?\]>|<!DOCTYPE[^>]*>','',svg)
        if g['kind']=='flow':
            for item in g['nodes']: item['dom_id']=g['id']+'-'+item['id']
        else:
            g['nodes']=[{'dom_id':g['id']+'-s'+str(i),'id':'s'+str(i),'name':str(i+1)+'. '+s['label'],'detail':s['label'],'ref':s['ref'],'kind':'code'} for i,s in enumerate(g['steps'])]
    for source in SOURCES.values():
        assert hashlib.sha256((ROOT/source['file']).read_bytes()).hexdigest()==source['sha256'], 'Source changed while generating'
    data={'generated_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),'graphs':GRAPHS,'sources':SOURCES}
    template=(ROOT/'scripts/developer_flow_template.html').read_text('utf-8')
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e')
    (OUT/'index.html').write_text(template.replace('__DATA__',payload),encoding='utf-8')
    (OUT/'源码与图数据.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    code=re.findall(r'<script>([\s\S]*?)</script>',template)[0]
    (OUT/'页面脚本校验.js').write_text(code,encoding='utf-8')
    subprocess.run(['node','--check',str(OUT/'页面脚本校验.js')],check=True)
    md=['# 开发者流程图：核心调用与数据边界','',f'生成时间：{data["generated_at"]}。按当前工作区源码生成；没有执行业务、模型或采集。','',
        '[打开离线交互页面](index.html)。点节点查看对应函数源码；图上保留方法、关键参数、事务、状态、表名及失败出口。','',
        '阅读顺序：模块关系→HTTP调用→MCP状态→更新发布→两套数据库→模型预览→表单。蓝色=函数，黄色=校验/事务，绿色=存储，红色=异常；虚线逻辑引用不是DDL外键。','']
    for g in GRAPHS:
        md += ['## '+g['title'],'',g['intent'],'',f'![{g["title"]}](diagrams/{g["id"]}.svg)','', '|代码位置|必要说明|','|---|---|']
        for item in g['nodes']:
            s=SOURCES[item['ref']]
            md.append(f'|[{item["ref"]}:{s["line"]}](../../{s["file"]}#L{s["line"]})|'+item['detail'].replace('|','／')+'|')
        md.append('')
    md += ['## 分析边界','', '以上是静态开发者说明；时序图的异常与幂等分支用于说明可能路径，不表示在一次请求里全部执行。没有据此宣布动态路径、真实模型或业务已经验收。数据库结构只从源码DDL提取，没有打开私有数据库。', '',
           '生成：`.venv/Scripts/python.exe scripts/build_developer_flow.py`。图布局复用本机已提供的@viz-js/viz，无联网安装；路径由脚本RUNTIME常量指定。', '',
           '原来的[业务总览与逐函数索引](../项目流程图详解-2026-10-18/index.html)保留作补充。','']
    (OUT/'开发者流程说明.md').write_text('\n'.join(md),encoding='utf-8')
    links=re.findall(r'\]\(([^)]+)\)', '\n'.join(md))
    missing=[p for p in links if not (OUT/p.split('#')[0]).resolve().exists()]
    assert not missing,missing
    report={'diagrams':len(GRAPHS),'annotated_nodes_and_messages':sum(len(g['nodes']) for g in GRAPHS),'referenced_functions_and_classes':len(SOURCES),
            'source_files':len({s['file'] for s in SOURCES.values()}),'source_references_and_fingerprints':'passed','svg_xml':'passed',
            'local_links':len(links),'javascript_syntax':'passed','business_tests_run':False,'model_calls':0,'browser_interaction_tested':False}
    (OUT/'生成校验.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
