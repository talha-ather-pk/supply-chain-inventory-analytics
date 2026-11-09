"""
Clean the raw ERP extracts and load them into SQLite (data_clean/larkspur.db).

Every fix is logged in dq_log with the rows it touched next to the number the generator injected
(data_raw/true_effects.json), so the cleaning can be audited.

Tables: products, customers, order_lines, credit_notes, production_orders, inventory_weekly, cycle_counts,
        returns, freight_rates, dq_log
Run: python scripts/clean.py
"""
from pathlib import Path
import json
import sqlite3
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW, CLEAN = ROOT / "data_raw", ROOT / "data_clean"
CLEAN.mkdir(exist_ok=True)
DB = CLEAN / "larkspur.db"
inj = json.loads((RAW / "true_effects.json").read_text())["injected"]
log = []


def note(table, issue, rows, action, key=None):
    log.append({"step": len(log) + 1, "table_name": table, "issue": issue, "rows_affected": int(rows), "action": action,
                "injected": inj.get(key) if key else None})
    print(f"{len(log):>2}. {table:<14} {issue:<44} {rows:>7,}  {action}")
    if key:
        assert int(rows) == inj[key], f"{issue}: fixed {rows}, injected {inj[key]}"


# ------------------------------------------------------------------ products
P = pd.read_csv(RAW / "products.csv")
note("products", "Rows received", len(P), "loaded")
canon = P.category.str.strip().str.lower().replace({"bedsheets": "bed sheets", "bed sheet": "bed sheets"})
fixed = canon.str.capitalize() != P.category
note("products", "Category spelled several ways", fixed.sum(), "mapped to 'Bed sheets'", "category_spelling_variants")
P["category"] = canon.str.capitalize()
miss = P.unit_cost.isna()
share = (P.unit_cost / P.list_price).groupby(P.category).transform("median")
P["unit_cost"] = P.unit_cost.fillna((P.list_price * share).round(2))
P["cost_imputed"] = miss.astype(int)
note("products", "Unit cost missing (new SKUs)", miss.sum(), "category cost ratio x list price, flagged", "unit_cost_missing")

# ------------------------------------------------------------------ customers
C = pd.read_csv(RAW / "customers.csv")
note("customers", "Rows received", len(C), "loaded")

# ------------------------------------------------------------------ order lines
O = pd.read_csv(RAW / "order_lines.csv", dtype={"order_id": str, "customer_id": str})
note("order_lines", "Rows received", len(O), "loaded")
n = len(O); O = O.drop_duplicates(); note("order_lines", "Exact duplicate lines (re-run export)", n - len(O), "removed", "duplicate_lines")
test = O.sku.str.startswith("TEST")
note("order_lines", "Test lines from ERP go-live", test.sum(), "removed", "test_lines"); O = O[~test]
cn = O.qty < 0
note("order_lines", "Credit notes booked as negative lines", cn.sum(), "moved to credit_notes", "negative_qty_credit_notes")
CN = O[cn].copy(); O = O[~cn].copy()
assert not O.duplicated(["order_id", "line_no"]).any()

iso = pd.to_datetime(O.order_date, format="%Y-%m-%d", errors="coerce")
dmy = pd.to_datetime(O.order_date, format="%d/%m/%Y", errors="coerce")
note("order_lines", "Order date as dd/mm/yyyy (GCC EDI feed)", iso.isna().sum(), "parsed with the EDI format",
     "order_date_ddmmyyyy (GCC EDI feed)")
assert (iso.notna() | dmy.notna()).all()
O["order_date"] = iso.fillna(dmy)
for c in ["promised_date", "ship_date", "delivery_date"]:
    O[c] = pd.to_datetime(O[c])

mode_map = {"sea": "Sea", "ocean": "Sea", "air": "Air", "road": "Road", "truck": "Road"}
clean_mode = O.ship_mode.str.strip().str.lower().map(mode_map)
note("order_lines", "Ship mode typed by hand (SEA, Ocean, Truck)", (clean_mode != O.ship_mode).sum(), "mapped to Sea / Air / Road",
     "ship_mode_variants")
O["ship_mode"] = clean_mode
cid = O.customer_id.str.strip().str.upper()
note("order_lines", "Customer id with stray case or spaces", (cid != O.customer_id).sum(), "trimmed, upper-cased",
     "customer_id_case_spaces")
O["customer_id"] = cid
assert O.customer_id.isin(C.customer_id).all()
sample = (O.unit_price == 0) & (O.status == "Shipped")
O["is_sample"] = sample.astype(int)
note("order_lines", "Free samples at zero price", sample.sum(), "kept, flagged, no revenue", "zero_price_samples")
pod = (O.status == "Shipped") & O.delivery_date.isna()
O["pod_missing"] = pod.astype(int)
note("order_lines", "Delivered, proof of delivery missing", pod.sum(), "kept, left out of on-time rates",
     "delivery_date_missing (POD not uploaded)")

