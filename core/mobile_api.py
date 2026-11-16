"""
Mobile API server for AurumOS.
Lightweight Bottle HTTP API on a configurable port (default 58904).
Owners access from phone browser over WiFi. Enterprise-only.
"""
import json
import threading
from datetime import datetime, timedelta

try:
    import bottle
except ImportError:
    bottle = None

_app = bottle.Bottle() if bottle else None
_db = None
_api_key = ''
_server_thread = None


def _auth():
    if not _api_key:
        return True
    key = bottle.request.headers.get('X-API-Key', '') or bottle.request.params.get('key', '')
    if key != _api_key:
        bottle.abort(401, 'Invalid API key')
    return True


def _json(data):
    bottle.response.content_type = 'application/json'
    return json.dumps(data, ensure_ascii=False, default=str)


if _app:
    @_app.route('/api/mobile/dashboard')
    def dashboard():
        _auth()
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            with _db._get_connection() as conn:
                sales = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(total_amount),0) FROM sales_history WHERE date=?",
                    (today,)).fetchone()
                dues = conn.execute(
                    "SELECT COALESCE(SUM(total_amount),0) FROM sales_history WHERE status='CREDIT'").fetchone()
                stock = conn.execute("SELECT COUNT(*), COALESCE(SUM(gr_wt),0) FROM stock_inventory").fetchone()
            cash_pos = {'cash': 0, 'bank': 0, 'total': 0}
            try:
                cp = _db.finance_cash_position()
                if cp.get('status') == 'success':
                    cash_pos = {'cash': cp.get('cash', 0), 'bank': cp.get('bank', 0), 'total': cp.get('total', 0)}
            except Exception:
                pass
            return _json({
                'sales_today': {'bills': int(sales[0] or 0), 'revenue': round(float(sales[1] or 0), 2)},
                'outstanding_dues': round(float(dues[0] or 0), 2),
                'stock': {'items': int(stock[0] or 0), 'weight_g': round(float(stock[1] or 0), 3)},
                'cash_position': cash_pos,
                'timestamp': datetime.now().isoformat(),
            })
        except Exception as e:
            return _json({'error': str(e)})

    @_app.route('/api/mobile/sales')
    def sales():
        _auth()
        try:
            fr = bottle.request.params.get('from', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
            to = bottle.request.params.get('to', datetime.now().strftime('%Y-%m-%d'))
            with _db._get_connection() as conn:
                rows = conn.execute(
                    "SELECT vch_id, customer, date, total_amount, status, payment_mode "
                    "FROM sales_history WHERE date>=? AND date<=? ORDER BY date DESC LIMIT 100",
                    (fr, to)).fetchall()
            return _json({'sales': [dict(r) for r in rows]})
        except Exception as e:
            return _json({'error': str(e)})

    @_app.route('/api/mobile/stock')
    def stock():
        _auth()
        try:
            with _db._get_connection() as conn:
                rows = conn.execute(
                    "SELECT it_code, it_name, tag_id, gr_wt, touch, huid, category "
                    "FROM stock_inventory ORDER BY it_name LIMIT 500").fetchall()
            return _json({'stock': [dict(r) for r in rows]})
        except Exception as e:
            return _json({'error': str(e)})

    @_app.route('/api/mobile/customers')
    def customers():
        _auth()
        try:
            with _db._get_connection() as conn:
                rows = conn.execute("""
                    SELECT customer as name, COUNT(*) as bills,
                           COALESCE(SUM(total_amount),0) as ltv, MAX(date) as last_purchase
                    FROM sales_history WHERE customer!='' AND customer!='Walk-in'
                    GROUP BY customer ORDER BY ltv DESC LIMIT 100
                """).fetchall()
            return _json({'customers': [dict(r) for r in rows]})
        except Exception as e:
            return _json({'error': str(e)})

    @_app.route('/api/mobile/alerts')
    def alerts():
        _auth()
        try:
            items = []
            with _db._get_connection() as conn:
                low = conn.execute(
                    "SELECT it_code, it_name, COUNT(*) as pcs FROM stock_inventory "
                    "GROUP BY it_code HAVING pcs <= 2 ORDER BY pcs LIMIT 20").fetchall()
                for r in low:
                    items.append({'type': 'low_stock', 'code': r[0], 'name': r[1], 'pcs': int(r[2])})
                overdue = conn.execute(
                    "SELECT customer, COALESCE(SUM(total_amount),0) as due FROM sales_history "
                    "WHERE status='CREDIT' GROUP BY customer HAVING due > 0 ORDER BY due DESC LIMIT 20").fetchall()
                for r in overdue:
                    items.append({'type': 'due_payment', 'customer': r[0], 'amount': round(float(r[1]), 2)})
            return _json({'alerts': items})
        except Exception as e:
            return _json({'error': str(e)})

    @_app.route('/api/mobile/pnl')
    def pnl():
        _auth()
        try:
            fr = bottle.request.params.get('from', '')
            to = bottle.request.params.get('to', '')
            result = _db.finance_profit_loss(fr, to)
            return _json(result)
        except Exception as e:
            return _json({'error': str(e)})


def start(db, port=58904, api_key=''):
    global _db, _api_key, _server_thread
    if not bottle or not _app:
        return False
    _db = db
    _api_key = api_key or ''

    def _run():
        try:
            _app.run(host='0.0.0.0', port=int(port), quiet=True)
        except Exception:
            pass

    _server_thread = threading.Thread(target=_run, daemon=True)
    _server_thread.start()
    return True


def stop():
    global _server_thread
    if _app:
        try:
            _app.close()
        except Exception:
            pass
    _server_thread = None
