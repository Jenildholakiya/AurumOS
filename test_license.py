"""
AurumOS License Key Tester
───────────────────────────
Run this to test if your license key and server URL are working.

Usage:
    python test_license.py AU-H9LF-GDEB-ULP6-TA3L
"""
import sys
import json
import uuid
import urllib.request
import urllib.error

# ← UPDATE THIS to your actual Vercel URL
CHECK_URL = "https://aurum-os-admin.vercel.app/api/check"

def test_key(key):
    machine_id = str(uuid.getnode())
    key = key.strip().upper()

    print(f"\n{'═'*50}")
    print(f"  AurumOS License Key Tester")
    print(f"{'═'*50}")
    print(f"  Key:        {key}")
    print(f"  Machine ID: {machine_id[:8]}...")
    print(f"  Server:     {CHECK_URL}")
    print(f"{'─'*50}")

    try:
        payload = json.dumps({"key": key, "machine_id": machine_id}).encode()
        req = urllib.request.Request(
            CHECK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        print(f"  ✅ Server response received")
        print(f"  Valid:    {data.get('valid')}")
        print(f"  Status:   {data.get('status')}")
        if data.get('business'):
            print(f"  Business: {data.get('business')}")
        if data.get('owner'):
            print(f"  Owner:    {data.get('owner')}")
        print(f"{'═'*50}\n")

    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()
        except: pass
        print(f"  ❌ HTTP Error {e.code}: {body}")
        print(f"{'═'*50}\n")

    except urllib.error.URLError as e:
        print(f"  ❌ Cannot reach server: {e.reason}")
        print(f"  Check: is the Vercel URL correct? Is internet connected?")
        print(f"{'═'*50}\n")

if __name__ == "__main__":
    key = sys.argv[1] if len(sys.argv) > 1 else input("Enter license key: ").strip()
    test_key(key)