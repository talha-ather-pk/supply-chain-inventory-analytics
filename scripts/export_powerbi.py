"""
Write the Power BI tables (data_clean/powerbi/*.csv) from the database and the analysis outputs.

Star schema: dim_sku, dim_customer, dim_week, dim_carrier around fact_order_line, fact_inventory, fact_return,
fact_count and fact_po; small result tables (policy backtest, leaks, transit effects, what-ifs, data-quality log,
findings) sit beside them.
Run: python scripts/export_powerbi.py   (after clean.py, delivery.py, inventory.py, leaks.py, excel_template.py)
"""
from pathlib import Path
import json
import sqlite3
import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data_clean"
OUT = CLEAN / "powerbi"; OUT.mkdir(exist_ok=True)
con = sqlite3.connect(CLEAN / "larkspur.db")
q = lambda sql, **kw: pd.read_sql_query(sql, con, **kw)


def monday(s):
    s = pd.to_datetime(s)
    return (s - pd.to_timedelta(s.dt.day_of_week, "D")).dt.normalize()


def save(df, name):
    df.to_csv(OUT / f"{name}.csv", index=False, date_format="%Y-%m-%d")
    print(f"{name:<18} {len(df):>7,} rows")


# ------------------------------------------------------------------ dimensions
P = q("SELECT * FROM products")
pol = pd.read_csv(CLEAN / "sku_policy.csv")
rin = pd.read_csv(CLEAN / "reorder_inputs.csv")
S = P.merge(pol[["sku", "abc", "xyz", "class", "mean_weekly_demand", "cv", "service_level", "safety_stock",
                 "rop_recommended", "rop_change_pct", "in_policy"]], on="sku", how="left")
# reorder status at 31 Dec 2025: the Excel template's formulas, replicated
r = rin.copy()
sl = r.abc.map({"A": 0.98, "B": 0.95, "C": 0.90}) - np.where(r.xyz == "Z", 0.02, 0)
ss = np.ceil(norm.ppf(sl) * np.sqrt(r.lead_wk * r.sd_d ** 2 + r.mean_d ** 2 * r.lt_sd ** 2))
rop = np.round(r.mean_d * r.lead_wk + ss)
ip = r.on_hand_units + r.on_order_units - r.backlog_units
r["reorder_status"] = np.where(ip <= rop, "Reorder now", np.where(ip <= rop + 2 * r.mean_d, "Watch", "OK"))
S = S.merge(r[["sku", "reorder_status"]], on="sku", how="left")
S["reorder_status"] = S.reorder_status.fillna(pd.Series(np.where(S.status == "Discontinued", "Discontinued", "New / not planned"), index=S.index))
S["abc"] = S.abc.fillna("–"); S["xyz"] = S.xyz.fillna("–"); S["class"] = S["class"].fillna("–")
S["sku_label"] = S.sku + " · " + S.product_name
save(S[["sku", "sku_label", "product_name", "category", "fabric", "size", "colour", "production_line", "lead_time_wk", "zone",
        "status", "list_price", "unit_cost", "abc", "xyz", "class", "mean_weekly_demand", "cv", "service_level", "safety_stock",
        "rop_current", "rop_recommended", "rop_change_pct", "order_qty_current", "reorder_status", "in_policy"]], "dim_sku")
save(q("SELECT customer_id, customer_name, market, channel, late_penalty_rate FROM customers"), "dim_customer")
weeks = pd.date_range("2023-01-02", "2025-12-29", freq="7D")
dw = pd.DataFrame({"week_start": weeks})
dw["month_date"] = dw.week_start.dt.to_period("M").dt.to_timestamp()
dw["year"] = dw.week_start.dt.year
dw["quarter"] = "Q" + dw.week_start.dt.quarter.astype(str) + " " + dw.year.astype(str)
save(dw, "dim_week")
save(q("SELECT ship_mode, carrier, usd_per_kg FROM freight_rates"), "dim_carrier")

# ------------------------------------------------------------------ facts
O = q("SELECT * FROM order_lines", parse_dates=["order_date", "promised_date", "ship_date", "delivery_date"])
O["line_key"] = O.order_id + "-" + O.line_no.astype(str)
O["order_week"] = monday(O.order_date)
O["peak_sea"] = ((O.ship_mode == "Sea") & (O.ship_date.dt.month >= 10)).astype(int)
O["shipped"] = (O.status == "Shipped").astype(int)
save(O[["line_key", "order_id", "sku", "customer_id", "order_week", "order_date", "promised_date", "ship_date", "delivery_date",
        "ship_mode", "carrier", "status", "qty", "unit_price", "revenue", "cogs", "freight_cost", "expedite_premium", "chargeback",
        "lost_revenue", "lost_margin", "on_time", "days_late", "transit_days", "days_to_ship", "waited_for_stock", "expedited",
        "is_sample", "pod_missing", "peak_sea", "shipped"]], "fact_order_line")
INV = q("SELECT * FROM inventory_weekly")
save(INV[["week_start", "sku", "demand_units", "shipped_units", "lost_units", "received_units", "on_hand_units", "backlog_units",
          "on_order_units", "stock_value", "short_week"]], "fact_inventory")
