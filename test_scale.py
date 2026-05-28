"""
AurumOS Scale Test — RS232C via USB
Tests reading live weight from a jewellery scale.

Usage:
    python scale_test.py
    python scale_test.py COM3        (specify port)
    python scale_test.py COM3 9600   (specify port + baud)

Common scale baud rates: 1200, 2400, 4800, 9600, 19200
Common ports on Windows: COM3, COM4, COM5, COM6
"""

import serial
import serial.tools.list_ports
import sys
import time
import re

# ── CONFIG ────────────────────────────────────────────────────────────────────
DEFAULT_BAUD    = 9600
DEFAULT_TIMEOUT = 2      # seconds to wait for data
READ_INTERVAL   = 0.5    # seconds between readings
MAX_RETRIES     = 3

# Common scale data patterns (covers most jewellery scales):
# Formats: "ST,GS,+  12.345g", "  12.345 g", "GS   12.345", "12.345"
WEIGHT_PATTERNS = [
    r'[+-]?\s*(\d+\.?\d*)\s*g',      # 12.345g or 12.345 g
    r'ST[,\s]+GS[,\s]+[+-]?\s*(\d+\.?\d*)',  # Stable + Gross: ST,GS,+ 12.345
    r'GS\s*[,]?\s*[+-]?\s*(\d+\.?\d*)',      # GS 12.345
    r'[+-]?\s*(\d+\.\d{2,3})\s*$',   # plain decimal at end of line
    r'(\d+\.\d+)',                     # any decimal number (fallback)
]

def list_ports():
    """Show all available COM ports."""
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("  No COM ports found.")
        return []
    for p in ports:
        print(f"  {p.device:8s} — {p.description}")
    return [p.device for p in ports]

def parse_weight(raw_bytes):
    """Extract weight value from raw scale bytes."""
    try:
        text = raw_bytes.decode('ascii', errors='ignore').strip()
    except:
        text = str(raw_bytes)

    if not text:
        return None, None

    for pattern in WEIGHT_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1))
                return val, text
            except:
                continue
    return None, text

def is_stable(text):
    """Check if scale reports stable reading.
    Format: '-0000.02 G S' = Stable, '-0000.02 G U' = Unstable
    """
    import re as _r
    t = (text or '').upper()
    if _r.search(r'G\s+S\b', t): return True   # this scale: G S = stable
    if _r.search(r'G\s+U\b', t): return False  # this scale: G U = moving
    return 'ST,' in t or 'ST ' in t or 'STABLE' in t

def test_scale(port, baud=DEFAULT_BAUD):
    print(f"\n{'='*50}")
    print(f"  Connecting: {port} @ {baud} baud")
    print(f"{'='*50}")

    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=DEFAULT_TIMEOUT,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False
        )
    except serial.SerialException as e:
        print(f"  [ERR] Cannot open {port}: {e}")
        return False

    print(f"  [OK] Port opened. Reading weight...\n")
    print(f"  {'RAW DATA':<35} {'WEIGHT':>10}  STABLE")
    print(f"  {'-'*55}")

    success_count = 0
    read_count    = 0

    try:
        # Some scales need a command to send data — try common ones
        for cmd in [b'\r\n', b'W\r\n', b'P\r\n', b'']:
            if cmd:
                ser.write(cmd)
                time.sleep(0.1)

        for _ in range(20):  # 20 readings
            read_count += 1
            raw = ser.readline()
            if not raw:
                raw = ser.read(32)  # fallback: read raw bytes

            weight, text = parse_weight(raw)
            stable = is_stable(text)
            text_short = (text or repr(raw))[:33]

            if weight is not None:
                success_count += 1
                flag = "[STABLE]" if stable else "[MOVING]"
                print(f"  {text_short:<35} {weight:>8.3f}g  {flag}")
            else:
                raw_repr = repr(raw)[:33]
                print(f"  {raw_repr:<35} {'---':>10}")

            time.sleep(READ_INTERVAL)

    except KeyboardInterrupt:
        print("\n  [STOP] Interrupted by user.")
    except Exception as e:
        print(f"\n  [ERR] Read error: {e}")
    finally:
        ser.close()
        print(f"\n  Results: {success_count}/{read_count} readings parsed successfully.")
        if success_count == 0:
            print("  HINT: Try a different baud rate (1200, 2400, 4800, 9600, 19200)")
        return success_count > 0

def auto_detect(port):
    """Try all common baud rates until one works."""
    baud_rates = [9600, 4800, 2400, 1200, 19200, 38400]
    print(f"\n  Auto-detecting baud rate for {port}...")
    for baud in baud_rates:
        print(f"  Trying {baud}...", end=' ', flush=True)
        try:
            ser = serial.Serial(port, baud, timeout=1)
            time.sleep(0.3)
            data = ser.read(64)
            ser.close()
            if data and len(data) > 2:
                weight, text = parse_weight(data)
                if weight is not None:
                    print(f"GOT WEIGHT! {weight}g  <- use this baud")
                    return baud
                else:
                    print(f"data but no weight ({repr(data[:20])})")
            else:
                print("no data")
        except Exception as e:
            print(f"failed ({e})")
    return None

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("\n" + "="*50)
    print("  AurumOS Scale Test Utility")
    print("="*50)

    # Show available ports
    print("\nAvailable COM ports:")
    ports = list_ports()

    if len(sys.argv) >= 2:
        port = sys.argv[1]
        baud = int(sys.argv[2]) if len(sys.argv) >= 3 else DEFAULT_BAUD
        test_scale(port, baud)
    elif ports:
        # Auto-test the first port found
        port = ports[0]
        print(f"\nNo port specified. Auto-testing: {port}")
        detected_baud = auto_detect(port)
        if detected_baud:
            test_scale(port, detected_baud)
        else:
            print(f"\nAuto-detect failed. Try manually:")
            print(f"  python scale_test.py {port} 9600")
    else:
        print("\nNo COM ports found. Check USB connection.")
        print("Usage: python scale_test.py COM3 9600")