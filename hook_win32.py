from PyInstaller.utils.hooks import collect_submodules
hiddenimports = collect_submodules("win32") + ["pywintypes","win32con","win32api","win32print","win32gui"]
