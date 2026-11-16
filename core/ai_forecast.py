"""
AI Demand Forecasting for AurumOS.
Statistical forecasting using weighted moving averages — no ML
libraries needed. Pure Python math on existing sales data.
"""
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta


def _get_db():
    from core.io_safety import robust_sqlite_connect
    db_path = os.environ.get('AURUM_DB_PATH', '').strip()
    if not db_path:
        db_path = os.path.join(os.path.abspath('.'), 'database', 'aurum_local.db')
    return robust_sqlite_connect(db_path)


def _weighted_ma(values, window=7):
    if not values:
        return 0
    n = min(len(values), window)
    subset = values[-n:]
    weights = list(range(1, n + 1))
    total_w = sum(weights)
    return sum(v * w for v, w in zip(subset, weights)) / total_w if total_w else 0


def forecast_daily_sales(days_ahead=30, lookback=90):
    try:
        conn = _get_db()
        end = datetime.now()
        start = end - timedelta(days=lookback)
        rows = conn.execute(
            "SELECT date, COUNT(*) as bills, COALESCE(SUM(total_amount),0) as revenue "
            "FROM sales_history WHERE date >= ? AND date <= ? GROUP BY date ORDER BY date",
            (start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))).fetchall()
        conn.close()

        daily = {}
        for r in rows:
            daily[r[0]] = {'bills': int(r[1] or 0), 'revenue': round(float(r[2] or 0), 2)}

        actual_dates = []
        actual_rev = []
        d = start
        while d <= end:
            ds = d.strftime('%Y-%m-%d')
            day_data = daily.get(ds, {'bills': 0, 'revenue': 0})
            actual_dates.append(ds)
            actual_rev.append(day_data['revenue'])
            d += timedelta(days=1)

        # Day-of-week multipliers from historical data
        dow_totals = defaultdict(list)
        for i, ds in enumerate(actual_dates):
            try:
                dow = datetime.strptime(ds, '%Y-%m-%d').weekday()
                if actual_rev[i] > 0:
                    dow_totals[dow].append(actual_rev[i])
            except Exception:
                pass
        dow_avg = {}
        overall_avg = sum(actual_rev) / max(len([x for x in actual_rev if x > 0]), 1)
        for dow in range(7):
            vals = dow_totals.get(dow, [])
            dow_avg[dow] = sum(vals) / len(vals) if vals else overall_avg
        dow_mult = {}
        for dow in range(7):
            dow_mult[dow] = dow_avg[dow] / overall_avg if overall_avg > 0 else 1.0

        # Forecast using WMA + day-of-week seasonality
        base = _weighted_ma(actual_rev, 14)
        forecast_dates = []
        forecast_rev = []
        for i in range(days_ahead):
            fd = end + timedelta(days=i + 1)
            dow = fd.weekday()
            pred = round(base * dow_mult.get(dow, 1.0), 2)
            forecast_dates.append(fd.strftime('%Y-%m-%d'))
            forecast_rev.append(max(0, pred))

        return {
            'status': 'success',
            'actual_dates': actual_dates[-30:],
            'actual_revenue': actual_rev[-30:],
            'forecast_dates': forecast_dates,
            'forecast_revenue': forecast_rev,
            'avg_daily': round(overall_avg, 2),
            'forecast_total': round(sum(forecast_rev), 2),
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


def reorder_suggestions():
    try:
        conn = _get_db()
        end = datetime.now()
        start = end - timedelta(days=90)
        sold = conn.execute("""
            SELECT items FROM sales_history WHERE date >= ? AND date <= ?
        """, (start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))).fetchall()

        demand = defaultdict(lambda: {'qty': 0, 'name': ''})
        for r in sold:
            try:
                items = json.loads(r[0] or '[]')
                for it in items:
                    code = str(it.get('code') or it.get('it_code') or '').strip()
                    if not code:
                        continue
                    demand[code]['qty'] += int(it.get('qty') or 1)
                    if not demand[code]['name']:
                        demand[code]['name'] = it.get('name', code)
            except Exception:
                pass

        stock = {}
        rows = conn.execute("SELECT it_code, COUNT(*) as pcs FROM stock_inventory GROUP BY it_code").fetchall()
        for r in rows:
            stock[r[0]] = int(r[1] or 0)
        conn.close()

        suggestions = []
        for code, d in demand.items():
            monthly_demand = round(d['qty'] / 3, 1)
            current = stock.get(code, 0)
            if monthly_demand > current:
                suggestions.append({
                    'code': code, 'name': d['name'],
                    'current_stock': current,
                    'monthly_demand': monthly_demand,
                    'reorder_qty': max(1, round(monthly_demand * 2 - current)),
                })
        suggestions.sort(key=lambda x: x['monthly_demand'], reverse=True)
        return {'status': 'success', 'suggestions': suggestions[:30]}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


def seasonal_patterns():
    try:
        conn = _get_db()
        rows = conn.execute("""
            SELECT date, COALESCE(SUM(total_amount),0) as revenue
            FROM sales_history WHERE date >= date('now','-365 days')
            GROUP BY date ORDER BY date
        """).fetchall()
        conn.close()

        month_dow = defaultdict(lambda: defaultdict(list))
        month_totals = defaultdict(list)
        for r in rows:
            try:
                dt = datetime.strptime(r[0], '%Y-%m-%d')
                rev = float(r[1] or 0)
                month_dow[dt.month][dt.weekday()].append(rev)
                month_totals[dt.month].append(rev)
            except Exception:
                pass

        heatmap = []
        for m in range(1, 13):
            row = []
            for dow in range(7):
                vals = month_dow[m][dow]
                row.append(round(sum(vals) / max(len(vals), 1), 2))
            heatmap.append(row)

        monthly_avg = []
        month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        for m in range(1, 13):
            vals = month_totals[m]
            monthly_avg.append({
                'month': month_names[m - 1],
                'avg_daily': round(sum(vals) / max(len(vals), 1), 2),
                'total': round(sum(vals), 2),
                'days': len(vals),
            })

        return {
            'status': 'success',
            'heatmap': heatmap,
            'months': month_names,
            'days': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
            'monthly': monthly_avg,
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)}
