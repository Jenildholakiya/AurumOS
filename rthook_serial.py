import sys, os

# Force UTF-8 stdout so print() never crashes with charmap error
os.environ.setdefault("PYTHONIOENCODING", "utf-8:replace")
try:
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

# Pre-load serial for COM port detection
try:
    import serial
    import serial.tools.list_ports
    import serial.serialutil
    import serial.win32
except Exception:
    pass
