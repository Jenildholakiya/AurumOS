# # -*- coding: utf-8 -*-
# """
# AurumOS Unlock Key Generator — Developer Tool
# Run on YOUR PC only. Never share this file with clients.
#
# Generates two types of keys:
#   1. Regular unlock key (12 chars) — clears wrong-password lock
#   2. BASTION admin key  (16 chars) — clears BASTION suspension
# """
# import hashlib, datetime
#
# REGULAR_SALT = 'AurumOS@Jewel#2024$Prof'
# BASTION_SALT = 'BASTION@AurumOS#Jenil$2024!Admin'
#
# LINE  = "-" * 50
# DLINE = "=" * 50
#
# def make_regular_key(lock_code, date_str):
#     lc = str(lock_code).strip().upper()[:8]
#     return hashlib.sha256(
#         (lc + REGULAR_SALT + date_str).encode('utf-8')
#     ).hexdigest()[:12].upper()
#
# def make_bastion_key(lock_code, date_str):
#     lc = str(lock_code).strip().upper()[:8]
#     return hashlib.sha256(
#         (lc + BASTION_SALT + date_str).encode('utf-8')
#     ).hexdigest()[:16].upper()
#
# def get_dates():
#     today     = datetime.date.today()
#     yesterday = today - datetime.timedelta(days=1)
#     tomorrow  = today + datetime.timedelta(days=1)
#     return yesterday, today, tomorrow
#
# def print_keys(lock_code, mode='both'):
#     yesterday, today, tomorrow = get_dates()
#     lc = lock_code.strip().upper()[:8]
#
#     if mode in ('regular', 'both'):
#         print(f"\n  REGULAR UNLOCK KEY (12 chars — for wrong-password lock)")
#         print(LINE)
#         for label, d in [("YESTERDAY", yesterday), ("TODAY    ", today), ("TOMORROW ", tomorrow)]:
#             key = make_regular_key(lc, d.strftime('%Y-%m-%d'))
#             print(f"  {label} ({d})  ->  {key}")
#
#     if mode in ('bastion', 'both'):
#         print(f"\n  BASTION ADMIN KEY (16 chars — for BASTION suspension)")
#         print(LINE)
#         for label, d in [("YESTERDAY", yesterday), ("TODAY    ", today), ("TOMORROW ", tomorrow)]:
#             key = make_bastion_key(lc, d.strftime('%Y-%m-%d'))
#             print(f"  {label} ({d})  ->  {key}")
#
# print(DLINE)
# print("  AurumOS Unlock Key Generator")
# print("  Developer tool — keep private")
# print(DLINE)
#
# print("\n  Mode:")
# print("  1. Regular unlock key   (wrong password lock — 12 chars)")
# print("  2. BASTION admin key    (security suspension  — 16 chars)")
# print("  3. Both")
# print()
#
# mode_input = input("  Select mode (1/2/3): ").strip()
# mode_map   = {'1': 'regular', '2': 'bastion', '3': 'both'}
# mode       = mode_map.get(mode_input, 'both')
#
# print()
# lock_code  = input("  Client lock code (8 chars from screen): ").strip().upper()[:8]
#
# if not lock_code:
#     print("No lock code entered. Exiting.")
#     input()
#     exit()
#
# print_keys(lock_code, mode)
#
# print()
# print(DLINE)
# print("  Send TODAY key to client via WhatsApp.")
# print("  If they enter it tomorrow, send TOMORROW key.")
# print("  Regular key = 12 chars, BASTION key = 16 chars.")
# print(DLINE)
# print()
# input("Press Enter to exit...")