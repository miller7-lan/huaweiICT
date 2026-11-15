"""A synthetic approval file proves gates only, never the user's real approval."""
import hashlib
import json

import pytest

from gov_mcp.archive_skill_preview import ArchiveSkillPreview, FORMAT, code_fingerprints, digest
from gov_mcp.ontology_crossdoc_preview_server import open_preview, create_app


def setup(tmp_path):
    root=tmp_path/'isolated';root.mkdir()
    original=root/'synthetic-original.txt';original.write_text('Synthetic archive bytes','utf-8')
    text='Synthetic yearly rule, not real policy.'
    unit={'source_id':'SYNTHETIC','locator':'p1','text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest()}
    corpus={'manifest':{'sources':[{'source_id':'SYNTHETIC','original_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),
              'revision':'fixture-rev','url':'https://example.invalid/archive'}]},'units':[unit]}
    path=root/'corpus.json';path.write_text(json.dumps(corpus),'utf-8')
    plan={'format':FORMAT,'implementation':code_fingerprints(),'scenario_id':'synthetic_archive',
          'corpus_path':str(path),'corpus_file_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
          'original_paths':{'SYNTHETIC':str(original)},
          'nodes':[{'id':'one','source_id':'SYNTHETIC','locator':'p1','text_sha256':unit['text_sha256'],'operator':'exact_excerpt'}]}
    planpath=root/'plan.json';planpath.write_text(json.dumps(plan),'utf-8')
    (root/'archive-skill-workspace.json').write_text(json.dumps({'format':FORMAT}),'utf-8')
    (root/'ontology-binding.json').write_text(json.dumps({'profile':FORMAT,'plan_path':str(planpath)}),'utf-8')
    return root,plan,original


def approve_fixture(root,plan):
    (root/'human-structural-review.json').write_text(json.dumps({'decision':'accept_structure','confirmed':True,
        'actor':{'kind':'human','name':'Synthetic fixture only'},'plan_sha256':digest(plan)}),'utf-8')


def test_live_mcp_registration_reuses_existing_surface_but_cannot_open_without_approval(tmp_path):
    root,plan,_=setup(tmp_path);preview=open_preview(root)
    assert preview.preflight()['status']=='PENDING_HUMAN_STRUCTURE_REVIEW'
    assert create_app(preview,'synthetic-token-'+'t'*32) is not None
    with pytest.raises(ValueError,match='EXPLICIT_HUMAN'):preview.open_query('synthetic_archive')
    assert not list(root.glob('[a-f0-9]'*32+'.json'))


def test_ordered_lifecycle_returns_exact_archive_result_and_stays_paused(tmp_path):
    root,plan,_=setup(tmp_path);approve_fixture(root,plan);preview=open_preview(root)
    task=preview.open_query('synthetic_archive')['task_id']
    with pytest.raises(ValueError,match='RETRIEVAL_REQUIRED'):preview.verify(task)
    read=preview.retrieve(task)
    with pytest.raises(ValueError,match='ALREADY_USED'):preview.retrieve(task)
    checked=preview.verify(task)
    final=preview.finalize(task)
    assert final==checked['answer'] and final['citations']==read['evidence']
    assert final['business_conclusion_status']=='PAUSED_UNREVIEWED' and final['semantic_correctness_verified'] is False
    assert preview.finalize(task)==final and preview.state(task)['stage']=='complete'


def test_revoked_or_wrong_plan_approval_cannot_continue(tmp_path):
    root,plan,_=setup(tmp_path);approve_fixture(root,plan);preview=open_preview(root)
    task=preview.open_query('synthetic_archive')['task_id']
    (root/'human-structural-review.json').unlink()
    with pytest.raises(ValueError,match='EXPLICIT_HUMAN'):preview.retrieve(task)
    approve_fixture(root,{**plan,'scenario_id':'other'})
    with pytest.raises(ValueError,match='NOT_BOUND'):preview.retrieve(task)


def test_original_change_prevents_final_result_even_with_old_approval(tmp_path):
    root,plan,original=setup(tmp_path);approve_fixture(root,plan);preview=open_preview(root)
    task=preview.open_query('synthetic_archive')['task_id'];preview.retrieve(task);preview.verify(task)
    original.write_text('Changed fixture archive','utf-8')
    with pytest.raises(ValueError,match='ORIGINAL_CHANGED'):preview.finalize(task)


def test_arbitrary_operators_and_task_paths_are_not_executable(tmp_path):
    root,plan,_=setup(tmp_path);approve_fixture(root,plan);preview=open_preview(root)
    with pytest.raises(ValueError,match='UNKNOWN_TASK'):preview.state('../../other')
    plan['nodes'][0]['operator']='publish'
    (root/'plan.json').write_text(json.dumps(plan),'utf-8')
    with pytest.raises(ValueError,match='ONLY_EXACT'):preview.preflight()


def test_duplicate_source_manifest_cannot_choose_a_last_source_silently(tmp_path):
    root,plan,_=setup(tmp_path)
    corpuspath=root/'corpus.json';corpus=json.loads(corpuspath.read_text('utf-8'))
    corpus['manifest']['sources']*=2;corpuspath.write_text(json.dumps(corpus),'utf-8')
    plan['corpus_file_sha256']=hashlib.sha256(corpuspath.read_bytes()).hexdigest()
    (root/'plan.json').write_text(json.dumps(plan),'utf-8')
    with pytest.raises(ValueError,match='MANIFEST_AMBIGUOUS'):open_preview(root).preflight()
