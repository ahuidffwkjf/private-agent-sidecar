from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent
a = Analysis(
    [str(root / 'desktop/backend_entry.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'agent_sidecar/web'), 'agent_sidecar/web')],
    hiddenimports=collect_submodules('agent_sidecar'),
    hookspath=[], runtime_hooks=[], excludes=['sidecar_fl', 'torch', 'transformers', 'peft'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='SidecarBackend',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='SidecarBackend')
