import os, sys, glob as _glob

def _boot_clr():
    try:
        import ctypes
    except Exception:
        return
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    base = getattr(sys, "_MEIPASS", exe_dir)

    roots = []
    for d in (exe_dir, base, os.path.join(base, "_internal")):
        if d and d not in roots:
            roots.append(d)

    # 1) preload embedded python DLL + pin PYDLL
    for d in roots:
        for name in ("python3.dll", "python312.dll"):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                try: ctypes.WinDLL(p)
                except Exception: pass
                os.environ.setdefault("PYTHONNET_PYDLL", p)

    # 2) locate pythonnet/runtime (Python.Runtime.dll lives here)
    rt = None
    for d in roots:
        cand = os.path.join(d, "pythonnet", "runtime")
        if os.path.isfile(os.path.join(cand, "Python.Runtime.dll")):
            rt = cand
            break
    if rt is None:
        try:
            import pythonnet as _pn
            cand = os.path.join(os.path.dirname(_pn.__file__), "runtime")
            if os.path.isfile(os.path.join(cand, "Python.Runtime.dll")):
                rt = cand
        except Exception:
            pass

    # 3) put bundle root + runtime dir on DLL search path
    for d in [r for r in (rt, base, exe_dir) if r and os.path.isdir(r)]:
        try: os.add_dll_directory(d)
        except Exception: pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

    # 4a) .NET Framework 4.x dir (netfx backend needs System.Windows.Forms)
    net_fw_dirs = []
    for arch in ("Framework64", "Framework"):
        candidate = os.path.join(
            os.environ.get("WINDIR", r"C:\\Windows"),
            "Microsoft.NET", arch, "v4.0.30319",
        )
        if os.path.isdir(candidate) and candidate not in net_fw_dirs:
            net_fw_dirs.append(candidate)
    for d in net_fw_dirs:
        try: os.add_dll_directory(d)
        except Exception: pass
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

    # 4b) .NET Core / .NET 5+ (coreclr backend needs hostfxr)
    dotnet_root = os.environ.get("DOTNET_ROOT")
    if not dotnet_root:
        for candidate in (
            os.path.join(os.environ.get("ProgramFiles", ""), "dotnet"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "dotnet"),
            r"C:\Program Files\dotnet",
            r"C:\Program Files (x86)\dotnet",
        ):
            if os.path.isdir(candidate):
                dotnet_root = candidate
                break
    if dotnet_root:
        os.environ.setdefault("DOTNET_ROOT", dotnet_root)
        fxr_root = os.path.join(dotnet_root, "host", "fxr")
        if os.path.isdir(fxr_root):
            versions = sorted(
                [v for v in os.listdir(fxr_root) if os.path.isdir(os.path.join(fxr_root, v))],
                reverse=True,
            )
            if versions:
                fxr_dir = os.path.join(fxr_root, versions[0])
                try: os.add_dll_directory(fxr_dir)
                except Exception: pass
                os.environ["PATH"] = fxr_dir + os.pathsep + os.environ.get("PATH", "")
        shared_root = os.path.join(dotnet_root, "shared")
        if os.path.isdir(shared_root):
            for fw_name in ("Microsoft.NETCore.App",):
                fw_dir = os.path.join(shared_root, fw_name)
                if os.path.isdir(fw_dir):
                    versions = sorted(
                        [v for v in os.listdir(fw_dir) if os.path.isdir(os.path.join(fw_dir, v))],
                        reverse=True,
                    )
                    if versions:
                        rt_dir = os.path.join(fw_dir, versions[0])
                        try: os.add_dll_directory(rt_dir)
                        except Exception: pass
                        os.environ["PATH"] = rt_dir + os.pathsep + os.environ.get("PATH", "")

    # 5) initialize the CLR - try netfx FIRST (WinForms needs .NET Framework),
    #    only fall back to coreclr. NEVER set PYTHONNET_RUNTIME=coreclr up front
    #    because that forces coreclr which can't load System.Windows.Forms.
    clr_ok = False
    try:
        import pythonnet
        os.environ.pop("PYTHONNET_RUNTIME", None)
        for runtime in ("netfx", "coreclr"):
            try:
                pythonnet.load(runtime)
                os.environ["PYTHONNET_RUNTIME"] = runtime
                clr_ok = True
                return
            except Exception:
                continue
    except Exception:
        pass

    # 6) no .NET runtime found - set flag for friendly error in main.py
    if not clr_ok:
        os.environ["AURUM_CLR_FAILED"] = "1"

_boot_clr()
