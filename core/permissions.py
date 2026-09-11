# -*- coding: utf-8 -*-
"""Who may do what on a staff terminal.

Until now a staff member's role came from the address bar. Changing the role
in the query string handed them the owner's whole sidebar, and every method on
the Python bridge stayed callable from the console no matter what the sidebar
showed. This module is the real boundary.

It lives under core/ deliberately. That directory reaches customers through
the auto-updater, so tightening a rule or naming a newly added method costs a
release tag, not a rebuild and a visit to every shop.

Three layers, checked in this order:

  1. OWNER_ONLY   -- never callable by staff, whatever bundle they hold.
  2. ALWAYS       -- callable by anyone logged in, bundle or not.
  3. BUNDLES      -- the modules an owner assigns to a terminal.
"""

import functools
import inspect
import re
import threading

# -- Seats ---------------------------------------------------------------
# How many PCs may connect to a shop's server, over and above the server
# itself. Extra connections are purchasable, so the real number arrives from
# the licensing server rather than living here; these are only the fallback
# for when the server has not said otherwise.
DEFAULT_SEATS = {
    'lite':       0,
    'pro':        1,
    'enterprise': 4,
}

# A sanity ceiling, so a malformed licence response cannot claim thousands.
MAX_SEATS = 50

# The licensing server sends a plain list of feature names, and that list
# already flows untouched all the way to the client. Riding the seat count in
# it means subscription_manager.py never has to change, which matters because
# that file cannot be updated remotely.
# Any run of digits, capped afterwards. Matching only short numbers would make
# an oversized value fall through to the plan default instead of the ceiling,
# so 51 and 9999 would land on different answers for no good reason.
_SEAT_TOKEN = re.compile(r'^terminals?\s*[=:_-]\s*(\d+)$', re.IGNORECASE)


def licensed_seats(plan, features=None):
    """How many terminals this licence allows, not counting the server PC."""
    for feature in (features or []):
        found = _SEAT_TOKEN.match(str(feature).strip())
        if found:
            return max(0, min(int(found.group(1)), MAX_SEATS))
    return DEFAULT_SEATS.get(str(plan or 'lite').strip().lower(), 0)


# -- Layer 1: never available to staff -----------------------------------
# Anything that changes who can log in, weakens a security control, destroys
# records, or spends money. A staff terminal is refused these at the bridge,
# so a tampered page or a console call gets nowhere.
OWNER_ONLY = frozenset([
    # Staff accounts
    'get_all_staff', 'add_staff', 'delete_staff', 'update_staff_password',
    # Credentials and security controls
    'change_owner_password', 'totp_status', 'totp_begin_enroll',
    'totp_confirm_enroll', 'totp_disable', 'reset_lockout',
    'admin_lockout_reset', 'verify_unlock_key', 'verify_web_unlock_key',
    'bastion_unlock', 'generate_protected_pdf', 'get_machine_id',
    # Configuration
    'save_setup', 'update_settings', 'set_metal_rates', 'set_default_printer',
    'save_network_config', 'change_db_location', 'reset_db_location',
    'ai_save_config',
    # Destructive
    'delete_bill', 'delete_stock_entry', 'delete_new_client',
    'delete_master_entry', 'delete_katti_voucher',
    'tag_audit_delete_session', 'do_year_close', 'switch_year',
    # Licensing and money
    'renew_subscription', 'get_plans', 'verify_key',
    # Diagnostics that expose the whole install
    'get_recent_logs', 'get_log_path', 'open_log_folder', 'get_db_path',
])


# -- Layer 2: available to anyone with a session -------------------------
# Session plumbing, chrome, and the read-only bits every page touches on load.
ALWAYS = frozenset([
    'get_session', 'get_business_name', 'get_settings', 'get_dynamic_greeting',
    'get_current_version', 'get_active_year', 'get_year_list', 'navigate',
    'logout', 'quit_app', 'restart_app', 'js_log', 'get_local_ip',
    'get_metal_rates', 'get_notifications', 'mark_notification_read',
    'mark_all_notifications_read', 'get_trial_status',
    'subscription_get_state', 'subscription_check_feature',
    'get_lockout_status', 'cluster',
])


