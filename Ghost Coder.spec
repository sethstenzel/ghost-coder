# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['src\\ghost_coder\\app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['amqtt.plugins.logging_amqtt', 'amqtt.plugins.logging', 'amqtt.plugins.authentication'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Ghost Coder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['c:\\s3711\\git\\ghost-coder\\.imgs\\gc_icon_512x512.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Ghost Coder',
)
