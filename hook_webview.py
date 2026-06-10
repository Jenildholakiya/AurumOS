from PyInstaller.utils.hooks import collect_all, collect_submodules
datas, binaries, hiddenimports = collect_all("webview")
hiddenimports += collect_submodules("webview")
