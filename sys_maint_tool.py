import hashlib
from datetime import date


def generate_access_code(machine_code):
    # USE THE SAME SECRET SALT YOU PUT IN main.py
    SECRET_SALT = "YOUR_SUPER_SECRET_SALT"

    # Get current date to ensure keys expire daily
    today = date.today().isoformat()

    # Generate the unique hash
    raw_input = f"{machine_code}{SECRET_SALT}{today}"
    full_hash = hashlib.sha256(raw_input.encode()).hexdigest()

    # Return the first 12 characters as the key
    return full_hash[:12].upper()


if __name__ == "__main__":
    print("--- System Maintenance Utility ---")
    code = input("Enter Client Lock Code: ").strip().upper()
    if code:
        print(f"Generated Key: {generate_access_code(code)}")
    else:
        print("Error: No code provided.")