# PyInstaller spec: bundle only redistributable runtime and built-in generated models.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

datas = collect_data_files('rf_link_calculator') + collect_data_files('webview')
for name in ('numpy', 'scipy', 'pandas', 'scikit-rf', 'openpyxl', 'jsonschema', 'fastapi', 'argon2-cffi', 'pywebview'):
    datas += copy_metadata(name)
datas += [('LICENSE', '.'), ('build/THIRD-PARTY-NOTICES', 'THIRD-PARTY-NOTICES')]
a = Analysis(['desktop.py'], pathex=['src'], binaries=[], datas=datas,
             hiddenimports=collect_submodules('uvicorn') + collect_submodules('webview.platforms') + ['clr', 'pythonnet', 'anyio._backends._asyncio'],
             hookspath=[], runtime_hooks=[],
             excludes=['streamlit', 'pytest', 'IPython', 'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'webview.platforms.gtk', 'webview.platforms.cocoa', 'webview.platforms.qt'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='RF-Link', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='RF-Link')
