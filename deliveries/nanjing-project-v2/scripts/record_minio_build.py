"""Record source image identity without claiming the removed upstream binary."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    images = json.load(sys.stdin)
    image = images[0]
    labels = image.get('Config', {}).get('Labels', {})
    commit = '7a311a3b666a0e5476fbde24d4413220876d9df2'
    if (labels.get('org.opencontainers.image.revision') != commit
            or labels.get('io.ict.minio.client.revision') != '8da737f8fd63de58dc475bf88eb9e285d206e3ae'):
        raise ValueError('MinIO source image revision label mismatch')
    record = {'captured_at': datetime.now(timezone.utc).isoformat(),
              'image': 'ict-minio:RELEASE.2023-12-20T01-00-02Z-source',
              'image_id': image['Id'], 'server_commit': commit,
              'server_release': 'RELEASE.2023-12-20T01-00-02Z',
              'client_commit': '8da737f8fd63de58dc475bf88eb9e285d206e3ae',
              'client_release': 'RELEASE.2023-12-14T00-37-41Z',
              'scope': 'unmodified pinned official source; locally built image, not the original vendor image'}
    root = Path(__file__).resolve().parents[1]
    (root / 'evidence/minio-source-build.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print('Pinned MinIO server/client source build recorded.')


if __name__ == '__main__':
    main()