# -- Layer 3: assignable modules -----------------------------------------
# `pages` drives the sidebar. `methods` is what the bridge will actually run.
# Page lists differ per software type because the two modes bill on different
# screens.
BUNDLES = {
    'billing': {
        'label': 'Billing Desk',
        'pages': {
            'wholesale': ['billing.html', 'tag_audit.html'],
            'retail':    ['pos_billing.html', 'tag_audit.html'],
        },
        'methods': frozenset([
            'get_sales_vch_id', 'get_next_uchak_vch_id', 'generate_bill',
            'generate_retail_bill', 'get_retail_estimate',
            'convert_estimate_to_paid', 'get_estimate_customers',
            'get_customer_estimate', 'get_bill_details',
            'get_live_invoice_print_payload', 'trigger_print_window',
            'open_print_window', 'generate_bill_pdf', 'send_bill_whatsapp',
            'get_pos_stock', 'get_product_by_tag', 'get_uchak_price_by_code',
            'check_weight_stock_available', 'check_uchak_stock_available',
            'get_touch_details', 'get_touch_groups', 'get_categories',
            'get_products', 'get_client_list', 'add_new_client',
            'get_client_live_outstanding', 'get_customer_purchases',
            'validate_touch_value', 'get_available_printers', 'get_printers',
            'check_printer_status', 'get_printer_status', 'print_tag',
            'print_tag_local', 'print_multiple_tags', 'get_tag_preview',
            'scale_connect', 'scale_disconnect', 'scale_get_ports',
            'scale_is_connected', 'scale_get_saved_config', 'scale_get_last',
            'tag_audit_start', 'tag_audit_scan', 'tag_audit_get_status',
            'tag_audit_mark_absent', 'tag_audit_remove_absence',
            'tag_audit_remove_scan', 'tag_audit_close',
            'tag_audit_start_snapshot', 'tag_audit_get_sessions',
            'tag_audit_get_available_touches',
        ]),
    },
    'crm': {
        'label': 'Customers',
        'pages': {
            'wholesale': ['crm.html', 'crm_schemes.html', 'customer_detail.html',
                          'customer_account.html'],
            'retail':    ['crm.html', 'crm_schemes.html', 'customer_detail.html',
                          'customer_account.html'],
        },
        'methods': frozenset([
            'crm_dashboard', 'crm_customers', 'crm_customer', 'crm_save_customer',
            'crm_add_note', 'crm_notes', 'crm_add_followup', 'crm_followups',
            'crm_close_followup', 'crm_orders', 'crm_save_order', 'crm_close_order',
            'crm_schemes', 'crm_save_scheme', 'crm_enrol', 'crm_enrolments',
            'crm_add_payment', 'crm_scheme_schedule',
            'crm_loyalty_balance', 'crm_award_loyalty', 'crm_redeem_loyalty',
            'photo_pick', 'photo_remove', 'photo_path', 'photo_to_clipboard',
            'photo_reveal', 'wa_send_item', 'items_with_photos',
            'wa_message', 'wa_wishes_today', 'wa_campaign', 'wa_mark_sent',
            'wa_opt_out', 'wa_history', 'wa_open',
            'get_client_list', 'get_client_balances', 'get_customer_purchases',
            'get_ledger_statement',
        ]),
    },
    'inventory': {
        'label': 'Inventory Vault',
        'pages': {
            'wholesale': ['inventory.html', 'stock_ledger.html',
                          'uchak_stock_entry.html', 'create_item.html'],
            # stock_ledger.html is how a retail shop adds a tagged piece, and
            # it is the only page that links to create_item.html. Granting
            # Create Item without it left staff holding a permission for a
            # screen they had no route to.
            'retail':    ['inventory.html', 'stock_ledger.html',
                          'create_item.html', 'sold_tags.html',
                          'bin_viewer.html'],
        },
        'methods': frozenset([
            'add_stock_entry', 'add_uchak_stock_entry', 'update_stock_entry',
            'get_stock_ledger', 'get_opening_stock', 'get_stock_ledger_by_date',
            'get_ledger_dates', 'get_product_by_tag', 'get_pos_stock',
            'get_item_by_tag', 'get_sold_tags',
            'get_inventory_stats', 'get_items_by_bin', 'get_untagged_items',
            'get_low_stock_items', 'get_low_stock_uchak_items',
            'get_out_of_stock_items', 'get_velocity_products',
            'get_weight_stock_it_codes', 'get_touch_ledger_details',
            'mark_as_tagged', 'get_stocksync_snapshot',
            'add_category', 'get_categories', 'add_touch_group',
            'get_touch_groups', 'add_product_master', 'get_products',
            'validate_touch_value', 'get_uchak_price_by_code',
            'print_tag', 'print_tag_local', 'print_multiple_tags',
            'get_tag_preview', 'get_available_printers', 'get_printers',
            'check_printer_status', 'get_printer_status',
            'scale_connect', 'scale_disconnect', 'scale_get_ports',
            'scale_is_connected', 'scale_get_saved_config', 'scale_get_last',
        ]),
    },
    'karigar': {
        'label': 'Karigar Desk',
        'pages': {
            'wholesale': ['karigar.html', 'katti_entry.html',
                          'uchak_stock_entry.html'],
            'retail':    ['karigar.html'],
        },
        'methods': frozenset([
            'get_katti_vch_id', 'save_katti_voucher', 'get_all_katti_vouchers',
            'update_katti_voucher', 'get_voucher_history',
            'get_next_uchak_inward_vch_id', 'get_uchak_inward_voucher_details',
            'save_uchak_inward_batch', 'get_touch_groups', 'get_categories',
            'get_products', 'get_client_list', 'validate_touch_value',
        ]),
    },
    'history': {
        'label': 'Sales History',
        'pages': {
            'wholesale': ['history.html', 'voucher_history.html'],
            'retail':    ['history.html'],
        },
        'methods': frozenset([
            'fetch_history', 'get_voucher_history_list', 'get_voucher_history',
            'get_bill_details', 'get_live_invoice_print_payload',
            'trigger_print_window', 'generate_bill_pdf', 'send_bill_whatsapp',
            'get_customer_purchases',
        ]),
    },
}

