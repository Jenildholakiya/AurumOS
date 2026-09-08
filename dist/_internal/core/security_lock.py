import subprocess
import hashlib


def get_hwid():
    """Generates a unique Hardware ID based on the system's Motherboard Serial."""
    try:
        # Command to get Motherboard Serial Number on Windows
        cmd = 'wmic baseboard get serialnumber'
        serial = subprocess.check_output(cmd, shell=True).decode().split('\n')[1].strip()

        # Hash the serial for security so the raw ID isn't exposed
        hwid = hashlib.sha256(serial.encode()).hexdigest().upper()
        return hwid
    except Exception:
        # Fallback for systems where WMIC is restricted
        return "DEFAULT-AURUM-OS-ID"