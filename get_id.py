import uuid
import hashlib

def get_my_fingerprint():
    # Gets the unique hardware ID (MAC) and hashes it
    node = uuid.getnode()
    fingerprint = hashlib.sha256(str(node).encode()).hexdigest()[:24]
    return fingerprint

if __name__ == "__main__":
    print("------------------------------------------")
    print(f"YOUR MACHINE FINGERPRINT: {get_my_fingerprint()}")
    print("------------------------------------------")
    input("Press Enter to close...")