#!/usr/bin/env python3
"""Build a deterministic, explicitly allowlisted V2 shadow distribution."""
import argparse
from hashlib import sha256
import io
import json
from pathlib import Path
import tarfile

from daily_v2 import VERSION

ROOT = Path(__file__).resolve().parents[1]


def build(destination):
    names = [
        'scripts/run_ai_daily_v2.py', 'scripts/package_ai_daily_v2.py', 'scripts/qa_v2_preview.cjs',
        'scripts/newspaper_text.py', 'scripts/email_feedback.py', 'scripts/editorial_backlog.py',
        'scripts/ai_daily_common.py', 'scripts/ai_gateway_client.py',
        'config/newspaper_presentation.json', 'config/newspaper_accent_palette.json',
        'templates/newspaper_text.html', 'requirements.txt', 'tests/test_daily_v2.py',
        'docs/V2_SHADOW_RELEASE.md', 'docs/AI_DAILY_V2_REQUIREMENTS.md',
    ]
    names += [str(p.relative_to(ROOT)) for p in (ROOT / 'scripts/daily_v2').glob('*.py')]
    names += [str(p.relative_to(ROOT)) for p in (ROOT / 'feedback_service').glob('*.py')]
    files = {name: (ROOT / name).read_bytes() for name in sorted(names)}
    manifest = {'version': VERSION, 'mode': 'shadow', 'has_smtp_transport': False,
                'files': {name: sha256(raw).hexdigest() for name, raw in files.items()}}
    files['release-package.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Uncompressed tar has stable bytes without gzip timestamp differences.
    with tarfile.open(destination, 'w') as archive:
        for name, raw in sorted(files.items()):
            item = tarfile.TarInfo('ai-daily-v2/' + name)
            item.size, item.mtime, item.mode = len(raw), 0, 0o644
            archive.addfile(item, io.BytesIO(raw))
    checksum = sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(destination.suffix + '.sha256').write_text(checksum + '  ' + destination.name + '\n')
    return {'package': str(destination), 'sha256': checksum, 'files': len(files), 'mode': 'shadow'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('output/v2-shadow/ai-daily-v2-' + VERSION + '.tar'))
    print(json.dumps(build(parser.parse_args().out), ensure_ascii=False, indent=2))
