"""
Profit leaks: from gross margin to what is left after the costs of running the supply chain badly, by year.

Gross margin = revenue - standard cost of goods shipped
then         - normal outbound freight
             - expedite premium      (air freight paid on lines switched from sea after waiting for stock)
             - late chargebacks      (retailer penalties: 3% big-box, 2% department stores, on late lines)
             - returns               (refunds + return freight; the goods are written off)
             - inventory carrying    (22% a year on average finished-goods stock at cost)
             - obsolete stock        (stock of discontinued SKUs left at 31 Dec 2025, written off at 70% of cost)
Memo:          margin lost on lines cancelled for lack of stock (an opportunity cost, not in the P&L).

Outputs: data_clean/leaks.csv (year x item), data_clean/leak_metrics.json
Run: python scripts/leaks.py
"""
from pathlib import Path
import json
import sqlite3
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data_clean"
CARRY, WRITE_OFF = 0.22, 0.70
with sqlite3.connect(CLEAN / "larkspur.db") as con:
    O = pd.read_sql_query("SELECT * FROM order_lines", con, parse_dates=["order_date", "ship_date"])
    R = pd.read_sql_query("SELECT * FROM returns", con, parse_dates=["return_date"])
    INV = pd.read_sql_query("SELECT * FROM inventory_weekly", con, parse_dates=["week_start"])
    P = pd.read_sql_query("SELECT sku, status, unit_cost FROM products", con)

O["year"] = O.order_date.dt.year
R["year"] = R.return_date.dt.year
INV["year"] = INV.week_start.dt.year
end = INV[INV.week_start == INV.week_start.max()].merge(P[["sku", "status"]], on="sku")
obsolete = float((end.stock_value[end.status == "Discontinued"]).sum() * WRITE_OFF)

rows = []
for y in (2023, 2024, 2025):
    o = O[O.year == y]
    items = [
        ("Revenue", o.revenue.sum()),
        ("Cost of goods", -o.cogs.sum()),
        ("Outbound freight", -(o.freight_cost.sum() - o.expedite_premium.sum())),
        ("Expedite premium", -o.expedite_premium.sum()),
        ("Late chargebacks", -o.chargeback.sum()),
        ("Returns", -(R[R.year == y].refund_value.sum() + R[R.year == y].return_freight.sum())),
        ("Inventory carrying", -INV[INV.year == y].groupby("week_start").stock_value.sum().mean() * CARRY),
        ("Obsolete stock", -obsolete if y == 2025 else 0.0),
    ]
    for i, (k, v) in enumerate(items):
        rows.append({"year": y, "step": i + 1, "item": k, "amount": round(float(v), 2)})
    rows.append({"year": y, "step": 99, "item": "Memo: margin lost to stock-outs", "amount": round(float(o.lost_margin.sum()), 2)})
L = pd.DataFrame(rows)
L.to_csv(CLEAN / "leaks.csv", index=False)

t = L.pivot(index="item", columns="year", values="amount")
order = L.drop_duplicates("item").set_index("item").step.sort_values().index
t = t.loc[order]
print((t / 1e3).round(0).to_string())
y25 = L[L.year == 2025].set_index("item").amount
gm = y25["Revenue"] + y25["Cost of goods"]
leaks = -y25[["Expedite premium", "Late chargebacks", "Returns", "Obsolete stock"]].sum()
m = {"revenue_2025": round(y25["Revenue"]), "gross_margin_2025": round(gm), "gross_margin_pct_2025": round(100 * gm / y25["Revenue"], 1),
     "avoidable_leaks_2025": round(leaks), "avoidable_leaks_pct_of_revenue": round(100 * leaks / y25["Revenue"], 2),
     "carrying_2025": round(-y25["Inventory carrying"]), "lost_margin_2025": round(y25["Memo: margin lost to stock-outs"]),
     "contribution_2025": round(y25[[i for i in y25.index if not i.startswith("Memo")]].sum())}
(CLEAN / "leak_metrics.json").write_text(json.dumps(m, indent=2))
print(m)
