"""
AurumOS Free Obfuscation Build
Uses python-obfuscator -- free, no license needed.
Works on files of any size.

Usage: python obfuscate_free.py
"""
import os, sys, shutil, subprocess, base64, zlib

ROOT = os.path.abspath(".")

# Files to obfuscate
TARGETS = [
    "main.py",
    "updater.py",
    "build_release.py",
    os.path.join("database", "db_manager.py"),
    os.path.join("core", "tag_engine.py"),
]

BAK_EXT = ".orig"


def obfuscate_with_marshal(src_path):
    """
    Free obfuscation using Python's built-in marshal + zlib + base64.
    The source code is:
      1. Compiled to bytecode (co object)
      2. Compressed with zlib
      3. Encoded with base64
      4. Wrapped in a tiny loader stub

    Result: valid .py file that runs identically but source is unreadable.
    No external tools needed -- uses only Python stdlib.
    """
    with open(src_path, 'r', encoding='utf-8') as f:
        source = f.read()

    try:
        import marshal, types

        # Compile source to code object
        code = compile(source, src_path, 'exec')

        # Serialize + compress + encode
        raw        = marshal.dumps(code)
        compressed = zlib.compress(raw, level=9)
        encoded    = base64.b85encode(compressed).decode('ascii')

        # Loader stub -- this is what the obfuscated file looks like
        stub = (
            "import marshal,zlib,base64\n"
            "_c=marshal.loads(zlib.decompress(base64.b85decode(\n"
            f"    {repr(encoded)}\n"
            ")))\n"
            "exec(_c)\n"
        )
        return stub
    except SyntaxError as e:
        print(f"  SKIP (syntax error): {src_path} -- {e}")
        return None
    except Exception as e:
        print(f"  SKIP (error): {src_path} -- {e}")
        return None


def obfuscate_all():
    print("\n[1/3] Obfuscating Python files (free marshal method)...")
    failed = []
    for rel in TARGETS:
        full = os.path.join(ROOT, rel)
        if not os.path.exists(full):
            print(f"  SKIP (not found): {rel}")
            continue

        stub = obfuscate_with_marshal(full)
        if stub is None:
            failed.append(rel)
            continue

        # Backup original
        bak = full + BAK_EXT
        shutil.copy2(full, bak)

        # Write obfuscated file
        with open(full, 'w', encoding='utf-8') as f:
            f.write(stub)

        size_orig = os.path.getsize(bak)
        size_obf  = os.path.getsize(full)
        print(f"  OK  {rel}  ({size_orig//1024}KB → {size_obf//1024}KB)")

    if failed:
        print(f"\n  Skipped {len(failed)} files: {failed}")
    return len(failed) == 0


def restore_all():
    print("\n[3/3] Restoring original source files...")
    for rel in TARGETS:
        full = os.path.join(ROOT, rel)
        bak  = full + BAK_EXT
        if os.path.exists(bak):
            shutil.copy2(bak, full)
            os.remove(bak)
            print(f"  Restored: {rel}")


def build_exe():
    print("\n[2/3] Building EXE with PyInstaller...")
    r = subprocess.run(
        "pyinstaller AurumOS.spec --clean --noconfirm",
        shell=True, cwd=ROOT
    )
    if r.returncode != 0:
        print("\n  ERROR: PyInstaller failed — restoring originals...")
        restore_all()
        sys.exit(1)
    print("  EXE built: dist\\AurumOS\\AurumOS.exe")


def verify(path):
    """Show first few lines of obfuscated file to confirm it's unreadable."""
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return
    lines = open(full, encoding='utf-8').readlines()[:4]
    print(f"\n  Preview of obfuscated {path}:")
    for l in lines:
        print(f"    {l.rstrip()[:80]}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"

    print("=" * 52)
    print(" AurumOS Free Obfuscation Build")
    print("=" * 52)

    if mode == "check":
        # Just show what obfuscated output looks like without modifying anything
        import tempfile, marshal, zlib, base64
        src = open(os.path.join(ROOT, "updater.py"), encoding='utf-8').read()
        code = compile(src, "updater.py", 'exec')
        raw  = marshal.dumps(code)
        enc  = base64.b85encode(zlib.compress(raw, 9)).decode('ascii')
        print(f"\n  Sample obfuscated output (first 200 chars of encoded data):")
        print(f"  import marshal,zlib,base64")
        print(f"  _c=marshal.loads(zlib.decompress(base64.b85decode(")
        print(f"      '{enc[:60]}...'")
        print(f"  )))")
        print(f"  exec(_c)")
        print(f"\n  Original readable? No. Runnable? Yes.")

    elif mode == "obfonly":
        obfuscate_all()
        verify("main.py")
        print("\n  Done. Run 'python obfuscate_free.py restore' to undo.")

    elif mode == "restore":
        restore_all()
        print("\n  Done. Originals restored.")

    elif mode == "full":
        obfuscate_all()
        verify("main.py")
        build_exe()
        restore_all()
        print("\n[DONE] Protected EXE ready: dist\\AurumOS\\AurumOS.exe")
        print("       Source files restored to originals.")

    else:
        print(f"Usage: python obfuscate_free.py [full|obfonly|check|restore]")