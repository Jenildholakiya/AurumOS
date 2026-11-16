"""
Multi-branch management for AurumOS.
Each branch runs independently. The owner branch imports summary
JSON from other branches for consolidated reporting.
"""
import json
import os
import sqlite3
from datetime import datetime


def _get_db():
    from core.io_safety import robust_sqlite_connect
    db_path = os.environ.get('AURUM_DB_PATH', '').strip()
    if not db_path:
        db_path = os.path.join(os.path.abspath('.'), 'database', 'aurum_local.db')
    return robust_sqlite_connect(db_path)


def _ensure_table():
    conn = _get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS branch_register (
        id INTEGER PRIMARY KEY AUTOINCREMENT, branch_name TEXT NOT NULL,
        branch_code TEXT UNIQUE, city TEXT DEFAULT '', owner_name TEXT DEFAULT '',
        last_sync TEXT DEFAULT '', summary TEXT DEFAULT '{}',
        active INTEGER DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()
    conn.close()


def register_branch(data):
    _ensure_table()
    d = data or {}
    try:
        conn = _get_db()
        conn.execute(
            "INSERT OR REPLACE INTO branch_register(branch_code,branch_name,city,owner_name) VALUES(?,?,?,?)",
            (d.get('code', ''), d.get('name', ''), d.get('city', ''), d.get('owner', '')))
        conn.commit()
        conn.close()
        return {'status': 'success'}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


def list_branches():
    _ensure_table()
    try:
        conn = _get_db()
        rows = conn.execute("SELECT * FROM branch_register WHERE active=1 ORDER BY branch_name").fetchall()
        conn.close()
        branches = []
        for r in rows:
            summary = {}
            try:
                summary = json.loads(r[6] or '{}')
            except Exception:
                pass
            branches.append({
                'id': r[0], 'name': r[1], 'code': r[2], 'city': r[3],
                'owner': r[4], 'last_sync': r[5], 'summary': summary
            })
        return {'status': 'success', 'branches': branches}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


def export_branch_summary(db):
    """Export this branch's daily summary as JSON for import by the owner branch."""
    try:
        with db._get_connection() as conn:
            today = datetime.now().strftime('%Y-%m-%d')
            sales = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(total_amount),0), COALESCE(SUM(ledger_fine),0) "
                "FROM sales_history WHERE date=?", (today,)).fetchone()
            stock = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(gr_wt),0) FROM stock_inventory").fetchone()
            cash_pos = {'cash': 0, 'bank': 0}
            try:
                cp = db.finance_cash_position()
                if cp.get('status') == 'success':
                    cash_pos = {'cash': cp.get('cash', 0), 'bank': cp.get('bank', 0)}
            except Exception:
                pass
            dues = conn.execute(
                "SELECT COALESCE(SUM(remaining_fine),0) FROM sales_history WHERE status='CREDIT'").fetchone()

        biz = db.get_config('business_name', '')
        city = db.get_config('shop_city', '')
        summary = {
            'branch_name': biz,
            'branch_code': db.get_config('terminal_id', biz[:3].upper() if biz else 'HQ'),
            'city': city,
            'export_date': today,
            'export_time': datetime.now().isoformat(),
            'sales_today': {'bills': int(sales[0] or 0), 'revenue': round(float(sales[1] or 0), 2),
                            'fine': round(float(sales[2] or 0), 3)},
            'stock': {'items': int(stock[0] or 0), 'weight': round(float(stock[1] or 0), 3)},
            'cash_position': cash_pos,
            'outstanding_dues': round(float(dues[0] or 0), 3),
        }
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        export_dir = os.path.join(base, 'exports')
        os.makedirs(export_dir, exist_ok=True)
        path = os.path.join(export_dir, f'branch_summary_{today}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        return {'status': 'success', 'path': path, 'summary': summary}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


def import_branch_summary(file_path):
    """Import a branch summary JSON and upsert into branch_register."""
    _ensure_table()
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        code = data.get('branch_code', '')
        if not code:
            return {'status': 'error', 'message': 'No branch_code in summary'}
        conn = _get_db()
        conn.execute("""INSERT INTO branch_register(branch_code,branch_name,city,last_sync,summary)
            VALUES(?,?,?,?,?) ON CONFLICT(branch_code) DO UPDATE SET
            branch_name=excluded.branch_name, city=excluded.city,
            last_sync=excluded.last_sync, summary=excluded.summary""",
            (code, data.get('branch_name', ''), data.get('city', ''),
             data.get('export_time', ''), json.dumps(data)))
        conn.commit()
        conn.close()
        return {'status': 'success', 'branch_code': code}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


def consolidated_report():
    """Aggregate all branch summaries into a consolidated report."""
    result = list_branches()
    if result.get('status') != 'success':
        return result
    branches = result.get('branches', [])
    total_rev = 0
    total_bills = 0
    total_stock_items = 0
    total_stock_wt = 0
    total_cash = 0
    total_bank = 0
    total_dues = 0
    branch_data = []
    for b in branches:
        s = b.get('summary', {})
        sales = s.get('sales_today', {})
        stock = s.get('stock', {})
        cp = s.get('cash_position', {})
        rev = float(sales.get('revenue', 0))
        bills = int(sales.get('bills', 0))
        total_rev += rev
        total_bills += bills
        total_stock_items += int(stock.get('items', 0))
        total_stock_wt += float(stock.get('weight', 0))
        total_cash += float(cp.get('cash', 0))
        total_bank += float(cp.get('bank', 0))
        total_dues += float(s.get('outstanding_dues', 0))
        branch_data.append({
            'name': b['name'], 'code': b['code'], 'city': b['city'],
            'revenue': rev, 'bills': bills,
            'stock_items': int(stock.get('items', 0)),
            'stock_weight': round(float(stock.get('weight', 0)), 3),
            'last_sync': b.get('last_sync', ''),
        })
    return {
        'status': 'success',
        'total_revenue': round(total_rev, 2), 'total_bills': total_bills,
        'total_stock_items': total_stock_items,
        'total_stock_weight': round(total_stock_wt, 3),
        'total_cash': round(total_cash, 2), 'total_bank': round(total_bank, 2),
        'total_dues': round(total_dues, 3),
        'branches': branch_data,
    }