# derived fields
O = O.merge(P[["sku", "unit_cost", "category", "production_line", "unit_weight_kg"]], on="sku", how="left")
O = O.merge(C[["customer_id", "market", "channel", "late_penalty_rate"]], on="customer_id", how="left")
shipped = O.status == "Shipped"
O["revenue"] = np.where(shipped, O.qty * O.unit_price, 0.0).round(2)
O["cogs"] = np.where(shipped, O.qty * O.unit_cost, 0.0).round(2)
O["lost_revenue"] = np.where(O.status == "Cancelled - stock-out", O.qty * O.unit_price, 0.0).round(2)
O["lost_margin"] = np.where(O.status == "Cancelled - stock-out", O.qty * (O.unit_price - O.unit_cost), 0.0).round(2)
O["days_to_ship"] = (O.ship_date - O.order_date).dt.days
O["waited_for_stock"] = (O.days_to_ship > 3).astype(int)          # quote allows 3 days to dispatch
O["transit_days"] = (O.delivery_date - O.ship_date).dt.days
measured = shipped & O.delivery_date.notna()
O["days_late"] = np.where(measured, (O.delivery_date - O.promised_date).dt.days.clip(lower=0), np.nan)
O["on_time"] = np.where(measured, (O.delivery_date <= O.promised_date).astype(float), np.nan)
O["chargeback"] = np.where(O.on_time == 0, O.revenue * O.late_penalty_rate, 0.0).round(2)
rates = pd.read_csv(RAW / "freight_rates.csv")
sea_rate = rates[rates.ship_mode == "Sea"].usd_per_kg.mean()
O["expedite_premium"] = np.where(O.expedited == 1, O.freight_cost - O.qty * O.unit_weight_kg * sea_rate, 0.0).round(2)
O["order_week"] = (O.order_date - pd.to_timedelta(O.order_date.dt.day_of_week, "D")).dt.normalize()
O["order_month"] = O.order_date.dt.to_period("M").dt.to_timestamp()

# ------------------------------------------------------------------ other tables
PO = pd.read_csv(RAW / "production_orders.csv", parse_dates=["order_date", "due_date", "received_date"])
PO["days_late"] = (PO.received_date - PO.due_date).dt.days
note("production_orders", "Rows received", len(PO), "loaded")
INV = pd.read_csv(RAW / "inventory_weekly.csv", parse_dates=["week_start"])
note("inventory_weekly", "Rows received", len(INV), "loaded")
INV = INV.merge(P[["sku", "unit_cost"]], on="sku")
INV["stock_value"] = (INV.on_hand_units * INV.unit_cost).round(2)
INV["short_week"] = ((INV.lost_units + INV.backlog_units) > 0).astype(int)
CC = pd.read_csv(RAW / "cycle_counts.csv", parse_dates=["count_date"])
note("cycle_counts", "Rows received", len(CC), "loaded")
nf = CC.counted_qty.isna()
note("cycle_counts", "Count keyed in but not finished", nf.sum(), "removed", "cycle_count_not_finished"); CC = CC[~nf].copy()
CC["variance_units"] = CC.counted_qty - CC.system_qty
CC["accurate"] = (CC.variance_units.abs() <= np.maximum(1, 0.02 * CC.system_qty)).astype(int)   # +/-2% tolerance
RT = pd.read_csv(RAW / "returns.csv", parse_dates=["return_date"])
note("returns", "Rows received", len(RT), "loaded")
RT = RT.merge(P[["sku", "production_line", "category", "fabric"]], on="sku")
note("order_lines", "Clean order lines", len(O), "loaded")

# ------------------------------------------------------------------ load
dates = lambda df: df.assign(**{c: df[c].dt.strftime("%Y-%m-%d") for c in df.columns if str(df[c].dtype).startswith("datetime")})
DB.unlink(missing_ok=True)
with sqlite3.connect(DB) as con:
    for name, df in [("products", P), ("customers", C), ("order_lines", O), ("credit_notes", CN), ("production_orders", PO),
                     ("inventory_weekly", INV), ("cycle_counts", CC), ("returns", RT), ("freight_rates", rates),
                     ("dq_log", pd.DataFrame(log))]:
        dates(df).to_sql(name, con, index=False)
    con.execute("CREATE INDEX ix_ol_sku ON order_lines(sku)"); con.execute("CREATE INDEX ix_inv_sku ON inventory_weekly(sku)")
print(f"wrote {DB} ({DB.stat().st_size / 1e6:.0f} MB)")
