# -*- coding: utf-8 -*-
import hmac
import hashlib
import json

class ConstellationHandshake:
    def __init__(self, db_manager):
        self.db = db_manager

    def verify_client_credentials(self, username, password, machine_id, role='staff'):
        """
        Validates connection parameters.
        Enforces web-dashboard seat boundaries before authorizing access.
        """
        try:
            # 1. Verify User Credentials against core SQLite credentials store
            auth = self.db.authenticate_user_by_password(password)
            if not auth.get("authenticated") or auth.get("username", "").lower() != username.lower():
                return {"status": "denied", "message": "Authentication failed: Invalid credentials profile."}

            # 2. SEAT ENFORCEMENT PRE-VALIDATION MATRIX (Future Vercel/Local Sync Hook)
            with self.db._get_connection() as conn:
                # Query maximum slots allowed under active subscription profile settings
                max_seats_row = conn.execute("SELECT value FROM app_config WHERE key='max_network_seats'").fetchone()
                max_seats = int(max_seats_row['value']) if max_seats_row else 3 # Default local framework ceiling

                # Query current registered network machine profiles
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS network_nodes ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "machine_id TEXT UNIQUE, "
                    "username TEXT, "
                    "role TEXT, "
                    "last_seen TEXT DEFAULT (datetime('now')))"
                )
                conn.commit()

                active_nodes = conn.execute("SELECT COUNT(*) as cnt FROM network_nodes WHERE machine_id != ?", (machine_id,)).fetchone()
                current_active_count = active_nodes['cnt'] if active_nodes else 0

                if current_active_count >= max_seats:
                    return {"status": "denied", "message": f"Access Slot Limit Exceeded: Terminal slot capacity ceiling ({max_seats}) hit."}

                # 3. Register/Update node heartbeat footprints
                conn.execute(
                    "INSERT OR REPLACE INTO network_nodes (machine_id, username, role, last_seen) "
                    "VALUES (?, ?, ?, datetime('now'))", (machine_id, username, role)
                )
                conn.commit()

            # 4. Generate absolute session encryption verification token
            session_secret = b"AurumOS-Constellation-Token-Salt"
            token = hmac.new(session_secret, f"{machine_id}-{username}".encode('utf-8'), hashlib.sha256).hexdigest()

            return {
                "status": "authorized",
                "session_token": token,
                "role": role,
                "message": "Terminal connection securely locked into constellation matrix."
            }

        except Exception as e:
            return {"status": "error", "message": f"Handshake generation breakdown: {str(e)}"}