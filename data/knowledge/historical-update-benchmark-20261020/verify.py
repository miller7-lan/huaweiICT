"""Offline integrity verification; Python standard library only."""
import hashlib
import json
import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, text):
        self.parts.append(text)


def compact(text):
    return re.sub(r'\s+', '', text)


def verify():
    root = Path(__file__).resolve().parent
    inventory = json.loads((root / 'SHA256SUMS.json').read_text('utf-8'))
    listed = {row['path'] for row in inventory}
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()
              and p.name != 'SHA256SUMS.json'}
    assert listed == actual, 'File inventory mismatch'
    for row in inventory:
        path = root / row['path']
        assert path.resolve().is_relative_to(root), 'Unsafe inventory path'
        data = path.read_bytes()
        assert len(data) == row['bytes'] and digest(data) == row['sha256'], row['path']
    manifest = json.loads((root / 'manifest.json').read_text('utf-8'))
    assert manifest['source_count'] == 7 and manifest['actual_model_runs'] == 0
    total = 0
    for source in manifest['sources']:
        folder = root / source['folder']
        raw = (folder / 'original.html').read_bytes()
        assert digest(raw) == source['original_sha256']
        paragraphs = json.loads((folder / 'paragraphs.json').read_text('utf-8'))
        assert len(paragraphs) == source['paragraph_count']
        parser = TextOnly()
        parser.feed(raw.decode('utf-8'))
        original_text = compact(''.join(parser.parts))
        txt = (folder / 'import.txt').read_text('utf-8')
        with sqlite3.connect((folder / 'knowledge.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
            assert db.execute('pragma integrity_check').fetchone()[0] == 'ok'
            assert db.execute('pragma foreign_key_check').fetchall() == []
            sources = db.execute('select source_id,original_sha256 from sources').fetchall()
            assert sources == [(source['source_id'],source['original_sha256'])]
            rows = db.execute('select locator,category,text,text_sha256 from paragraphs order by ordinal').fetchall()
            assert len(rows) == len(paragraphs)
            for row, p in zip(rows, paragraphs):
                assert tuple(row[:3]) == (p['locator'],p['category'],p['text'])
                assert row[3] == digest(p['text'].encode('utf-8'))
                assert compact(p['text']) in original_text, p['locator']
                assert p['text'] in txt
        total += len(paragraphs)
    print(json.dumps({'status':'passed','files_checked':len(inventory),'databases':7,
                      'paragraphs':total,'actual_model_runs':0},ensure_ascii=False))


if __name__ == '__main__':
    verify()
