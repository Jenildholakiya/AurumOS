# -*- mode: python ; coding: utf-8 -*-
import os
import glob

project_root    = os.getcwd()
venv_site       = os.path.join(project_root, '.venv', 'Lib', 'site-packages')
win32_dir       = os.path.join(venv_site, 'win32')
win32_lib_dir   = os.path.join(venv_site, 'win32', 'lib')
pywin32_sys_dir = os.path.join(venv_site, 'pywin32_system32')

block_cipher = None

# ✅ All .pyd files from win32 folder → bundled into 'win32' subfolder
win32_binaries = [
    (f, 'win32')
    for f in glob.glob(os.path.join(win32_dir, '*.pyd'))
]

# ✅ pywintypes*.dll + pythoncom*.dll → bundled into root
system_dlls = [
    (f, '.')
    for f in glob.glob(os.path.join(pywin32_sys_dir, '*.dll'))
]

# ✅ win32/lib .py files (win32con.py lives here)
win32_lib_data = [
    (f, os.path.join('win32', 'lib'))
    for f in glob.glob(os.path.join(win32_lib_dir, '*.py'))
]

a = Analysis(
    ['main.py'],
    pathex=[
        project_root,
        venv_site,
        win32_dir,
        win32_lib_dir,
    ],
    binaries=[
        *win32_binaries,
        *system_dlls,
        # ✅ winspool.drv must be explicitly bundled — ctypes.WinDLL needs it frozen
        ('C:\\Windows\\System32\\winspool.drv', '.'),
    ],
    datas=[
        ('ui',       'ui'),
        ('fonts',    'fonts'),
        ('database', 'database'),
        ('core',     'core'),
        *win32_lib_data,
    ],
    hiddenimports=[
        # win32 — only what actually exists on this system
        'win32api',
        'win32con',
        'win32print',
        'win32gui',
        'win32security',
        'win32file',
        'win32process',
        'win32event',
        'win32service',
        'win32net',
        'win32transaction',
        'win32clipboard',
        'win32console',
        'win32cred',
        'win32crypt',
        'win32inet',
        'win32job',
        'win32lz',
        'win32pipe',
        'win32ras',
        'win32ts',
        'pywintypes',
        # ❌ win32ui intentionally removed — not available on Python 3.12
        # ❌ win32com/pythoncom removed — not needed
        # App deps
        'qrcode',
        'qrcode.image.base',
        'qrcode.image.pure',
        'PIL',
        'PIL._imaging',
        'PIL._tkinter_finder',
        'sqlite3',
        'json',
        'webview',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['hook_win32.py'],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AurumOS',
    icon='favicon.ico',  # ✅ embedded app icon
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        'win32api.pyd',
        'win32print.pyd',
        'win32gui.pyd',
        'pywintypes*.dll',
        'pythoncom*.dll',
        'winspool.drv',   # ✅ never compress this
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
