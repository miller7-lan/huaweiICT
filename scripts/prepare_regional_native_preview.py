"""Pin already acquired public candidates; no HTTP, inference or publication."""
from copy import deepcopy
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from gov_mcp.knowledge import KnowledgeStore
from gov_mcp.regional_preview import CANDIDATES, PAUSES, SCENARIOS, check_context, read, write, RegionalPreview
from private_runtime import ensure_private_directory

RUNTIME = ROOT / '.runtime/regional-native-preview'


def main():
    if RUNTIME.exists() and any(RUNTIME.iterdir()):
        raise ValueError('Existing experiment preserved; preparation cannot reset its state')
    ensure_private_directory(RUNTIME)
    snapshot = read(CANDIDATES / 'snapshot.json')
    store = KnowledgeStore(CANDIDATES, read_only=True)
    (RUNTIME / 'sources').mkdir()
    for binding in snapshot['bindings']:
        with store.db() as db:
            row, _ = store._verify(db, binding['revision_id'], require_review=False)
        raw = (store.root / 'originals' / row['sha256'][:2] / row['sha256']).read_bytes()
        (RUNTIME / 'sources' / (binding['spec']['source_id'] + '.html')).write_bytes(raw)
    write(RUNTIME / 'manifest.json', {'schema': 1, 'mode': 'regional_public_preview', 'data_class': 'public', 'snapshot': snapshot})
    write(RUNTIME / 'gates.json', {'source_stopped': False, 'paused_fields': deepcopy(PAUSES)})
    check_context(RUNTIME)
    write(RUNTIME / 'access.json', {'mcp_token': secrets.token_urlsafe(48)})
    preview = RegionalPreview(RUNTIME)
    print({'prepared': True, 'scenarios': list(SCENARIOS), 'counts': preview.counts(), 'cloud_calls': 0})


if __name__ == '__main__':
    main()
