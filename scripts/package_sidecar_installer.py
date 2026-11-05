"""Build a source installer plus 362 independently loadable Skills (no plugin)."""
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
    content = {str(path.relative_to(ROOT)): path.read_bytes() for path in source_files(ROOT)}
    with tempfile.TemporaryDirectory() as temporary:
        skill_root = Path(temporary) / 'agent-skills'
        catalog = export_skills(skill_root)
        for path in skill_root.rglob('*'):
            if path.is_file():
                content['agent-skills/' + str(path.relative_to(skill_root))] = path.read_bytes()
    # The onboarding Skill and its references are already shipped under skills/.
    content['BUNDLE.json'] = json.dumps({
        'schema': 'sidecar-agent-installer/1', 'source_sha256': digest_source(ROOT),
        'operation_skill_count': len(catalog), 'onboarding_skill_count': 1,
        'platforms': ['macOS', 'Linux'], 'python': '>=3.10', 'tailcat': 'v0.7.0',
        'dependency_mode': 'download at install time; not an offline binary bundle',
        'operation_skills': 'catalog guidance; not 361 implemented executors',
        'entry': 'docs/AGENT_INSTALL.md',
    }, ensure_ascii=False, indent=2).encode()
    content['SHA256SUMS'] = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n'
                                 for name, data in sorted(content.items())).encode()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(content.items()):
            info = zipfile.ZipInfo('sidecar-agent-installer/' + name, (2026, 11, 5, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(digest + '  ' + output.name + '\n')
    return {'file': str(output), 'sha256': digest, 'operation_skills': len(catalog), 'onboarding_skills': 1}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.output), ensure_ascii=False))
