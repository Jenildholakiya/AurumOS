import hashlib
import secrets
import json
import os
from datetime import datetime


LOCKED_SCREEN_CODE_FILE = "locked_screen_code.json"
KEYS_FILE = "unlock_keys.json"
USED_KEYS_FILE = "used_keys.json"


def generate_locked_screen_code():
    code = secrets.token_hex(8)
    data = {
        "code": code,
        "created_at": datetime.now().isoformat(),
        "used": False
    }
    with open(LOCKED_SCREEN_CODE_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[LOCKED SCREEN CODE] Enter this code on the locked screen: {code}")
    return code


def verify_locked_screen_code(code_to_verify):
    if not os.path.exists(LOCKED_SCREEN_CODE_FILE):
        print("[ERROR] Locked screen code file not found.")
        return False

    with open(LOCKED_SCREEN_CODE_FILE, "r") as f:
        data = json.load(f)

    if data["used"]:
        print("[ERROR] This locked screen code has already been used.")
        return False

    if data["code"] == code_to_verify:
        data["used"] = True
        with open(LOCKED_SCREEN_CODE_FILE, "w") as f:
            json.dump(data, f, indent=2)
        print("[SUCCESS] Locked screen code verified.")
        return True
    else:
        print("[ERROR] Invalid locked screen code.")
        return False


def generate_unlock_key():
    key = f"aurum-{secrets.token_hex(16)}"
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    data = {
        "key": key,
        "key_hash": key_hash,
        "purpose": "suspension_and_login_unlock",
        "created_at": datetime.now().isoformat(),
        "single_use": True
    }

    if os.path.exists(KEYS_FILE):
        with open(KEYS_FILE, "r") as f:
            keys_data = json.load(f)
    else:
        keys_data = {"keys": []}

    keys_data["keys"].append(data)

    with open(KEYS_FILE, "w") as f:
        json.dump(keys_data, f, indent=2)

    print(f"\n{'='*50}")
    print(f"[UNLOCK KEY] (One-Time Use Only)")
    print(f"{'='*50}")
    print(f"KEY: {key}")
    print(f"Purpose: Unlock Suspension + Login Page")
    print(f"Created: {data['created_at']}")
    print(f"{'='*50}")
    print("NOTE: This key can only be used ONCE!")
    return key


def use_key(key_to_use):
    if not os.path.exists(KEYS_FILE):
        print("[ERROR] Keys file not found.")
        return False

    if os.path.exists(USED_KEYS_FILE):
        with open(USED_KEYS_FILE, "r") as f:
            used_data = json.load(f)
    else:
        used_data = {"used_keys": []}

    with open(KEYS_FILE, "r") as f:
        keys_data = json.load(f)

    for entry in keys_data["keys"]:
        if entry["key"] == key_to_use:
            if key_to_use in used_data["used_keys"]:
                print("[ERROR] This key has already been used!")
                return False

            used_data["used_keys"].append(key_to_use)
            with open(USED_KEYS_FILE, "w") as f:
                json.dump(used_data, f, indent=2)

            keys_data["keys"] = [k for k in keys_data["keys"] if k["key"] != key_to_use]
            with open(KEYS_FILE, "w") as f:
                json.dump(keys_data, f, indent=2)

            print("\n[SUCCESS] Key accepted!")
            print("[UNLOCKING] Suspension: UNLOCKED")
            print("[UNLOCKING] Login Page: UNLOCKED")
            print("[INFO] Key has been consumed and cannot be reused.")
            return True

    print("[ERROR] Invalid key.")
    return False


def main():
    while True:
        print("\n" + "="*50)
        print("  AURUM OS - SUSPENSION & LOGIN UNLOCK")
        print("="*50)
        print("1. Generate Locked Screen Code")
        print("2. Generate Unlock Key (after code verification)")
        print("3. Use Key to Unlock")
        print("4. Exit")
        print("="*50)

        choice = input("Select option: ").strip()

        if choice == "1":
            print("\n--- Generating Locked Screen Code ---")
            generate_locked_screen_code()

        elif choice == "2":
            print("\n--- Verify Locked Screen Code ---")
            code = input("Enter locked screen code: ").strip()
            if verify_locked_screen_code(code):
                print("\n--- Generating Unlock Key ---")
                generate_unlock_key()
            else:
                print("[FAILED] Cannot generate key without valid code.")

        elif choice == "3":
            print("\n--- Use Key to Unlock ---")
            key = input("Enter unlock key: ").strip()
            use_key(key)

        elif choice == "4":
            print("Exiting...")
            break

        else:
            print("Invalid option. Try again.")


if __name__ == "__main__":
    main()