# What an owner may hand out on each plan. Pro keeps this deliberately small.
PLAN_BUNDLES = {
    'lite':       [],
    'pro':        ['billing'],
    'enterprise': ['billing', 'inventory', 'karigar', 'history'],
}


def bundles_for_plan(plan):
    return list(PLAN_BUNDLES.get(str(plan or 'lite').strip().lower(), []))


def pages_for(bundle_ids, software_type='wholesale'):
    """Sidebar pages for a set of bundles, in a stable order, no repeats."""
    mode = 'retail' if str(software_type).strip().lower() == 'retail' else 'wholesale'
    seen, out = set(), []
    for bid in (bundle_ids or []):
        for page in BUNDLES.get(bid, {}).get('pages', {}).get(mode, []):
            if page not in seen:
                seen.add(page)
                out.append(page)
    return out


def methods_for(bundle_ids):
    """Every bridge method the given bundles unlock, plus the shared ones."""
    allowed = set(ALWAYS)
    for bid in (bundle_ids or []):
        allowed |= set(BUNDLES.get(bid, {}).get('methods', ()))
    return allowed - set(OWNER_ONLY)


def is_allowed(method_name, role, bundle_ids=None):
    """The single question the bridge asks before running anything."""
    name = str(method_name or '')
    if name.startswith('_'):
        return False
    if str(role or '').strip().lower() != 'staff':
        return True          # owner and admin are unrestricted
    if name in OWNER_ONLY:
        return False
    return name in methods_for(bundle_ids)


# Methods the application calls on itself from its own background threads,
# rather than anything a page asked for. Licence checks, the event stream and
# the scale reconnect all run on timers, and a staff member being signed in at
# that moment must not make them fail. Found by scanning main.py for calls of
# the form api.<name>() outside the class, not by guesswork; re-run that scan
# if the startup code changes. Calls nested inside an already-authorised method
# are handled separately, by depth, so `self.<name>()` chains need no entry.
INTERNAL = frozenset([
    'set_window', 'check_license_revoked', 'fire_revoked_screen',
    'start_sse_stream', 'scale_get_saved_config',
])

# Tracks whether this thread is already inside a guarded method. Authorisation
# belongs at the boundary: once a call has been allowed in, whatever that
# method does internally is the application's own business.
_depth = threading.local()


def denial(method_name):
    """What a refused call hands back to the page that made it."""
    return {
        'status': 'denied',
        'denied': True,
        'method': str(method_name or ''),
        'message': 'Your account does not have access to this.',
    }


def _guarded(name, function, on_denied=None):
    @functools.wraps(function)
    def call(self, *args, **kwargs):
        outer = getattr(_depth, 'level', 0)
        if outer == 0 and name not in INTERNAL:
            role = getattr(self, '_session_role', None)
            if role and not is_allowed(name, role,
                                       getattr(self, '_session_bundles', None)):
                if on_denied:
                    try:
                        on_denied(name, role)
                    except Exception:
                        pass
                return denial(name)
        _depth.level = outer + 1
        try:
            return function(self, *args, **kwargs)
        finally:
            _depth.level = outer

    # Carry the original signature across explicitly.
    #
    # pywebview reads every method with inspect.getfullargspec and drops the
    # first argument, assuming it is self. getfullargspec does NOT follow the
    # __wrapped__ link that functools.wraps sets, so on its own the wrapper
    # would report (*args, **kwargs), which comes back as an empty argument
    # list. Every method would then be published to the page as taking no
    # arguments. getfullargspec does honour __signature__, so setting it keeps
    # the bridge exactly as it was.
    try:
        call.__signature__ = inspect.signature(function)
    except (TypeError, ValueError):
        pass

    return call


def install_guard(api, on_denied=None):
    """Check the policy before any bridge method runs.

    Wraps on the class rather than the instance, because pywebview finds the
    API's methods with dir() plus inspect.ismethod. An instance attribute
    holding a plain function would still be found, but its signature would be
    read one argument out of step; a bound method built by hand would report no
    arguments at all. A wrapped class function stays a bound method and keeps
    the signature it started with.

    Returns how many methods are now guarded.
    """
    cls = type(api)
    if getattr(cls, '_aurum_guard_installed', False):
        return 0

    guarded = 0
    for name, attribute in list(vars(cls).items()):
        if name.startswith('_') or not inspect.isfunction(attribute):
            continue
        setattr(cls, name, _guarded(name, attribute, on_denied))
        guarded += 1

    cls._aurum_guard_installed = True
    return guarded


def describe_bundles(plan=None):
    """Bundle list for the terminal-assignment screen."""
    allowed = bundles_for_plan(plan) if plan else list(BUNDLES)
    return [{
        'id': bid,
        'label': BUNDLES[bid]['label'],
        'available': bid in allowed,
    } for bid in BUNDLES]