R = q("SELECT * FROM returns", parse_dates=["return_date"])
R["return_week"] = monday(R.return_date)
save(R[["return_id", "order_id", "sku", "return_week", "return_date", "reason", "return_qty", "refund_value", "return_freight"]],
     "fact_return")
CC = q("SELECT * FROM cycle_counts", parse_dates=["count_date"])
CC["count_week"] = monday(CC.count_date)
save(CC[["count_week", "count_date", "sku", "zone", "system_qty", "counted_qty", "variance_units", "accurate"]], "fact_count")
PO = q("SELECT * FROM production_orders", parse_dates=["order_date", "due_date", "received_date"])
PO["order_week"] = monday(PO.order_date)
PO["late"] = np.where(PO.received_date.isna(), np.nan, (PO.days_late > 0).astype(float))
save(PO[["po_id", "sku", "order_week", "due_date", "received_date", "qty", "days_late", "late"]], "fact_po")

# ------------------------------------------------------------------ result tables
save(pd.read_csv(CLEAN / "policy_backtest.csv"), "policy_backtest")
save(pd.read_csv(CLEAN / "policy_backtest_by_line.csv"), "policy_by_line")
pw = pd.read_csv(CLEAN / "policy_weekly.csv")
pw = pd.concat([pw[["week_start"]].assign(policy="Current rule", stock_value=pw.stock_value_current, lost_units=pw.lost_units_current),
                pw[["week_start"]].assign(policy="Recommended", stock_value=pw.stock_value_recommended,
                                          lost_units=pw.lost_units_recommended)])
save(pw, "policy_weekly")
lk = pd.read_csv(CLEAN / "leaks.csv")
# waterfall rows: start from gross margin (revenue and cost of goods stay for the KPI measures, kind = base)
gm = lk[lk.item.isin(["Revenue", "Cost of goods"])].groupby("year", as_index=False).amount.sum().assign(item="Gross margin", step=2)
lk = pd.concat([lk, gm])
lk["kind"] = np.select([lk.item.isin(["Revenue", "Cost of goods"]), lk.item == "Gross margin", lk.step == 99],
                       ["base", "bridge", "memo"], "cost")
lk["step"] = np.where(lk.item == "Gross margin", 25, lk.step * 10)
save(lk.sort_values(["year", "step"])[["year", "step", "item", "kind", "amount"]], "leaks")
save(pd.read_csv(CLEAN / "transit_effects.csv"), "transit_effects")
wi = pd.read_csv(CLEAN / "delivery_whatif.csv"); wi.insert(0, "order", range(1, len(wi) + 1))
save(wi, "delivery_whatif")
save(q("SELECT * FROM dq_log"), "dq_log")

dm = json.loads((CLEAN / "delivery_metrics.json").read_text())
im = json.loads((CLEAN / "inventory_metrics.json").read_text())
lm = json.loads((CLEAN / "leak_metrics.json").read_text())
F = [("whatif_all_on_time", float(wi.on_time.iloc[-1]) / 100, "On time if all three delivery fixes were made"),
     ("whatif_meridian_gain", float(wi.gain_pts.iloc[1]) / 100, "On-time gain from moving Meridian volume"),
     ("whatif_peak_gain", float(wi.gain_pts.iloc[2]) / 100, "On-time gain from quoting peak congestion"),
     ("meridian_saving", dm["meridian_saving_per_year_usd"], "Freight Meridian saves a year"),
     ("meridian_chargebacks", dm["meridian_chargebacks_per_year_usd"], "Chargebacks on Meridian lines a year"),
     ("meridian_chargebacks_if", dm["meridian_chargebacks_if_blue_anchor_usd"], "...if those lines had Blue Anchor's transit"),
     ("transit_r2", dm["transit_model_r2"], "Transit model R squared"),
     ("effects_matched", dm["effects_within_half_day"], "Transit effects within half a day of the truth"),
     ("fill_current", im["replay_current_fill_pct"] / 100, "2025 fill rate, current rule (replay)"),
     ("fill_recommended", im["recommended_fill_pct"] / 100, "2025 fill rate, recommended reorder points"),
     ("fill_actual", im["actual_2025_fill_pct"] / 100, "2025 fill rate actually recorded"),
     ("lost_margin_saved", im["lost_margin_saved_usd"], "Margin no longer lost to stock-outs, 2025"),
     ("stock_change", im["stock_value_change_usd"], "Change in average stock value"),
     ("carrying_change", im["carrying_cost_change_usd"], "Change in carrying cost (22% a year)"),
     ("skus_in_policy", im["skus_in_policy"], "SKUs the policy covers"),
     ("avoidable_leaks_2025", lm["avoidable_leaks_2025"], "Avoidable leaks 2025"),
     ("avoidable_leaks_pct", lm["avoidable_leaks_pct_of_revenue"] / 100, "Avoidable leaks, share of revenue")]
save(pd.DataFrame(F, columns=["key", "value", "finding"]), "findings")
con.close()
