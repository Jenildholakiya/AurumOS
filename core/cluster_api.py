# -*- coding: utf-8 -*-
"""The single entry point every multi-PC call goes through.

main.py exposes exactly one method for all of this, `cluster(action, payload)`,
which hands straight over to `dispatch` below. That indirection is the whole
point: main.py cannot be updated remotely, so anything named there is frozen
into the EXE until every shop is visited and reinstalled. Everything reachable
through this file ships with an ordinary release instead, so the multi-PC
feature can keep growing without another rebuild.

Add an action by writing a function and listing it in ACTIONS.
"""

from network import cluster_client as client
from network.cluster_server import CLUSTER_PORT


def _fail(message, status='error', **extra):
    out = {'status': status, 'message': message}
    out.update(extra)
    return out


# -- shared ---------------------------------------------------------------

def identity(api, payload):
    """What this PC is, for the setup and network screens."""
    cfg = client.load_config()
    role = client.local_role()
    return {
        'status':      'success',
        'role':        role,
        'is_server':   role == client.ROLE_SERVER,
        'terminal_id': client.terminal_id(api.db),
        'hostname':    client.hostname(),
        'ip':          api.get_local_ip(),
        'server_ip':   cfg.get('server_ip', ''),
        'server_port': int(cfg.get('server_port') or CLUSTER_PORT),
        'business':    cfg.get('shop_business', ''),
    }


def status(api, payload):
    """Whether this PC can currently see its server."""
    role = client.local_role()
    if role == client.ROLE_SERVER:
        from network import cluster_server
        return {'status': 'success', 'role': role,
                'serving': cluster_server.is_running()}
    return {'status': 'success', 'role': role,
            'online': client.server_online(),
            'server_ip': client.load_config().get('server_ip', '')}


# -- joining a shop -------------------------------------------------------

def verify_server(api, payload):
    """Check an address typed into setup, before committing to it."""
    return client.verify_server(payload.get('ip'),
                                payload.get('port') or CLUSTER_PORT)


def join(api, payload):
    return client.join_server(api.db, payload.get('ip'),
                              payload.get('port') or CLUSTER_PORT)


def become_server(api, payload):
    """Mark this PC as the one that holds the shop, and start serving.

    Called from setup when the Host Core card is chosen. Starting the server
    here means a staff PC can be joined immediately afterwards, without the
    owner having to restart the application first.
    """
    client.leave_server()
    try:
        api._start_cluster()
    except Exception:
        pass
    from network import cluster_server
    return {'status': 'success', 'role': client.ROLE_SERVER,
            'serving': cluster_server.is_running(), 'ip': api.get_local_ip()}


def leave(api, payload):
    return client.leave_server()


def login(api, payload):
    """Sign a staff member in against the shop server.

    On success the session is recorded locally too, so the rest of the app
    sees a normal logged-in staff user and the sidebar has its page list.
    """
    result = client.login(payload.get('password') or '')
    if result.get('status') != 'success':
        return result

    api._session_role = 'staff'
    api._session_username = result.get('username', '')
    api._session_bundles = list(result.get('bundles') or [])
    try:
        api._audit(f'Terminal login: {api._session_username}',
                   f'Modules: {", ".join(api._session_bundles) or "none"}', 'auth')
    except Exception:
        pass
    return result


# -- the owner's view of connected PCs ------------------------------------

def _server_only(api):
    if client.local_role() != client.ROLE_SERVER:
        return _fail('Only the main PC can manage connected computers.',
                     status='not_server')
    return None


def list_terminals(api, payload):
    blocked = _server_only(api)
    if blocked:
        return blocked
    from database import cluster_store as store
    from core import permissions as perms
    plan, features = _plan_and_features(api)
    licensed = perms.licensed_seats(plan, features)
    terminals = store.list_terminals(api.db)
    return {
        'status':    'success',
        'terminals': terminals,
        'licensed':  licensed,
        'in_use':    store.seats_in_use(api.db),
        'plan':      plan,
        'bundles':   perms.describe_bundles(plan),
    }


def assign_terminal(api, payload):
    blocked = _server_only(api)
    if blocked:
        return blocked
    from database import cluster_store as store
    from core import permissions as perms
    plan, _ = _plan_and_features(api)

    wanted = list(payload.get('bundles') or [])
    allowed = perms.bundles_for_plan(plan)
    rejected = [b for b in wanted if b not in allowed]
    if rejected:
        return _fail(f'Your plan does not include: {", ".join(rejected)}.',
                     status='plan_limit')

    terminal = store.assign_terminal(
        api.db, payload.get('terminal_id'),
        username=payload.get('username', ''),
        bundles=wanted,
        status=payload.get('status') or store.STATUS_APPROVED)
    return {'status': 'success', 'terminal': terminal}


def block_terminal(api, payload):
    blocked = _server_only(api)
    if blocked:
        return blocked
    from database import cluster_store as store
    return {'status': 'success',
            'terminal': store.assign_terminal(api.db, payload.get('terminal_id'),
                                              username=None, bundles=None,
                                              status=store.STATUS_BLOCKED)}


def remove_terminal(api, payload):
    blocked = _server_only(api)
    if blocked:
        return blocked
    from database import cluster_store as store
    store.remove_terminal(api.db, payload.get('terminal_id'))
    return {'status': 'success'}


def seats(api, payload):
    from database import cluster_store as store
    from core import permissions as perms
    plan, features = _plan_and_features(api)
    licensed = perms.licensed_seats(plan, features)
    return {'status': 'success', 'plan': plan, 'licensed': licensed,
            'in_use': store.seats_in_use(api.db),
            'available': max(licensed - store.seats_in_use(api.db), 0)}


def _plan_and_features(api):
    try:
        state = api.sub.get_state_json()
        return state.get('effective_plan', 'lite'), state.get('features', [])
    except Exception:
        return 'lite', []


ACTIONS = {
    'identity':        identity,
    'status':          status,
    'verify_server':   verify_server,
    'join':            join,
    'leave':           leave,
    'become_server':   become_server,
    'login':           login,
    'list_terminals':  list_terminals,
    'assign_terminal': assign_terminal,
    'block_terminal':  block_terminal,
    'remove_terminal': remove_terminal,
    'seats':           seats,
}


def dispatch(api, action, payload=None):
    handler = ACTIONS.get(str(action or '').strip())
    if handler is None:
        return _fail(f'Unknown network action: {action}', status='unknown_action')
    try:
        return handler(api, payload or {})
    except Exception as e:
        try:
            import logging
            logging.getLogger('aurumos.cluster').exception(
                f'[CLUSTER] action {action} failed')
        except Exception:
            pass
        return _fail(str(e))
