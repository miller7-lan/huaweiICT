#!/usr/bin/env python3
"""Check public receipt consistency; optionally reproduce against local private inputs.

No network calls, extraction, writes to review files, scores, or answer logging.
"""
from pathlib import Path
import argparse,hashlib,json,re,sys,unicodedata,zipfile

RECEIPT_DIR='evidence/chengqiancheng-closeout-20261112'
def digest(data):return hashlib.sha256(data).hexdigest()
def normalized(text):return re.sub(r'\s+','',unicodedata.normalize('NFKC',text))
def sections(text):
    return {s.partition('\n')[0].strip():s.partition('\n')[2].strip()
            for s in re.split(r'^## ',text,flags=re.M)[1:]}
def require(condition,code):
    if not condition:raise ValueError(code)
def load(directory,name):return json.loads((directory/name).read_text(encoding='utf-8'))

def check_public(root):
    directory=root/RECEIPT_DIR
    summary=load(directory,'reference-review-summary.json')
    quotes=load(directory,'quote-integrity.json')
    mapping=load(directory,'mapping-source-checks.json')
    comments=load(directory,'comment-resolution.json')
    status=load(directory,'status.json')
    integrity=load(directory,'review-package-integrity.json')
    dates=load(directory,'date-and-locator-corrections.json')
    require(summary['questions_reviewed']==30,'question_count')
    require(summary['quoted_assertions_checked']==58,'assertion_count')
    require(len(summary['items'])==30 and {x['id']for x in summary['items']}=={f'F{i:02}'for i in range(1,31)},'question_ids')
    require(len(quotes['items'])==58 and all(x['locator_found']and x['quote_match']for x in quotes['items']),'quote_checks')
    require(len({(x['id'],x['claim_index'])for x in quotes['items']})==58,'duplicate_quote_check')
    require(sum(x['quoted_assertions']for x in summary['items'])==58,'quote_count_reconciliation')
    require(len(mapping['items'])==9 and all(x['normalized_text_matches']for x in mapping['items']),'mapping_checks')
    require(len(comments['items'])==14 and len({x['id']for x in comments['items']})==14,'comment_count')
    require(all(x['disposition']=='output_opinion_no_corresponding_reference_error_identified'for x in comments['items']),'comment_disposition')
    require(len(integrity['members'])==13 and all(x['matched']for x in integrity['members']),'archive_manifest_checks')
    require(status['project_closeout_complete']is False and status['github_push_performed']is False,'completion_state')
    require(status['raw_scores_modified']is False and status['reference_modified']is False,'original_records')
    require(status['accepted_new_human_experiment_results']is False,'human_experiment_claim')
    require(status['owner_adoption_complete']is True and status['owner_adoption_date']=='2026-11-12','owner_adoption')
    require(comments['final_owner_adoption']=='confirmed','comment_adoption')
    tasks={x['id']:x['status']for x in status['tasks']}
    require(tasks['owner_adoption']=='completed','owner_adoption_status')
    require(tasks['joint_discussion']=='pending_two_reviewers','joint_discussion_status')
    require(tasks['method_b']=='waiting_official_candidates','method_b_status')
    require(tasks['r11_readthrough']=='waiting_private_documents','r11_status')
    require(tasks['scan_review']=='opinion_adopted_missing_process_fields_documented','scan_process_status')
    prediction=status['scan_prediction']
    require(prediction['minutes']==sum(prediction['breakdown_minutes'])==12,'scan_prediction_total')
    require(prediction['data_type']=='task_estimate_not_measured'and prediction['included_in_human_efficiency_results']is False,'scan_prediction_boundary')
    audit=load(directory,'remaining-input-search.json')
    require(audit['branches']==9 and audit['unique_trees']==len(audit['trees'])==8,'branch_search_count')
    require(all(x['truncated']is False and not x['private_candidate_paths']and not x['r11_document_paths']for x in audit['trees']),'branch_search_scope')
    expected=load(directory,'expected-private-inputs.json')
    require(len(expected['files'])==3,'expected_private_input_count')
    require(expected['files'][0]['unit_counts']=={'U1':6,'U2':9,'U3':9,'U4':10,'U5':4,'U6':8},'method_b_expected_counts')
    require(dates['F27']['source']=='B1'and dates['F27']['paragraphs']==[19,20],'F27_locator')
    require(dates['F29']['pub_date']=='2026-05-05'and dates['F29']['last_modified_date']=='2026-05-11','F29_dates')
    require(dates['F29']['changes_reference_or_scores']is False,'F29_disposition')
    for name in ['reference-review-summary.json','quote-integrity.json','mapping-source-checks.json','comment-resolution.json','status.json','review-package-integrity.json','date-and-locator-corrections.json','remaining-input-search.json','expected-private-inputs.json']:
        text=(directory/name).read_text()
        require('/Users/'not in text and 'C:/Users/'not in text,'absolute_personal_path')
        def inspect(value):
            if isinstance(value,dict):
                require(not({'answer','quote','key_assertions','must_not','stop_boundary','scores','active_minutes'}&set(value)),'private_payload_key')
                for x in value.values():inspect(x)
            elif isinstance(value,list):
                for x in value:inspect(x)
        inspect(json.loads(text))
    return directory,summary,quotes,mapping,integrity,dates

