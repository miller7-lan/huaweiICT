"""Read-only static source atlas; never imports or executes project modules."""
from __future__ import annotations

import ast
import csv
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / '项目流程图详解-2026-10-18'
SKIP = {'.git', '.venv', '.runtime', '.pytest_cache', '__pycache__', 'node_modules', 'models', 'vendor', 'references', 'build', 'dist'}
FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)
CONTROL = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.TryStar, ast.With, ast.AsyncWith, ast.Match)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def rel(path):
    return path.relative_to(ROOT).as_posix()


def snippet(node):
    try:
        return ast.unparse(node)
    except (ValueError, RecursionError):
        return type(node).__name__


def scan():
    files, ignored = [], []
    def walk(directory):
        for path in sorted(directory.iterdir()):
            if path.is_symlink():
                ignored.append({'path': rel(path), 'reason': 'symbolic_link_not_followed'})
                continue
            if path.is_dir():
                if path.name in SKIP or path == OUT:
                    ignored.append({'path': rel(path), 'reason': 'private_runtime_dependency_reference_or_generated'})
                else:
                    walk(path)
            else:
                name = path.name.lower()
                if name.startswith('.env') or re.search(r'(secret|credential|token|password).*(\.(env|pem|key|txt))$', name):
                    ignored.append({'path': rel(path), 'reason': 'potential_secret_content_not_read'})
                    continue
                files.append({'path': rel(path), 'bytes': path.stat().st_size, 'extension': path.suffix.lower()})
    walk(ROOT)
    modules, symbols, issues, tests = [], [], [], []
    for item in files:
        p = item['path']
        if not p.endswith('.py') or not p.startswith(('src/', 'scripts/', 'tests/')):
            continue
        path = ROOT / p
        source = path.read_text(encoding='utf-8-sig')
        try:
            tree = ast.parse(source, filename=p)
        except SyntaxError as error:
            issues.append({'path': p, 'line': error.lineno, 'error': error.msg})
            continue
        item['sha256'] = sha(path.read_bytes())
        if p.startswith('tests/'):
            tests.extend({'file': p, 'name': n.name, 'line': n.lineno} for n in ast.walk(tree) if isinstance(n, FUNC) and n.name.startswith('test_'))
            continue
        module = {'path': p, 'lines': len(source.splitlines()), 'sha256': item['sha256'], 'imports': [], 'routes': [], 'tools': [], 'tables': []}
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                module['imports'].extend(a.name for a in n.names)
            if isinstance(n, ast.ImportFrom):
                module['imports'].append('.' * n.level + (n.module or ''))
            if isinstance(n, ast.Call):
                target = snippet(n.func)
                if target.rsplit('.', 1)[-1] in {'Route', 'Mount'}:
                    module['routes'].append({'line': n.lineno, 'code': snippet(n)})
            if isinstance(n, FUNC) and any('.tool(' in snippet(d) for d in n.decorator_list):
                module['tools'].append({'name': n.name, 'line': n.lineno, 'signature': snippet(n.args)})
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                module['tables'].extend(re.findall(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)', n.value, re.I))
        module['imports'] = sorted(set(module['imports']))
        module['tables'] = sorted(set(module['tables']))
        modules.append(module)
        lines = source.splitlines()
        def register(node, name, owner, body, begin, finish, kind):
            events, calls, errors = [], [], []
            def visit(n):
                if isinstance(n, (ast.ClassDef, *FUNC)) and n is not node:
                    return
                typ = type(n).__name__
                expr = None
                if isinstance(n, ast.If): expr = snippet(n.test)
                elif isinstance(n, (ast.While, ast.Assert)): expr = snippet(n.test)
                elif isinstance(n, (ast.For, ast.AsyncFor)): expr = snippet(n.target) + ' in ' + snippet(n.iter)
                elif isinstance(n, ast.ExceptHandler): expr = snippet(n.type) if n.type else 'BaseException'
                elif isinstance(n, ast.IfExp): expr = snippet(n)
                elif isinstance(n, ast.BoolOp): expr = snippet(n)
                elif isinstance(n, ast.comprehension): expr = snippet(n.target) + ' in ' + snippet(n.iter) + ''.join(' if ' + snippet(c) for c in n.ifs)
                elif isinstance(n, (ast.Return, ast.Raise, ast.Break, ast.Continue, ast.Yield, ast.YieldFrom)): expr = snippet(n)
                elif isinstance(n, (ast.Try, ast.TryStar)): expr = 'try / except / else / finally'
                elif isinstance(n, (ast.With, ast.AsyncWith)): expr = ', '.join(snippet(i.context_expr) for i in n.items)
                elif isinstance(n, ast.Match): expr = snippet(n.subject)
                if expr is not None:
                    events.append({'type': typ, 'line': getattr(n, 'lineno', begin), 'code': expr})
                if isinstance(n, ast.Call): calls.append({'target': snippet(n.func), 'line': n.lineno})
                if isinstance(n, ast.Raise): errors.append({'line': n.lineno, 'code': snippet(n.exc) if n.exc else 're-raise'})
                for child in ast.iter_child_nodes(n): visit(child)
            for child in body: visit(child)
            symbols.append({'id': p + ':' + name, 'file': p, 'name': name, 'owner': owner, 'kind': kind,
                'line': begin, 'end': finish, 'signature': snippet(node.args) if isinstance(node, FUNC) else '',
                'doc': ast.get_docstring(node) if isinstance(node, (ast.Module, ast.ClassDef, *FUNC)) else None,
                'source': '\n'.join(lines[begin-1:finish]), 'events': sorted(events, key=lambda e:e['line']),
                'calls': calls, 'errors': errors})
        register(tree, '$module', '', tree.body, 1, len(lines), 'module')
        def definitions(parent, prefix=''):
            for n in ast.iter_child_nodes(parent):
                if isinstance(n, (ast.ClassDef, *FUNC)):
                    name = prefix + n.name
                    register(n, name, prefix.rstrip('.'), n.body, n.lineno, n.end_lineno, 'class' if isinstance(n, ast.ClassDef) else 'function')
                    definitions(n, name + '.')
                else:
                    definitions(n, prefix)
        definitions(tree)
    text_assets = []
    for item in files:
        p = item['path']
        path = ROOT / p
        if p in {'scripts/flow_atlas_template.html'}:
            continue
        if (p.startswith(('src/', 'scripts/', 'deploy/')) and path.suffix in {'.js', '.html', '.css', '.ps1', '.sh'}) or (path.parent == ROOT and (path.suffix == '.yml' or path.name.startswith('Dockerfile'))):
            source = path.read_text(encoding='utf-8-sig')
            markers = []
            patterns = {
                '前端事件／请求': r'addEventListener\(|\.on(?:click|change|input|submit)\s*=|\bfetch\(|XMLHttpRequest',
                '条件／循环／异常（文本定位）': r'\b(?:if|else|for|while|try|catch|finally|throw)\b|\b(?:If|Else|ForEach|While|Try|Catch|Throw)\b',
                '函数／命令入口': r'\bfunction\b|^\s*(?:param\(|[a-zA-Z_]\w*\s*\(\)\s*\{)|Start-Process|subprocess|python(?:\.exe)?',
                '部署配置': r'^\s*(?:FROM |RUN |CMD |ENTRYPOINT |COPY |services:|ports:|volumes:|networks:|restart:|environment:|healthcheck:)',
            }
            for no, line in enumerate(source.splitlines(), 1):
                for label, pattern in patterns.items():
                    if re.search(pattern, line): markers.append({'type': label, 'line': no, 'code': line.strip()})
            text_assets.append({'file': p, 'source': source, 'lines': len(source.splitlines()), 'sha256': sha(path.read_bytes()), 'markers': markers})
            item['sha256'] = sha(path.read_bytes())
    return {'files': files, 'ignored': ignored, 'modules': modules, 'symbols': symbols, 'text_assets': text_assets, 'parse_errors': issues, 'tests': tests}


# Diagrams are curated from the actual entry points. References are verified below.
DIAGRAMS = []
def diagram(key, title, subtitle, rows, edges):
    nodes = []
    for row, values in enumerate(rows):
        for col, value in enumerate(values):
            if value is None: continue
            ident, label, note, ref, kind = value
            nodes.append({'id': ident, 'label': label, 'note': note, 'ref': ref, 'kind': kind, 'row': row, 'col': col})
    DIAGRAMS.append({'id': key, 'title': title, 'subtitle': subtitle, 'nodes': nodes, 'edges': edges})


def node(ident, label, note, ref, kind='process'):
    return ident, label, note, ref, kind


def define_diagrams():
    d='src/gov_mcp/decision/'
    k='src/gov_mcp/'
    diagram('01-overview','01 · 项目全景与实现边界','箭头表示入口或数据关系；各链路使用独立存储，并非自动串接。',[
        [node('user','用户／专业人员','群众咨询；专业人员复核；网页身份是本地工程身份。',d+'server.py:create_app.login'),node('native','Nexent 主控与子 Agent','配置端5010、运行端5014；Skill约束工具顺序；是否真实调用须看对应执行证据。',d+'connections.py:Connections.request_nexent'),node('files','资料、配置与历史证据','config=契约，data=共享目录，evidence=分阶段证据；目录条目不等于私有原件已部署。','README.md')],
        [node('decision','8780 决策工作台','默认模拟资料；问答填表、三值条件、检索核验、逐项复核和版本。',d+'workflow.py:Workflow'),node('public','8781／8782 原生公开预览','独立public入口；固定地区情境、五工具、英译和Jev；原文支持不自动成为现行要求。',k+'public_preview.py:Preview'),node('registry','公开资料修订库','registry.KnowledgeStore＋schedule/worker_once；候选归档、原件指纹、复核、评测和范围发布。',k+'knowledge/registry.py:KnowledgeStore')],
        [node('legacy','M1／M2／资产查询','早期环境工具、模拟材料交接与多模态资料检索，保留各自工程边界。',k+'m2.py:M2Tools'),node('engine','8775 维护引擎／8778回放','Engine＋Worker＋NativeDriver；文件索引和业务规则分别发布，与公开修订库不同。',k+'knowledge/engine.py:Engine'),node('safety','贯穿全程的返回门','角色、数据标签、来源授权、原文定位、版本、当前暂停、人工确认、预算。',k+'privacy.py:data_classes_allowed','gate')]
    ], [('user','decision','网页入口'),('native','public','专用MCP'),('files','registry','来源配置'),('decision','legacy','并列历史入口'),('native','engine','原生维护编排'),('registry','safety','查询／发布校验'),('public','safety','返回前重验'),('decision','safety','返回前重验')])
    diagram('02-auth','02 · 请求、角色与隐私边界','以8780工作台为准；公开预览及其他服务使用各自Bearer和角色配置。',[
        [node('req','HTTP 请求','TrustedHost限制localhost、127.0.0.1及host.docker.internal。',d+'server.py:create_app')],
        [node('kind','区分 MCP 与网页','MCP校验服务Bearer；网页POST要求同源Origin。',d+'server.py:create_app.Gate.__call__','gate'),node('bad','拒绝请求','权限失败403；参数／版本／存储校验失败409；超限413；不向页面透传密钥或原始异常。',d+'server.py:create_app.Gate.__call__','stop')],
        [node('size','接收前限制真实字节','MCP正文≤1MiB，网页≤65536字节；校验Content-Length并逐块累计。',d+'server.py:create_app.Gate.__call__','gate')],
        [node('session','校验会话及专业角色','会话4小时；群众最多1024，专业人员64；专业登录需本地令牌。',d+'server.py:create_app.session','gate')],
        [node('own','任务归属／幂等请求','群众只读本人任务；request_id同输入才复用；复用仍重读暂停状态；每会话最多200任务。',d+'server.py:create_app.api','gate')],
        [node('class','递归检查原始数据标签','在投影或翻译前检查嵌套dict/list/tuple的data_class；不允许借嵌套标签绕过限制。',k+'privacy.py:data_classes_allowed','gate')],
        [node('call','调用获准程序工具','有推理开关和角色能力边界；来源内容只作为数据；日志与响应受控。',d+'connections.py:Connections.request')]
    ], [('req','kind','进入'),('kind','bad','鉴权不通过'),('kind','size','通过'),('size','bad','超限／非法长度'),('size','session','通过'),('session','bad','过期／角色不符'),('session','own','通过'),('own','class','需要模型外发时'),('class','bad','不允许的数据类别'),('class','call','通过')])
    diagram('03-form','03 · 一问一答填表与人工确认','当前config中form_verifier_enabled=false；完整链路存在，默认不能越过AI核查门。',[
        [node('start','创建会话专属表单','模板只允许synthetic；字段及选项须与固定快照一致；表单30分钟有效。',d+'intake.py:GuidedForm.__init__')],
        [node('answer','按顺序回答／编辑','回答1–120字符、禁止控制字符；NFKC＋去空白＋casefold；必须唯一匹配选项；原答仅本机会话。',d+'intake.py:GuidedForm.answer')],
        [node('rev','revision递增，旧确认失效','每次编辑清空reviewed；下一请求须持当前revision；未知选项保留null。',d+'intake.py:GuidedForm.expected','gate'),node('stop','返回澄清／阻断','次序错误、无法唯一匹配、过期、版本或字段变化均停止当前操作。',d+'intake.py:GuidedForm.answer','stop')],
        [node('consent','完整性、同意与AI预算','review必须consent=true；总预算20次、每会话3次；独立开关开启后才核查。',d+'server.py:create_app.form_api','gate')],
        [node('verify','只读 DeepSeek 格式核查','只发送当前选项代码与选中映射；无工具、无提交权；只允许official及synthetic。',d+'intake.py:DeepSeekFormVerifier.payload')],
        [node('check','逐字段核对模型输出','字段覆盖、value类型和值、display均精确一致；unknown_fields必须完全等于null字段；再核对版本。',d+'intake.py:GuidedForm.review','gate')],
        [node('human','人工逐项检查并确认','review_id＋revision＋允许叶分支＋acknowledge=true；缓存只限本表单同版本同值，命中也须人工确认。',d+'intake.py:GuidedForm.submit','gate')],
        [node('submit','进入现有本地咨询','用expected_head固定版本；返回咨询回执；清空原回答及核查缓存；不是正式政务提交。',d+'intake.py:GuidedForm.submit')]
    ], [('start','answer','开始'),('answer','rev','保存规范值'),('answer','stop','无效'),('rev','answer','继续回答／编辑'),('rev','consent','全部完成'),('consent','stop','未启用／无同意／预算耗尽'),('consent','verify','允许'),('verify','check','返回JSON'),('check','stop','修改事实／结构不符'),('check','human','通过'),('human','submit','确认且版本一致')])
    diagram('04-query','04 · 当前完整咨询链路','Workflow.query执行确定性离线角色；配置DeepSeek路由不意味着此函数自动进行模型推理。',[
        [node('q','接收 facts、branch_id、question','question≤2000字符，只作数据；补充任务须存在；可校验expected_head。',d+'workflow.py:Workflow.query')],
        [node('pin','锁定版本与准备草稿','校验快照指纹；重建当前暂停；固定用户事实、分支、地区、日期和原文依赖。',d+'service.py:DecisionService.prepare')],
        [node('need','计算未找到的证据ID','从全部草稿要求收集所需span；没有要求／证据则跳过检索。',d+'workflow.py:Workflow._evidence_request','gate')],
        [node('ret','检索角色取登记证据','仅使用固定快照，不由Agent指定任意URL；每轮存任务与角色轨迹。',d+'workflow.py:Workflow._execute_query')],
        [node('ver','独立核验精确原文','所需ID与请求ID取交集；span必须等于登记内容，text[start:end]必须等于quote。',d+'workflow.py:Workflow._verify_retrieval','gate'),node('loop','仍有缺口且轮次小于3','仅针对真实缺口补检索；不重复完整材料；最多3轮。',d+'workflow.py:Workflow._verify_retrieval')],
        [node('unk','未取到证据的项降为unknown','EVIDENCE_RETRIEVAL_INCOMPLETE；用户未知字段不自动补全。',d+'workflow.py:Workflow._execute_query')],
        [node('gate','单写事务内返回前重验','重新读取版本、当前暂停，执行finalize，再持久化答复；异常将任务标failed。',d+'workflow.py:Workflow._execute_query','gate')],
        [node('out','分环节清单＋待核＋排除＋暂停','携带引用、版本、缺失字段、候选证据和轨迹；候选不进入确定清单。',d+'service.py:DecisionService.finalize')]
    ], [('q','pin','合法输入'),('pin','need','草稿'),('need','ret','有缺口'),('need','gate','无所需证据'),('ret','ver','候选span'),('ver','loop','尚有缺口'),('loop','ret','继续下一轮'),('ver','unk','齐备或第3轮结束'),('unk','gate','计算结果'),('gate','out','检查并落库')])
    diagram('05-staged','05 · Nexent分阶段MCP查询状态机','上游Nexent推理单独取证；MCP程序工具本身不进行推理。',[
        [node('open','govkb_open_query','锁定事实及快照，创建staged任务；有证据需求→needs_retrieval，否则can_finalize。',d+'workflow.py:Workflow.open_query')],
        [node('retrieve','govkb_retrieve_query','仅needs_retrieval且已核验轮次<3可取；重复needs_verification直接回同一retrieval。',d+'workflow.py:Workflow.retrieve_query')],
        [node('verify','govkb_verify_query','needs_verification→核验span，累计found_ids和rounds；重复调用复用已核验轮。',d+'workflow.py:Workflow.verify_query','gate'),node('state','govkb_query_state','只返回已存状态，供主控决定下一工具；不授权任意变更。',d+'server.py:create_app.govkb_query_state')],
        [node('missing','needs_retrieval／can_finalize','有缺口且不足3轮继续；其余可结束，缺证据项保留unknown。',d+'workflow.py:Workflow._verify_retrieval','gate')],
        [node('final','govkb_finalize_query','只接受can_finalize／complete；每次包括重试都重读当前暂停；首次保存持久化答复。',d+'workflow.py:Workflow.finalize_query')],
        [node('history','历史追溯','原始答复与current_availability并列；旧快照不能绕过当前暂停。',d+'workflow.py:Workflow.history')]
    ], [('open','retrieve','needs_retrieval'),('open','final','can_finalize'),('retrieve','verify','needs_verification'),('verify','missing','核验结果'),('missing','retrieve','有缺口且<3轮'),('missing','final','can_finalize'),('state','missing','查询状态'),('final','history','complete')])
    diagram('06-rules','06 · 三值规则、范围与最终分流','程序判断资格条件；模型仅提供候选与支持信号。',[
        [node('expr','校验JSON表达式','最大深度12、节点128；all/any每组1–16；in列表1–32；运算符及字段白名单。',d+'logic.py:validate_expression','gate')],
        [node('value','校验事实的严格类型','bool不等价于1；数字须有限，日期严格YYYY-MM-DD；非法／缺失事实为unknown。',d+'logic.py:valid_value','gate')],
        [node('truth','Kleene三值计算','all：任一false→false，全true→true，其余unknown；any：任一true→true，全false→false；not保留unknown。',d+'logic.py:evaluate')],
        [node('scope','复核状态、地区与生效窗口','未确认→unknown；地区不符→false；时间未核→unknown；起日含、止日不含。',d+'service.py:DecisionService._scope','gate')],
        [node('deps','构建要求依赖和可溯源路径','事项→分支→要求→材料／环节／条件／适用范围→规范条款→证据→原件；定位按Unicode码点[start,end)。',d+'service.py:DecisionService._requirement')],
        [node('pause','当前依赖暂停优先','只暂停受影响条目；即便固定旧版本也先检查当前gate。',d+'service.py:DecisionService.finalize','gate'),node('split','未暂停项按三值分流','true→确定清单，false→排除，unknown→待核并提供可选补充字段。',d+'service.py:DecisionService.finalize')],
        [node('result','合成状态与分环节输出','branch_not_applicable / needs_branch_selection / no_confirmed_requirements / partial / ready / paused / insufficient_evidence。',d+'service.py:DecisionService.finalize')]
    ], [('expr','value','合法'),('expr','truth','无效规则返回unknown'),('value','truth','事实'),('truth','scope','条件结果'),('scope','deps','范围结果'),('deps','pause','依赖集'),('pause','split','其余条目'),('pause','result','暂停条目'),('split','result','聚合')])
    diagram('07-wf-update','07 · 8780模拟更新、冲突暂停与候选','此链路读取本仓库fixture；不是官网采集或真实政策更新。',[
        [node('beat','heartbeat记录存活','默认paused；force可单轮；claim租约120秒、总预算600秒；已有轮占用则already_running。',d+'workflow.py:Workflow.heartbeat')],
        [node('resume','优先恢复processing阶段','每轮至多处理3个包／来源；只处理enabled且approved的到期来源。',d+'workflow.py:Workflow.heartbeat')],
        [node('compare','读取模拟来源并计算指纹','baseline / changed / unavailable / noise；同指纹标unchanged；读取失败只记来源失败。',d+'workflow.py:Workflow._check','gate')],
        [node('change','生成包并立即暂停受影响要求','新generation；旧未发布包superseded；候选严格绑定现有要求、动作及原文；写block与audit_gate。',d+'workflow.py:Workflow._check')],
        [node('classify','discovered → classified','更新主控规划＋Jev合同回放分类；明确标synthetic_contract_replay_not_jev。',d+'workflow.py:Workflow._process')],
        [node('extract','classified → extracted → validated','fixture提取器＋结构检查＋核验角色；冲突清单；原文精确匹配信号不代表真实Jev效果。',d+'workflow.py:Workflow._extract_packet')],
        [node('docs','文档及本地稀疏索引更新','检索索引使用汉字双字组／拉丁词归一化余弦；不是稠密向量；来源guard须仍有效。',d+'workflow.py:Workflow._process')],
        [node('wait','awaiting_review','业务规则不自动发布；候选保留引用；人工期限带时区；失败保留阶段供后续恢复。',d+'workflow.py:Workflow.deadline','gate')]
    ], [('beat','resume','获租约且允许'),('resume','compare','空余本轮额度'),('compare','change','指纹改变'),('change','classify','保存包'),('classify','extract','成功'),('extract','docs','核验完成'),('docs','wait','写候选'),('wait','beat','下一轮仅恢复未完成包')])
    diagram('08-wf-publish','08 · 8780分项复核、发布及回退','这里的professional是模拟工程身份；正式业务审批仍需真实验收。',[
        [node('select','选择待审包与对象','仅awaiting_review；ID不重复、不含已发布项；可accepted或rejected且意见1–2000字符。',d+'workflow.py:Workflow.review')],
        [node('basis','记录逐对象复核依据','actor、decision、note、time和basis_hash绑定；仅复核不切换知识版本。',d+'workflow.py:Workflow.review')],
        [node('head','发布前核对当前头及来源','expected_head一致；来源仍启用、批准、当前包且内容指纹相同；依赖不能相对base变化。',d+'workflow.py:Workflow.publish','gate')],
        [node('actor','核对同一发布人已接受','accepted review由同一actor给出且basis_hash精确匹配；任一项不符整次失败。',d+'workflow.py:Workflow.publish','gate')],
        [node('merge','合并选中的知识记录','要求、别名或本体提案；本体扩展登记executable=false，不伪称新规则可执行。',d+'workflow.py:Workflow.publish')],
        [node('valid','生成传递依赖复核哈希','验证全图、原文span及review；压缩活动快照中无引用记录，历史快照仍保存。',d+'models.py:reviewed_hash','gate')],
        [node('switch','事务内切换wf_head','更新知识／文档版本及accepted_hash；部分或全部发布；旧依赖哈希仍会保持暂停。',d+'workflow.py:Workflow.publish')],
        [node('rollback','显式回退历史版本','检查expected_head与目标版本指纹；只切换头，conflicts_automatically_cleared=false。',d+'workflow.py:Workflow.rollback')]
    ], [('select','basis','复核'),('basis','head','人工发起发布'),('head','actor','校验通过'),('actor','merge','通过'),('merge','valid','候选新快照'),('valid','switch','完整验证成功'),('switch','rollback','需要回退时'),('rollback','head','下次发布重新校验')])
    diagram('09-acquisition','09 · 公开资料注册、调度与增量采集','knowledge_fetch＋registry链路；本次仅静态分析，不触发采集或打开私有资料。',[
        [node('reg','注册审核后的来源配置','HTTP(S) origin、允许路径、seeds、正文选择器、范围与间隔；启用须近期访问及使用审核；配置变化清除条件请求缓存。',k+'knowledge/registry.py:KnowledgeStore.register')],
        [node('sched','schedule到期入队','只入enabled且未暂停来源；发现模式创建cycle，否则seeds和既有URL入队；不重复有效任务。',k+'knowledge_fetch.py:schedule')],
        [node('claim','worker_once认领一项','BEGIN IMMEDIATE；同来源运行互斥；attempts<3；常规租约180秒、OCR600秒。',k+'knowledge_fetch.py:worker_once','gate')],
        [node('fetch','robots、网络与正文获取','校验DNS／地址、路径、大小、限速、重定向、证书及robots；原采集器使用直连接线，专用代理实验不等于通用路径已修好。',k+'knowledge_fetch.py:Fetcher.fetch')],
        [node('index','索引／RSS／Atom／Sitemap','record_index记录链接、深度、范围与截断信号；发现不等于已采集或业务有效。',k+'knowledge_discovery.py:record_index'),node('304','304 未变化','刷新检查时间；保留已存原件与批准；可从原件继续发现附件。',k+'knowledge_fetch.py:worker_once'),node('200','200 正文','仅HTML／PDF；网页空壳不可冒充正文；附件模式要求真实PDF头。',k+'knowledge_fetch.py:worker_once')],
        [node('parse','隔离解析及选段','HTML选择器／段落组／条款；PDF子进程30秒、OCR150秒；无选中正文隔离；新变化不继承旧生效日。',k+'knowledge_fetch.py:worker_once')],
        [node('ingest','原件保全、修订去重和候选','归档完整HTTP原件＋选择段；提出分类、关联附件；成功done并在全来源任务完成后刷新last_success。',k+'knowledge/registry.py:KnowledgeStore.ingest')],
        [node('fail','失败／暂停／退避','AccessStopped立即暂停来源；其他失败最多3次，退避60/300/1800或Retry-After；过期最后租约标failed。',k+'knowledge_fetch.py:worker_once','stop')]
    ], [('reg','sched','已注册'),('sched','claim','到期'),('claim','fetch','获任务'),('fetch','index','索引响应'),('fetch','304','304'),('fetch','200','200正文'),('fetch','fail','网络／访问／格式失败'),('200','parse','解析'),('parse','ingest','有效选段'),('parse','fail','空壳／超时／不符'),('index','sched','发现后续URL'),('fail','sched','允许重试时')])
    diagram('10-kb-lifecycle','10 · 原件、修订、复核、评测与发布','KnowledgeStore为独立knowledge_update运行目录；只读分析不创建或切换业务版本。',[
        [node('raw','内容寻址归档原件','SHA256命名、排他写入；已有原件须哈希匹配；保存原文定位、分类及全部选段。',k+'knowledge/registry.py:KnowledgeStore.ingest')],
        [node('dedup','正文／元数据／观察信号去重','相同正文且观察符合可保留修订；旧文本回归也按观察先后保留；元数据纠正新增修订。',k+'knowledge/registry.py:KnowledgeStore.ingest','gate')],
        [node('pending','pending_review','新增来源日期置未知；范围、usage_basis、冲突、生效证据须复核；采集成功不增加批准数。',k+'knowledge/registry.py:KnowledgeStore.review')],
        [node('review','人工review并绑定digest','confirmed=true＋具名reviewer；校验原件／选段指纹和reviewed_start；范围必须已核。',k+'knowledge/registry.py:KnowledgeStore.review','gate'),node('exclude','exclude／reopen／amend','排除须具名、理由和确认并生成处置修订；重开恢复待审且清除日期；观察证据不可改写。',k+'knowledge/registry.py:KnowledgeStore.exclude')],
        [node('candidate','按地区＋事项建立candidate','未分类潜在相关发现阻止生成；最新修订须复核或有有效排除；版本绑定members及parent。',k+'knowledge/registry.py:KnowledgeStore.candidate')],
        [node('quality','绑定独立评测与发布质量门','business策略：至少30例、Recall@5≥0.9、claim support=1.0、来源新鲜；attributed_v2另校核题集来源与独立性。',k+'knowledge_quality.py:validate_report','gate')],
        [node('pub','明确维护人确认后publish','必须仍为candidate、parent等于当前头、范围成员未变且质量通过；事务内切换heads。',k+'knowledge/registry.py:KnowledgeStore.publish','gate')],
        [node('back','rollback历史published版本','显式维护人确认；重新验证全部成员；保留历史原件与引用。',k+'knowledge/registry.py:KnowledgeStore.rollback')]
    ], [('raw','dedup','生成指纹'),('dedup','pending','变化新增'),('pending','review','符合条件后人工确认'),('pending','exclude','排除／纠正'),('exclude','pending','重开／amend'),('review','candidate','reviewed'),('candidate','quality','版本绑定'),('quality','pub','核验达标'),('pub','back','显式需要时')])
    diagram('11-kb-query','11 · 正式知识检索与当前暂停门','共享候选目录及预览资料不自动进入此检索；没有已发布头时返回NO_PUBLISHED_KNOWLEDGE。',[
        [node('input','query＋region＋matter＋日期','问题1–300字符；可显式选择已发布版本，区分过去／当前／未来日期视图。',k+'knowledge/registry.py:KnowledgeStore.search')],
        [node('version','查scope中的published版本','默认当前heads；指定版本必须属于相同scope且state=published。',k+'knowledge/registry.py:KnowledgeStore.search','gate'),node('none','EMPTY：无已发布知识','knowledge_version=null、citations=[]；不将待审候选凑成办理结论。',k+'knowledge/registry.py:KnowledgeStore.search','stop')],
        [node('members','逐成员验证原件、正文与复核','按已核定的时间起点及修订顺序为每文档选择适用修订；未来未生效／已终止项排除。',k+'knowledge/registry.py:KnowledgeStore._verify','gate')],
        [node('rank','字面召回并绑定完整引用','score_literal＋top_five；引用含revision、原件SHA、locator、chunk ordinal、版本和日期；不是稠密检索。',k+'knowledge/registry.py:KnowledgeStore.search')],
        [node('current','重查最新状态及来源新鲜度','待复核、待发布、潜在相关未分类、来源暂停／失败／停用／超期均构成当前视图阻断。',k+'knowledge/registry.py:KnowledgeStore.search','gate')],
        [node('blocked','BLOCKED且不返回citations','UPDATE_PENDING_REVIEW / UPDATE_AWAITING_PUBLICATION / SOURCE_CHECK_REQUIRED。',k+'knowledge/registry.py:KnowledgeStore.search','stop'),node('ok','OK／EMPTY及历史标记','显式旧发布版本可历史追溯并报告freshness；不能把历史原文当作当前可办结论。',k+'knowledge/registry.py:KnowledgeStore.search')]
    ], [('input','version','校验输入'),('version','none','无匹配版本'),('version','members','存在'),('members','rank','验证通过'),('rank','current','检索结果'),('current','blocked','当前视图且存在风险'),('current','ok','无阻断或显式历史版本')])
    diagram('12-engine','12 · 8775维护引擎的独立Agent链路','与公开修订库并列；8778使用synthetic replay，不能当作真实Jev准确率证据。',[
        [node('tick','Worker.tick独立心跳','写running/idle/waiting_sources；现有线程续租；失租或超时尝试停止driver并abort。',k+'knowledge/worker.py:Worker.tick')],
        [node('due','工作启动门','enabled、来源存在、暂停／trigger、到期检查／待重试／待文档发布；claim单写租约与预算。',k+'knowledge/worker.py:Worker.tick','gate')],
        [node('poll','Engine.poll采集并比较group','来源配置＋正文＋processing_hash；变化淘汰旧job和旧待审candidate，入分文档任务。',k+'knowledge/engine.py:Engine.poll')],
        [node('jobs','select_jobs选择有界批次','只选最新来源组、enabled且approved；受max_documents和max_segments约束；fence绑定run。',k+'knowledge/engine.py:Engine.select_jobs','gate')],
        [node('native','NativeDriver／回放编排','Jev分类→提取子Agent提交原文支持claim→核验子Agent核对遗漏冲突→Jev支持信号→finish_job。',k+'knowledge/native.py:NativeDriver')],
        [node('docs','publish_documents','全部选中任务processed才发文档；先剔除撤权来源，生成向量并校验维度、有限值及原件。',k+'knowledge/engine.py:Engine.publish_documents')],
        [node('rules','人工review／规则发布','文件可检索不等于业务规则批准；本体候选、冲突和支持度须复核；版本头与来源仍须核对。',k+'knowledge/engine.py:Engine'),node('retry','等待凭据／等待供应商／重试','缺Jev凭据不启动无效Agent；异常设置60秒重试并abort；持久化阶段供恢复。',k+'knowledge/worker.py:Worker.round','stop')]
    ], [('tick','due','无活动线程'),('due','poll','获租约'),('poll','jobs','检查完成'),('jobs','native','有选中任务及凭据'),('jobs','docs','无模型工作但待发布'),('native','docs','全部processed'),('native','retry','外部失败'),('jobs','retry','缺凭据'),('docs','rules','待人工复核')])
    diagram('13-assets','13 · 多模态资产准备及资料查询','当前AssetQuery只接受synthetic快照；返回原文与定位，不自动给出办理判定。',[
        [node('prep','准备文本、表格、图文资产','准备脚本统一asset_id/source_id/sha256/segments；图像可OCR，原件与索引分别保存。','scripts/prepare_topic3_data.py')],
        [node('role','tenant/user角色与参数','角色必须citizen或staff；query≤500字符；limit严格整数1–6。',k+'asset_query.py:AssetQuery.search','gate')],
        [node('load','加载snapshot并验证完整性','assets＋vectors总哈希、docs版本、raw目录边界、原件SHA、segment唯一ID、向量顺序与维度。',k+'asset_query.py:AssetQuery.load','gate')],
        [node('lex','主题覆盖及字面候选门','费用／时限／地址缺主题证据或没有候选→EMPTY；保守门不构成一般可回答性证明。',k+'asset_query.py:AssetQuery.search','gate')],
        [node('embed','查询embedding与余弦排序','本地11434；模型／维度须与快照一致；无有效向量或网络失败返回错误。',k+'asset_query.py:AssetQuery.embed')],
        [node('out','原文＋版本＋可点击定位','引用带asset/source/segment/sha/locator；Markdown转义不改JSON原文；界面8774原文查看。',k+'asset_query.py:AssetQuery.search')],
        [node('audit','审计必须成功','记录问题和principal哈希、耗时、请求ID与结果；审计文件不可写则AUDIT_UNAVAILABLE。',k+'asset_query.py:AssetQuery.search','gate')]
    ], [('prep','load','离线快照'),('role','load','有权限'),('load','lex','校验通过'),('lex','embed','有字面候选'),('embed','out','余弦排名'),('out','audit','返回前记录')])
    diagram('14-m1-m2','14 · M1空白底座与M2材料交接','早期工程链路；M2仍是候选材料演示，formal_approval=false。',[
        [node('m1','M1身份和环境工具','role-map定角色，空白库只查状态／证据／前置条件；缺资料保持EMPTY／BLOCKED；不写正式审批。',k+'core.py:GovernmentTools')],
        [node('task','M2群众创建task','问题1–300字符；拦身份证号／邮件模式；固定候选事项和版本；初始材料unknown。',k+'m2.py:M2Tools.dispatch')],
        [node('search','查引用与声明材料','检索限制task版本、事项与地区；声明仅identity_card/household_register，各为present/missing/unknown。',k+'m2.py:M2Tools.dispatch')],
        [node('handoff','幂等交接','task不再draft后不能任意改材料；同idempotency_key重复交接复用原单；保存引用和材料快照。',k+'m2.py:M2Tools.dispatch')],
        [node('staff','工作人员队列与前置核对','仅同租户handoff；原身份证missing／unknown分别展示；户口簿要求等保留待核。',k+'m2.py:M2Tools.assessment')],
        [node('confirm','显式确认assessment_sha256','confirmed=true且指纹一致；冲突不覆盖；记录demo_ui_confirmation，不声称业务规则已审定。',k+'m2.py:M2Tools.dispatch','gate')],
        [node('done','demo_reviewed＋工具审计','租户、所有权和工具角色矩阵均校验；数据库锁／存储失败返回明确错误。',k+'m2.py:M2Tools.execute')]
    ], [('m1','task','后续独立M2入口'),('task','search','draft'),('search','handoff','群众发起'),('handoff','staff','工作人员可见'),('staff','confirm','展示评估后'),('confirm','done','一致且确认')])
    diagram('15-preview','15 · 8781公开原文原生预览','历史记录：二七预览1次成功后关闭本轮预算；本次没有重新调用或重验。',[
        [node('context','检查manifest与当前gates','原件校验、固定来源、public标签；材料／时限等暂停项不能缺失；source_stopped时阻断。',k+'public_preview.py:Preview._context','gate')],
        [node('budget','begin_run预算门','整轮最多3次；成功passed或external_block后拒绝；重试需失败状态和8–300字修复说明。',k+'public_preview.py:Preview.begin_run','gate')],
        [node('open','主控open_query固定情境','CASE及QUESTION必须精确匹配；只有活动run能创建任务；同run复用任务。',k+'public_preview.py:Preview.open_query')],
        [node('ret','检索子Agent retrieve','从manifest提取五项来源事实，绑定原文quote／SHA／locator；opened→retrieved。',k+'public_preview.py:Preview.retrieve')],
        [node('verify','核验子Agent verify','验证精确检索与活动run；先持久化Jev预留计数／verifying；相同任务已核验结果可复用。',k+'public_preview.py:Preview.verify','gate')],
        [node('jev','DeepSeek英译→Jev单次支持核验','逐原文陈述＋提前30天负例；要求英文回执；保留中文来源和请求／响应证据。',k+'public_preview.py:LiveVerifier.__call__')],
        [node('final','finalize重新核对绑定与回执','回执SHA、probabilities、threshold=.8均须一致；≥.8仅source_supported；负例≤.2标拒绝。',k+'public_preview.py:Preview.finalize','gate'),node('stop','外部失败立即封闭本轮','凭据／余额／访问／限流／传输等失败写external_block；不自动追加请求。',k+'public_preview.py:Preview.verify','stop')],
        [node('out','partial_preview持久化答复','未核业务结论继续paused；advance_days未知；published=false、formal_approval=false；重复答复须一致。',k+'public_preview.py:Preview.finalize')]
    ], [('context','budget','通过'),('budget','open','获准本轮'),('open','ret','opened'),('ret','verify','retrieved'),('verify','jev','预留额度后'),('jev','final','有效回执'),('jev','stop','外部失败'),('final','out','来源支持与业务暂停并列')])
    diagram('16-regional','16 · 8782北京／南京跨地区原生预览','历史结果PARTIAL_BLOCKED：南京通过；北京唯一重试受传输阻断，不能标两地全通过。',[
        [node('context','校验两地区候选快照','北京15段／南京3段；共享目录与原件SHA一致；独立候选库；地区／事项隔离，24小时快照约束。',k+'regional_preview.py:check_context','gate')],
        [node('budget','有界活动与场景顺序','整轮原生≤3，Jev尝试≤3；北京先首次、两地首次后才重试；成功场景不再跑；不得并发活动。',k+'regional_preview.py:RegionalPreview.begin_run','gate')],
        [node('open','按scenario_id建任务','活动run必须匹配scenario；绑定两地context及当前暂停；同一run任务幂等。',k+'regional_preview.py:RegionalPreview.open_query')],
        [node('ret','只取本场景完整原文条件','检索保留选段、来源及相关条件；不把北京材料当南京依据，反之亦然。',k+'regional_preview.py:RegionalPreview.retrieve')],
        [node('verify','核验与专属负例','南京承诺3天无依据；北京对应情境负例；支持核验只解释来源，不决定现行适用性。',k+'regional_preview.py:RegionalPreview.verify')],
        [node('receipt','保存实际英文与Jev回执','最终实现请求前存实际英文；旧北京失败仅有英文哈希，不能恢复或补造正文。',k+'regional_preview.py:RegionalLiveVerifier.__call__')],
        [node('final','返回前重新验源、暂停和回执','逐项分数／未知值／暂停原因；持久化最终答复必须一致。',k+'regional_preview.py:RegionalPreview.finalize','gate'),node('stop','external_block全轮停止','不得清空预算、自动换供应商或将南京通过扩展为北京通过。',k+'regional_preview.py:RegionalPreview.verify','stop')]
    ], [('context','budget','有效范围'),('budget','open','活动场景'),('open','ret','opened'),('ret','verify','retrieved'),('verify','receipt','预留Jev额度'),('receipt','final','有效结果'),('receipt','stop','传输／鉴权／余额阻断')])
    diagram('17-model','17 · 模型开关、翻译、分类与Jev校验','本次读取默认配置：inference_enabled=false、form_verifier_enabled=false；不检查或展示密钥值。',[
        [node('port','角色工具白名单与推理开关','prepare只记录路由；WiredRoles.invoke仍调OfflineRoles；request才进入真实推理边界。',d+'connections.py:Connections.request','gate')],
        [node('class','原始payload类别与输入预算','递归数据标签先于投影／翻译；工作台仅synthetic；public走隔离预览；UTF-8字节限额。',k+'privacy.py:data_classes_allowed','gate')],
        [node('ready','先检查供应商就绪和调用预算','已知缺凭据／封闭预算时不先浪费翻译；官方Jev固定版本jev-1.13.0。',k+'classification.py:JevProvider.check_ready','gate')],
        [node('english','提取中文语义槽并英译','不改ID/SHA/locator/URL等opaque字段；保留否定、例外、范围、数字、单位；缓存限相同文本及配置。',k+'jev_translation.py:EnglishJevProvider')],
        [node('validate','核对译文及翻译后大小','字段必须一一对应，数字一致，不能残留中文；重新算UTF-8请求字节；英文与中文原文可对照。',k+'jev_translation.py:EnglishJevProvider','gate')],
        [node('jev','Jev分类／原文支持概率','按窗口合并文档类型、业务领域、片段用途；问题数及字节预算；响应模型／问项／概率均校验。',k+'classification.py:classify_corpus')],
        [node('review','不确定、近似类别及冲突待核','阈值、top类别间距、用途冲突供人工复核；支持概率不替代业务判断；记录usage与缓存token明细。',k+'classification.py:validate_response'),node('network','网络及外部失败边界','云端DeepSeek/Jev沿用已有环境／系统代理且TLS校验；本地服务直连。通用采集／embedding路径接线需分别看源码。',k+'deepseek.py:chat')]
    ], [('port','class','明确真实调用且已启用'),('class','ready','标签通过'),('ready','english','需要英译'),('english','validate','译文'),('validate','jev','满足预算'),('ready','jev','无需英译'),('jev','review','有效响应'),('jev','network','请求与错误处理')])
    diagram('18-ops','18 · 运行、心跳、维护与恢复','配置可启动≠当前运行；本图按实现描述，未读取私有运行目录或启动任何进程。',[
        [node('launch','启动脚本和独立运行目录','8780决策；8772公开知识看板＋8773只读MCP；8775维护容器／8778演示；8781/8782隔离预览。','scripts/start_knowledge_update.ps1')],
        [node('super','knowledge_service supervisor','独占supervisor.lock；web、worker、maintenance及可选mcp；每15秒检查子进程并写service-state。',k+'knowledge_service.py:supervise')],
        [node('watch','进程／心跳过期恢复','worker启动360秒后、maintenance3600秒后检查stale；失活terminate→必要时kill→重启；Windows后台无可见窗口。',k+'knowledge_service.py:supervise')],
        [node('worker','公开采集worker自然周期','默认60秒轮询，max_jobs≤100；schedule＋worker_once；服务暂停文件约束自动恢复入口。',k+'knowledge_cli.py:main')],
        [node('maintenance','维护锁、备份及恢复核验','Asia/Shanghai：每日bundle、每周恢复检查；可用磁盘≥1GiB；失败1小时退避；不自动删备份。',k+'knowledge_maintenance.py:maintenance_tick')],
        [node('signal','只记录有意义的变化','来源暂停／陈旧／失败、待审、worker失活及恢复任务告警；signal指纹相同保持安静。',k+'knowledge_maintenance.py:maintenance_tick')],
        [node('recovery','OS恢复与Docker分别登记','Docker unless-stopped；Windows任务必须独立注册及核查，不能由进程心跳推出系统重启可恢复。',k+'knowledge_startup.py'),node('stop','本轮心跳试验已结束','文档记录短周期南京采集：正文未变、无新修订／发布；测试源暂停、无永久轮询；不能推断常驻真实更新已启用。','docs/赛题3资料/心跳与主动知识更新测试-2026-10-18.md','stop')]
    ], [('launch','super','公开采集器启动入口'),('super','watch','持续监控'),('watch','super','子进程退出／失活'),('super','worker','采集子进程'),('super','maintenance','维护子进程'),('maintenance','signal','状态对比'),('launch','recovery','恢复另行配置'),('signal','stop','最新历史试验范围')])


PALETTE = {'process': ('#eef4ff','#3368b7'), 'gate': ('#fff4d8','#a56b13'), 'stop': ('#ffeded','#b74343')}


def wrap(text, limit):
    # CJK counts as two display units; preserve readable labels without dependencies.
    rows, current, width = [], '', 0
    for char in text:
        size = 2 if ord(char)>255 else 1
        if width+size > limit:
            rows.append(current); current, width = '', 0
        current += char; width += size
    if current: rows.append(current)
    return rows


def svg_for(graph):
    boxw, boxh, step, margin = 330, 116, 186, 60
    maxcol=max(n['col'] for n in graph['nodes'])
    width=max(800, (maxcol+1)*440+margin*2)
    height=(max(n['row'] for n in graph['nodes'])+1)*step+100
    pos={n['id']:(margin+n['col']*440,70+n['row']*step) for n in graph['nodes']}
    out=[f'<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{html.escape(graph["title"])}" viewBox="0 0 {width} {height}" width="{width}" height="{height}">',
         '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#72859b"/></marker></defs>',
         '<style>text{font-family:Microsoft YaHei,Segoe UI,sans-serif}.node{cursor:pointer}.node:hover rect{stroke-width:3}.edgeLabel{paint-order:stroke;stroke:#fff;stroke-width:5;stroke-linejoin:round}</style>',
         f'<rect width="{width}" height="{height}" fill="#fff"/><text x="60" y="32" fill="#183049" font-size="18" font-weight="700">{html.escape(graph["title"])}</text>']
    for a,b,label in graph['edges']:
        x,y=pos[a]; tx,ty=pos[b]
        if ty-y>step and tx==x:
            sx,sy,ex,ey=x+boxw,y+boxh/2,tx+boxw,ty+boxh/2
            outer=x+boxw+42
            path=f'M {sx} {sy} L {outer} {sy} L {outer} {ey} L {ex} {ey}'
            lx,ly=outer+6,sy-10
        elif ty>y:
            sx,sy,ex,ey=x+boxw/2,y+boxh,tx+boxw/2,ty
            mid=(sy+ey)/2
            path=f'M {sx} {sy} C {sx} {mid}, {ex} {mid}, {ex} {ey}'
            lx,ly=(sx+ex)/2,mid-7
        elif ty==y:
            sx,sy,ex,ey=x+boxw,y+boxh/2,tx,ty+boxh/2
            if tx<x: sx,ex=x,tx+boxw
            path=f'M {sx} {sy} L {ex} {ey}'; lx,ly=(sx+ex)/2,sy-8
        else:
            sx,sy,ex,ey=x+boxw,y+boxh/2,tx+boxw,ty+boxh/2
            outer=max(sx,ex)+54
            path=f'M {sx} {sy} C {outer} {sy}, {outer} {ey}, {ex} {ey}'; lx,ly=outer-10,(sy+ey)/2
        out.append(f'<path d="{path}" fill="none" stroke="#72859b" stroke-width="1.7" marker-end="url(#arrow)"/>')
        out.append(f'<text class="edgeLabel" x="{lx}" y="{ly}" font-size="11" text-anchor="middle" fill="#496176">{html.escape(label)}</text>')
    for n in graph['nodes']:
        x,y=pos[n['id']]; fill,stroke=PALETTE[n['kind']]
        out.append(f'<g class="node" data-node="{n["id"]}" tabindex="0"><title>{html.escape(n["label"]+"："+n["note"]+"；源码："+n["ref"])}</title><rect x="{x}" y="{y}" width="{boxw}" height="{boxh}" rx="12" fill="{fill}" stroke="{stroke}"/>')
        for i,line in enumerate(wrap(n['label'],36)):
            out.append(f'<text x="{x+18}" y="{y+27+i*21}" font-size="15" font-weight="700" fill="#183049">{html.escape(line)}</text>')
        wrapped=wrap(n['note'],46)
        short=wrapped[:2]
        if len(wrapped)>2: short[-1]=short[-1].rstrip('。；，')+'…'
        for i,line in enumerate(short): out.append(f'<text x="{x+18}" y="{y+76+i*17}" font-size="11" fill="#486075">{html.escape(line)}</text>')
        out.append('</g>')
    return ''.join(out)+'</svg>'


def mermaid_for(g):
    lines=['flowchart TD']
    for n in g['nodes']:
        label=n['label'].replace('"', "'")
        lines.append(f'    n{n["id"].replace("-", "")}[' + '"'+label+'"' + ']')
    for a,b,label in g['edges']:
        lines.append(f'    n{a.replace("-", "")} -->|"{label}"| n{b.replace("-", "")}')
    lines+=['    classDef gate fill:#fff4d8,stroke:#a56b13','    classDef stop fill:#ffeded,stroke:#b74343']
    for kind in ('gate','stop'):
        ids=['n'+n['id'].replace('-', '') for n in g['nodes'] if n['kind']==kind]
        if ids: lines.append('    class '+','.join(ids)+' '+kind)
    return '\n'.join(lines)+'\n'


def write_csv(name, rows, fields):
    with (OUT/name).open('w', encoding='utf-8-sig', newline='') as stream:
        writer=csv.DictWriter(stream, fields, extrasaction='ignore'); writer.writeheader(); writer.writerows(rows)


def build():
    OUT.mkdir(exist_ok=True)
    (OUT/'diagrams').mkdir(exist_ok=True)
    data=scan()
    define_diagrams()
    symbol_map={s['id']:s for s in data['symbols']}
    for g in DIAGRAMS:
        ids={n['id'] for n in g['nodes']}
        assert len(ids)==len(g['nodes']), g['id']
        for a,b,_ in g['edges']: assert a in ids and b in ids, (g['id'],a,b)
        for n in g['nodes']:
            ref=n['ref']
            if ':' in ref:
                assert ref in symbol_map, ref
                n['line']=symbol_map[ref]['line']
            else:
                assert (ROOT/ref).is_file(), ref
                n['line']=1
        (OUT/'diagrams'/f'{g["id"]}.svg').write_text(svg_for(g),encoding='utf-8')
        (OUT/'diagrams'/f'{g["id"]}.mmd').write_text(mermaid_for(g),encoding='utf-8')
    config=json.loads((ROOT/'config/decision/connections.json').read_text('utf-8'))
    data['summary']={'scanned_at':datetime.now(timezone(timedelta(hours=8), 'Asia/Shanghai')).isoformat(), 'git_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'files':len(data['files']), 'python_modules':len(data['modules']), 'functions':sum(s['kind']=='function' for s in data['symbols']),
        'classes':sum(s['kind']=='class' for s in data['symbols']), 'events':sum(len(s['events']) for s in data['symbols']), 'text_assets':len(data['text_assets']),
        'test_functions':len(data['tests']), 'diagrams':len(DIAGRAMS), 'diagram_nodes':sum(len(g['nodes']) for g in DIAGRAMS),
        'inference_enabled':config['inference_enabled'],'form_verifier_enabled':config['form_verifier_enabled'],
        'note':'Static scan only; no business execution, provider request, private runtime read, test run, publication or push.'}
    data['diagrams']=DIAGRAMS
    write_csv('文件清单.csv',data['files'],['path','bytes','extension','sha256'])
    write_csv('函数索引.csv',({**s,'event_count':len(s['events']),'call_count':len(s['calls'])} for s in data['symbols']),['file','name','kind','line','end','signature','event_count','call_count'])
    write_csv('条件异常与返回明细.csv',({'file':s['file'],'symbol':s['name'],**e} for s in data['symbols'] for e in s['events']),['file','symbol','line','type','code'])
    write_csv('调用位置.csv',({'file':s['file'],'symbol':s['name'],**c} for s in data['symbols'] for c in s['calls']),['file','symbol','line','target'])
    write_csv('测试定义索引.csv',data['tests'],['file','name','line'])
    write_csv('路由与MCP工具.csv',({'file':m['path'],'kind':kind,**r} for m in data['modules'] for kind,items in [('route',m['routes']),('tool',m['tools'])] for r in items),['file','kind','line','name','signature','code'])
    write_csv('前端脚本与部署定位.csv',({'file':s['file'],**m} for s in data['text_assets'] for m in s['markers']),['file','line','type','code'])
    (OUT/'静态扫描数据.json').write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    template=(ROOT/'scripts/flow_atlas_template.html').read_text('utf-8')
    safe=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    (OUT/'index.html').write_text(template.replace('__ATLAS_DATA__',safe),encoding='utf-8')
    make_markdown(data)
    for path in (OUT/'diagrams').glob('*.svg'): ET.parse(path)
    markdown=(OUT/'流程分析.md').read_text('utf-8')
    links=re.findall(r'\]\(([^)]+)\)',markdown)
    missing=[target for target in links if not (OUT/target.split('#')[0]).resolve().exists()]
    assert not missing, missing
    js_path=OUT/'页面脚本校验.js'
    js_path.write_text(re.findall(r'<script>([\s\S]*?)</script>',template)[0],encoding='utf-8')
    node_binary=shutil.which('node')
    if node_binary:
        checked=subprocess.run([node_binary,'--check',str(js_path)],capture_output=True,text=True,timeout=30)
        assert checked.returncode==0, checked.stderr
    fingerprints=all(sha((ROOT/m['path']).read_bytes())==m['sha256'] for m in data['modules'])
    assert fingerprints, 'Source changed during build; regenerate once against stable files.'
    validation={'summary':data['summary'],'parse_errors':data['parse_errors'],'diagram_references_valid':True,
        'generated_svg_files':len(list((OUT/'diagrams').glob('*.svg'))),'generated_mermaid_files':len(list((OUT/'diagrams').glob('*.mmd'))),
        'svg_xml_parsed':True,'local_links_checked':len(links),'missing_local_links':missing,'source_fingerprints_match':fingerprints,
        'javascript_syntax':'passed' if node_binary else 'not_run_node_not_installed','browser_interaction_tested':False,
        'coverage_note':'All executable Python AST bodies under src/ and scripts/ are indexed. Events include branches, loops, exceptions, returns, short-circuit expressions and comprehensions. Calls are syntactic targets, not dynamically resolved call graphs. Non-Python assets are inventoried and linked; no claim of exhaustive runtime path coverage.'}
    (OUT/'生成校验.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(validation,ensure_ascii=False,indent=2))


def make_markdown(data):
    s=data['summary']
    lines=['# 项目文件扫描与分层流程详解','',f'扫描时间：{s["scanned_at"]}。源码基线：`{s["git_head"]}`，包含扫描时工作区文件（含未提交文件）；不是只按commit扫描。','',
        '交互入口：[离线流程图与源码明细](index.html)。无需联网或启动服务。点击图中节点可查看完整注释和源码定位；函数页支持名称、文件、条件和错误码搜索。', '',
        f'范围：{s["files"]}个项目文件，{s["python_modules"]}个src/scripts Python模块，{s["functions"]}个函数、{s["classes"]}个类、{s["events"]}条结构事件；{s["test_functions"]}个测试函数定义仅作索引，未执行。{s["diagrams"]}张详细流程图，{s["diagram_nodes"]}个有源码依据的节点。','',
        '## 阅读与完整性约定','',
        '- 业务图按真实入口手工梳理；每个节点标注职责、条件、数据、失败或状态以及源码位置。SVG图面提供摘要，完整说明见下方节点表及交互页。',
        '- Python细节逐函数提取完整源码、签名、条件、循环、with、try/except、raise/return、短路逻辑、条件表达式和推导式。静态调用名保留接收者，不猜测运行时动态绑定。',
        f'- 另收录{s["text_assets"]}个前端、PowerShell、Shell和部署源码文件的完整文本与行号，按事件／请求、函数／命令、控制关键字和部署项提供定位。这是文本定位，不声称对这些语言完成语义解析。其他配置、资料与证据逐文件列入清单。',
        '- 排除私有运行目录、密钥文件、Git内部、虚拟环境、缓存、模型权重、vendor与references依赖快照；不把第三方源码当作项目流程。详细排除路径见静态扫描数据。',
        '- 原始文件含源码注释和表达式，函数详情提供完整代码；流程图并不等于已执行所有路径。没有启动服务、读取真实个人回答、调用模型、采集官网、跑业务回归、发布或推送。',
        '- 默认模型和填表AI开关均关闭。公开原文支持、业务适用性、正式发布和赛题达标分别判断；历史证据不能证明当前服务状态。','',
        '## 分层结论','',
        '1. 当前8780工作台是模拟知识快照上的确定性规则与角色调度；三值规则、精确引用、冲突暂停、人工复核与版本机制真实存在，但路由配置不代表自动真实推理。',
        '2. 公开资料修订库（registry＋knowledge_fetch）和8775维护引擎（Engine＋Worker）是两套独立实现，版本表、任务、发布方式均不同；流程图没有把它们拼成已运行的自动生产链。',
        '3. 8781及8782公开资料原生预览使用独立public权限、运行次数与Jev预算门，输出原文支持信号，现行材料、时限、范围和业务发布仍受暂停保护。',
        '4. 最新接续记录注明：二七原生预览通过；南京通过；北京重试外部阻断且预算封闭；短周期心跳试验结束，测试来源已暂停。此处是已有记录，未在本次重验。',
        '5. 本体登记不等于可执行本体演化；fixture更新不等于真实官网更新；模拟演示不等于独立业务评测；63份共享目录不等于本机63份原件已入库。','',
        '## 各入口与数据边界','',
        '|入口／实现|主要用途|存储与边界|','|---|---|---|',
        '|8780 decision.server|分支咨询、填表、模拟更新与人工复核|.runtime/agent-workflow；synthetic；默认无模型推理|',
        '|8772看板／8773只读MCP|公开资料采集与修订库查询|.runtime/knowledge-update；原件、修订、复核、评测、scope发布；存在启动配置不代表当前已部署|',
        '|8775维护／8778回放|Agent候选提取与文档／规则版本|.runtime/knowledge及knowledge-demo；回放和真实供应商分别标注|',
        '|8781 public preview|二七固定公开原文的原生预览|隔离库与预算，public；不发布正式知识|',
        '|8782 regional preview|北京／南京固定原文原生预览|隔离候选与场景预算；北京受阻，南京历史通过|',
        '|M1／M2／资产查询|环境底座、模拟交接、多模态检索|各自运行目录；不得跨当成正式办理审批|','',
        '## 流程图与逐节点注释','']
    for g in data['diagrams']:
        lines += [f'### {g["title"]}','',g['subtitle'],'',f'![{g["title"]}](diagrams/{g["id"]}.svg)','',f'[SVG原图](diagrams/{g["id"]}.svg) · [可编辑Mermaid源](diagrams/{g["id"]}.mmd)','', '|节点|完整注释|源码|','|---|---|---|']
        for n in g['nodes']:
            path=n['ref'].split(':')[0]; target='../../'+path
            lines.append('|'+n['label'].replace('|','／')+'|'+n['note'].replace('|','／')+'|'+f'[{n["ref"]}:{n["line"]}]({target}#L{n["line"]})'+'|')
        lines.append('')
    lines += ['## 文件、函数、接口与条件明细','',
        '- [全部文件清单](文件清单.csv)：路径、字节数、类型；Python源码另有SHA256。',
        '- [函数／类／模块索引](函数索引.csv)：签名、起止行及结构事件数。',
        '- [条件、异常、返回和循环逐条明细](条件异常与返回明细.csv)：完整表达式和行号。',
        '- [静态调用位置](调用位置.csv)：调用语法目标与行号；不宣称全量动态调用解析。',
        '- [路由与MCP工具](路由与MCP工具.csv)：Route/Mount表达式及工具签名。',
        '- [前端脚本与部署定位](前端脚本与部署定位.csv)：完整源代码见交互页，CSV提供文本级事件／请求／控制关键字／命令定位。',
        '- [测试定义索引](测试定义索引.csv)：测试名称及位置，未运行。',
        '- [完整扫描数据](静态扫描数据.json)：模块导入、SQL建表名、源码、函数事件、图节点与排除说明。',
        '- [生成校验](生成校验.json)：AST解析结果、节点引用及图文件数。','',
        '## 已验证与未验证','',
        '本轮仅校验AST可解析、所有流程节点源码引用、图边连接、文档本地链接、HTML脚本语法和SVG渲染。未做浏览器交互实测，未复测真实模型、来源当前可达性、私有库发布数量或服务当前运行状态；历史状态以带日期的接续记录和证据报告阅读。','',
        '生成方式：在仓库根目录运行 `.venv/Scripts/python.exe scripts/build_flow_atlas.py`。生成器只解析代码与公开项目文件，不导入业务模块。新生成文件夹自动排除在自身扫描之外。','']
    (OUT/'流程分析.md').write_text('\n'.join(lines),encoding='utf-8')


if __name__ == '__main__':
    build()
