from PyInstaller.utils.hooks import collect_submodules
hiddenimports = (
    collect_submodules("database") +
    collect_submodules("core") +
    collect_submodules("network")
)
