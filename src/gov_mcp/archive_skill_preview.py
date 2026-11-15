"""Reusable source-bound archive excerpt workflow behind the existing five MCP tools.

A new matter supplies a fixed plan of located archive excerpts. This executor
does not decide eligibility or infer new policy. No task opens without the user's
explicit structural approval file bound to the exact plan and implementation.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import threading
import uuid

FORMAT = "reviewed-archive-excerpt-plan-v1"
STATUS = {"formally_published":False,"formal_approval":False,"current_applicability":"unknown",
          "business_conclusion_status":"PAUSED_UNREVIEWED"}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def code_fingerprints():
    return {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ("archive_skill_preview.py","ontology_crossdoc_preview_server.py")}


class ArchiveSkillPreview:
    fingerprints = staticmethod(code_fingerprints)

    def __init__(self, root, plan_path):
        self.root, self.plan_path = Path(root), Path(plan_path)
        marker=self.root/'archive-skill-workspace.json'
        if not marker.is_file() or json.loads(marker.read_text('utf-8')).get('format') != FORMAT:
            raise ValueError("ISOLATED_ARCHIVE_SKILL_WORKSPACE_REQUIRED")
        self.lock=threading.Lock()

    def _bundle(self, approved=True):
        plan=json.loads(self.plan_path.read_text('utf-8'))
        if plan.get('format')!=FORMAT or plan.get('implementation')!=self.fingerprints():
            raise ValueError("PLAN_OR_IMPLEMENTATION_CHANGED_REVIEW_REQUIRED")
        if not re.fullmatch(r'[a-z][a-z0-9_]{2,63}',plan.get('scenario_id','')):
            raise ValueError("FIXED_SCENARIO_REQUIRED")
        corpus_path=Path(plan['corpus_path'])
        raw=corpus_path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=plan['corpus_file_sha256']:
            raise ValueError("SOURCE_SNAPSHOT_CHANGED")
        corpus=json.loads(raw)
        source_index={s['source_id']:s for s in corpus['manifest']['sources']}
        if len(source_index)!=len(corpus['manifest']['sources']):
            raise ValueError("SOURCE_MANIFEST_AMBIGUOUS")
        selected=[]
        nodes=plan.get('nodes')
        if not isinstance(nodes,list) or not 1<=len(nodes)<=10 or len({n['id'] for n in nodes})!=len(nodes):
            raise ValueError("BOUNDED_UNIQUE_EXCERPT_NODES_REQUIRED")
        for node in nodes:
            if set(node)!={'id','source_id','locator','text_sha256','operator'} or node['operator']!='exact_excerpt':
                raise ValueError("ONLY_EXACT_ARCHIVED_EXCERPT_OPERATOR_SUPPORTED")
            matches=[u for u in corpus['units'] if (u['source_id'],u['locator'])==(node['source_id'],node['locator'])]
            if len(matches)!=1:
                raise ValueError("SOURCE_LOCATOR_AMBIGUOUS_OR_MISSING")
            unit=matches[0]
            if hashlib.sha256(unit['text'].encode()).hexdigest()!=node['text_sha256'] or unit['text_sha256']!=node['text_sha256']:
                raise ValueError("SOURCE_TEXT_CHANGED")
            source=source_index[node['source_id']]
            original=Path(plan['original_paths'][node['source_id']])
            if hashlib.sha256(original.read_bytes()).hexdigest()!=source['original_sha256']:
                raise ValueError("SOURCE_ORIGINAL_CHANGED")
            selected.append({'node_id':node['id'],'source_id':unit['source_id'],'locator':unit['locator'],
                             'quote':unit['text'],'text_sha256':unit['text_sha256'],
                             'original_sha256':source['original_sha256'],'revision':source['revision'],'url':source['url']})
        binding=digest(plan)
        if approved:
            approval_path=self.root/'human-structural-review.json'
            if not approval_path.is_file():
                raise ValueError("EXPLICIT_HUMAN_STRUCTURE_REVIEW_REQUIRED")
            approval=json.loads(approval_path.read_text('utf-8'))
            actor=approval.get('actor',{})
            if (approval.get('decision')!='accept_structure' or approval.get('confirmed') is not True
                    or not isinstance(actor,dict) or actor.get('kind')!='human' or type(actor.get('name')) is not str or not actor['name'].strip()
                    or approval.get('plan_sha256')!=binding):
                raise ValueError("HUMAN_REVIEW_NOT_BOUND_TO_CURRENT_PLAN")
        if (json.loads(self.plan_path.read_text('utf-8')) != plan
                or hashlib.sha256(corpus_path.read_bytes()).hexdigest()!=plan['corpus_file_sha256']):
            raise ValueError("SOURCE_OR_PLAN_CHANGED_DURING_READ")
        for source_id, path in plan['original_paths'].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest()!=source_index[source_id]['original_sha256']:
                raise ValueError("SOURCE_ORIGINAL_CHANGED_DURING_READ")
        return plan,binding,selected

    def _unchanged(self,binding):
        if self._bundle()[1]!=binding:raise ValueError("SOURCE_OR_PLAN_CHANGED_DURING_STAGE")

    def preflight(self):
        plan,binding,selected=self._bundle(approved=False)
        return {'plan_sha256':binding,'scenario_id':plan['scenario_id'],'nodes':len(selected),
                'status':'PENDING_HUMAN_STRUCTURE_REVIEW','execution_operator':'exact_excerpt',**STATUS}

    def _load(self,task_id):
        if type(task_id) is not str or re.fullmatch(r'[a-f0-9]{32}',task_id) is None:
            raise ValueError("UNKNOWN_TASK")
        path=self.root/(task_id+'.json')
        if not path.is_file():raise ValueError("UNKNOWN_TASK")
        task=json.loads(path.read_text('utf-8'))
        plan,binding,selected=self._bundle()
        if task['plan_sha256']!=binding:raise ValueError("PLAN_CHANGED_OPEN_NEW_TASK")
        return task,plan,binding,selected

    def _save(self,task):
        path=self.root/(task['task_id']+'.json');temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(task,ensure_ascii=False),'utf-8');temp.replace(path)

    def open_query(self,scenario_id):
        with self.lock:
            plan,binding,selected=self._bundle()
            if scenario_id!=plan['scenario_id']:raise ValueError("FIXED_SCENARIO_REQUIRED")
            task={'task_id':uuid.uuid4().hex,'stage':'opened','plan_sha256':binding}
            self._save(task)
            self._unchanged(binding)
            return {**task,**STATUS}

    def retrieve(self,task_id):
        with self.lock:
            task,_,binding,selected=self._load(task_id)
            if task['stage']!='opened':raise ValueError("RETRIEVE_STAGE_ALREADY_USED")
            task['stage']='retrieved';self._save(task)
            self._unchanged(binding)
            return {'task_id':task_id,'evidence':selected,'stage':'retrieved',**STATUS}

    def verify(self,task_id):
        with self.lock:
            task,_,binding,selected=self._load(task_id)
            if task['stage']!='retrieved':raise ValueError("RETRIEVAL_REQUIRED_BEFORE_VERIFY")
            task.update(stage='verified',answer={'mode':'human_reviewed_archive_excerpt_projection',
                        'plan_sha256':binding,'citations':deepcopy(selected),'semantic_correctness_verified':False,**STATUS})
            self._save(task)
            self._unchanged(binding)
            return {'task_id':task_id,'stage':'verified','answer':task['answer'],**STATUS}

    def state(self,task_id):
        with self.lock:
            task,_,binding,_=self._load(task_id)
            self._unchanged(binding)
            return {**task,**STATUS}

    def finalize(self,task_id):
        with self.lock:
            task,_,binding,selected=self._load(task_id)
            if task['stage'] not in ('verified','complete'):raise ValueError("VERIFICATION_REQUIRED")
            if task['answer']['citations']!=selected or task['answer']['plan_sha256']!=binding:
                raise ValueError("VERIFIED_RESULT_CHANGED")
            task['stage']='complete';self._save(task)
            self._unchanged(binding)
            return deepcopy(task['answer'])