def check_private_package(path,quotes,mapping,integrity):
    raw=path.read_bytes()
    require(digest(raw)==integrity['archive_sha256'],'review_archive_sha256_mismatch')
    prefix='ict-holdout-review-20261105T003300Z/'
    with zipfile.ZipFile(path)as archive:
        require(archive.testzip()is None,'review_archive_crc')
        for m in integrity['members']:
            require(digest(archive.read(prefix+m['path']))==m['sha256'],'member_sha256_mismatch')
        reference=json.loads(archive.read(prefix+'reference.json'))
        questions=json.loads(archive.read(prefix+'questions.json'))
        blind=json.loads(archive.read(prefix+'blind-20261105T003300Z.json'))
        require(len(reference)==len(questions)==len(blind['items'])==30,'private_question_count')
        require(blind['run_id']=='20261105T003300Z','private_run_id')
        require({r['id']for r in reference}=={q['id']for q in questions},'private_question_ids')
        source_text={Path(n).name.split('-')[0]:archive.read(n).decode('utf-8-sig')for n in archive.namelist()if n.startswith(prefix+'sources/')and n.endswith('.md')}
        source_sections={k:sections(v)for k,v in source_text.items()}
        actual=[]
        for r in reference:
            for index,assertion in enumerate(r['key_assertions'],1):
                quote=assertion['quote'];source=assertion['source'];locator=assertion['locator']
                section=source_sections[source].get(locator)
                require(section is not None,'private_locator_missing')
                require(normalized(quote)in normalized(section),'private_quote_mismatch')
                actual.append({'id':r['id'],'claim_index':index,'source':source,'locator':locator,'quote_sha256':digest(quote.encode()),'locator_found':True,'quote_match':True})
        require(actual==quotes['items'],'private_quote_receipt_mismatch')
        for item in mapping['items']:
            section=source_sections[item['source']].get(item['locator'])
            require(section is not None,'mapping_locator_missing')
            require(digest(normalized(section).encode())==item['frozen_normalized_text_sha256'],'mapping_text_sha256_mismatch')
            require(item['reviewed_normalized_text_sha256']==item['frozen_normalized_text_sha256'],'mapping_previous_text_mismatch')
    return True

def check_html(path,dates):
    raw=path.read_bytes();expected=dates['F29']
    require(digest(raw)==expected['html_sha256'],'a6_html_sha256_mismatch')
    text=raw.decode('utf-8')
    require('name="PubDate" content="2026-05-05 09:18:04.0"'in text,'a6_pubdate_metadata')
    require('name="lastmodifiedtime" content="2026-05-11 09:23"'in text,'a6_modified_metadata')
    require('id="pubDate">2026-05-05<'in text,'a6_visible_date')
    return True

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--review-package',type=Path,help='Optional private ZIP; read locally without extraction or content logging.')
    parser.add_argument('--a6-html',type=Path,help='Optional frozen same-byte A6 HTML for metadata reproduction.')
    args=parser.parse_args()
    try:
        directory,summary,quotes,mapping,integrity,dates=check_public(args.root)
        private_ok=check_private_package(args.review_package,quotes,mapping,integrity)if args.review_package else False
        html_ok=check_html(args.a6_html,dates)if args.a6_html else False
        print(json.dumps({'status':'PASS','public_receipt_consistency':True,'questions':30,'quote_checks':58,'mapping_checks':9,'comment_resolutions':14,'private_package_reproduced':private_ok,'html_metadata_reproduced':html_ok,'human_experiment_acceptance_claimed':False,'owner_opinion_adopted':True},ensure_ascii=False))
        return 0
    except(ValueError,KeyError,OSError,zipfile.BadZipFile)as error:
        print(json.dumps({'status':'FAIL','reason':str(error)},ensure_ascii=False),file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
