#!/usr/bin/env python3
"""Read-only version checks for locally held method-B and r11 inputs.

Does not release candidates, print answers, execute models, or claim a read-through.
Exit 0: all three supplied inputs match; 2: mismatched/unreadable input;
3: one or more inputs not supplied. A hash mismatch can indicate another version.
"""
import argparse,hashlib,json,sys,zipfile
from pathlib import Path
from xml.etree import ElementTree

MANIFEST='evidence/chengqiancheng-closeout-20261112/expected-private-inputs.json'

def check(path,expected):
    if path is None:return {'status':'NOT_SUPPLIED','same_version':False}
    try:
        raw=path.read_bytes()
    except OSError:return {'status':'UNREADABLE','same_version':False}
    if hashlib.sha256(raw).hexdigest()!=expected['sha256']:
        return {'status':'VERSION_MISMATCH','same_version':False}
    try:
        if expected['type']=='method_b':
            document=json.loads(raw)
            counts={unit:len(value['candidates'])for unit,value in document['units'].items()}
            if counts!=expected['unit_counts']:raise ValueError()
            if not all(item.get('quote_in_source')is True for value in document['units'].values()for item in value['candidates']):raise ValueError()
        else:
            with zipfile.ZipFile(path)as archive:
                if archive.testzip()is not None:raise ValueError()
                ElementTree.fromstring(archive.read('word/document.xml'))
    except (ValueError,KeyError,TypeError,AttributeError,zipfile.BadZipFile,ElementTree.ParseError,OSError):
        return {'status':'STRUCTURE_MISMATCH','same_version':True}
    return {'status':'MATCHED','same_version':True}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--method-b',type=Path,help='Local original candidate JSON; hash checked before contents are parsed.')
    parser.add_argument('--r11-body',type=Path,help='Local r11 body Word.')
    parser.add_argument('--r11-appendix',type=Path,help='Local r11 appendix Word.')
    args=parser.parse_args()
    try:
        manifest=json.loads((args.root/MANIFEST).read_text())
        if len(manifest['files'])!=3 or len({item['id']for item in manifest['files']})!=3:raise ValueError()
        results={item['id']:check(getattr(args,item['argument']),item)for item in manifest['files']}
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        print(json.dumps({'status':'INVALID_MANIFEST'}));return 2
    states={value['status']for value in results.values()}
    failed=bool(states-{'MATCHED','NOT_SUPPLIED'})
    complete=states=={'MATCHED'}
    print(json.dumps({'status':'INPUT_ERROR'if failed else('ALL_VERSION_CHECKS_MATCHED'if complete else'AWAITING_PRIVATE_INPUTS'),
                      'files':results,'candidate_release_authorized':False,'method_b_processing_completed':False,
                      'r11_readthrough_completed':False,'official_submission_completed':False},ensure_ascii=False))
    return 2 if failed else(0 if complete else 3)

if __name__=='__main__':sys.exit(main())
