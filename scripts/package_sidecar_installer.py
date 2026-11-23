"""Build a source installer plus native communication and onboarding Skills."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sidecar_setup import ROOT, source_files, digest_source
from export_agent_skills import export_skills


def package(output):
    if output.exists():
        raise ValueError('Output exists; choose a fresh output path')
    output.parent.mkdir(parents=True, exist_ok=True)
    source_digest = digest_source(ROOT)
    content = {str(path.relative_to(ROOT)): path.read_bytes() for path in source_files(ROOT)}
    with tempfile.TemporaryDirectory() as temporary:
        skill_root = Path(temporary) / 'agent-skills'
        catalog = export_skills(skill_root)
        for path in skill_root.rglob('*'):
            if path.is_file():
                content['agent-skills/' + str(path.relative_to(skill_root))] = path.read_bytes()
    # The onboarding Skill and its references are already shipped under skills/.
    content['BUNDLE.json'] = json.dumps({
        'schema': 'sidecar-agent-installer/1', 'source_sha256': source_digest,
        'operation_skill_count': len(catalog), 'onboarding_skill_count': 1,
        'platforms': ['macOS', 'Linux'], 'python': '>=3.10', 'tailcat': 'v0.7.0',
        'desktop': {
            'entry': 'desktop/README.md',
            'platform_targets': ['Windows x64', 'macOS arm64', 'macOS x64', 'Linux x64'],
            'source_requirements': 'Node.js >=22.12.0 and Python 3.10-3.14',
            'launch': 'cd desktop; npm ci; npm start',
            'distribution': 'source only; build native binaries per OS using desktop workflow',
        },
        'dependency_mode': 'download at install time; not an offline binary bundle',
        'operation_skills': '23 native Sidecar communication tools; node trust enforced',
        'entry': 'docs/AGENT_INSTALL.md',
    }, ensure_ascii=False, indent=2).encode()
    content['SHA256SUMS'] = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n'
                                 for name, data in sorted(content.items())).encode()
    if digest_source(ROOT) != source_digest:
        raise ValueError('Source changed while packaging; retry after the workspace is stable')
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(content.items()):
            info = zipfile.ZipInfo('sidecar-agent-installer/' + name, (2026, 11, 5, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if name.endswith(('.command', '.sh')) else 0o100644) << 16
            archive.writestr(info, data)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(digest + '  ' + output.name + '\n')
    return {'file': str(output), 'sha256': digest, 'operation_skills': len(catalog), 'onboarding_skills': 1}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.output), ensure_ascii=False))
