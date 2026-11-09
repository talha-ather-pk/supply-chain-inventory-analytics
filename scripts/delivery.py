"""
What makes deliveries late? A transit-time model, a check against the generator's true effects, and what-ifs.

1. Linear model of transit days on carrier, market, peak season (Oct-Dec departure) and mode, fitted on delivered lines.
   Its coefficients are compared with data_raw/true_effects.json (the generator's real extra days).
2. What-ifs on the same lines: how on-time changes if Meridian sea volume moves to Blue Anchor, if Oct-Dec sea quotes
   include the congestion the model measured, and if no line had to wait for stock.

Outputs: data_clean/transit_effects.csv, data_clean/delivery_whatif.csv, data_clean/delivery_metrics.json
Run: python scripts/delivery.py
"""
from pathlib import Path
import json
import sqlite3
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data_clean"
truth = json.loads((ROOT / "data_raw" / "true_effects.json").read_text())["transit_days"]
with sqlite3.connect(CLEAN / "larkspur.db") as con:
    O = pd.read_sql_query("SELECT * FROM order_lines WHERE on_time IS NOT NULL", con,
                          parse_dates=["order_date", "promised_date", "ship_date", "delivery_date"])
O["peak"] = ((O.ship_mode == "Sea") & (O.ship_date.dt.month >= 10)).astype(int)

# ------------------------------------------------------------------ 1. transit model, sea lines (the mode with the problem)
S = O[O.ship_mode == "Sea"].copy()
X = pd.get_dummies(S[["carrier", "market"]]).astype(float)
X = X.drop(columns=["carrier_Blue Anchor Line", "market_USA"])          # baselines
X["peak"] = S.peak
lr = LinearRegression().fit(X, S.transit_days)
est = dict(zip(X.columns, lr.coef_))
sea_base = truth["sea_base"]; customs = truth["customs"]
rows = [("Meridian vs Blue Anchor", est["carrier_Meridian Freight"], truth["carrier_extra_days"]["Sea"]["Meridian Freight"]),
        ("Coral vs Blue Anchor", est["carrier_Coral Shipping"], truth["carrier_extra_days"]["Sea"]["Coral Shipping"]),
        ("Oct-Dec departure", est["peak"], truth["peak_sea_oct_dec"])]
for m in ["UK", "EU", "GCC"]:
    true_m = (sea_base[m] + customs[m]) - (sea_base["USA"] + customs["USA"])
    rows.append((f"{m} vs USA", est[f"market_{m}"], true_m))
T = pd.DataFrame(rows, columns=["effect", "estimated_days", "true_days"]).round(2)
T["error_days"] = (T.estimated_days - T.true_days).round(2)
T.to_csv(CLEAN / "transit_effects.csv", index=False)
print(T.to_string(index=False))

# ------------------------------------------------------------------ 2. what-ifs (all lines with a measured delivery)
late_by = (O.delivery_date - O.promised_date).dt.days


def on_time(shift_days):
    return float(((late_by - shift_days) <= 0).mean())


base = on_time(0)
mer = np.where(O.carrier == "Meridian Freight", est["carrier_Meridian Freight"], 0.0)
peak = np.where(O.peak == 1, est["peak"], 0.0)
# lines that waited for stock: days beyond the 3 quoted for dispatch
wait = np.where(O.waited_for_stock == 1, (O.days_to_ship - 3).clip(lower=0), 0.0)
W = pd.DataFrame([
    ("Today", base),
    ("Move Meridian sea volume to Blue Anchor", on_time(mer)),
    ("Quote Oct-Dec sea with the congestion allowance", on_time(peak)),
    ("No line waits for stock", on_time(wait)),
    ("All three", on_time(mer + peak + wait)),
], columns=["scenario", "on_time"])
W["gain_pts"] = ((W.on_time - base) * 100).round(1)
W["on_time"] = (W.on_time * 100).round(1)
# what the switch would cost: Meridian is cheaper per kg
kg_mer = (O.qty * O.unit_weight_kg)[O.carrier == "Meridian Freight"].sum() / 3     # per year
W.to_csv(CLEAN / "delivery_whatif.csv", index=False)
print(W.to_string(index=False))

shipped = O
cb_mer = O.loc[O.carrier == "Meridian Freight", "chargeback"].sum() / 3
cb_mer_if = (O.loc[O.carrier == "Meridian Freight"].pipe(lambda d: d.revenue * d.late_penalty_rate
                                                          * ((late_by[d.index] - mer[d.index]) > 0))).sum() / 3
metrics = {
    "on_time_pct": round(base * 100, 1),
    "transit_model_r2": round(lr.score(X, S.transit_days), 3),
    "effects_within_half_day": int((T.error_days.abs() <= 0.5).sum()), "effects": len(T),
    "meridian_saving_per_year_usd": round(kg_mer * (0.36 - 0.29)),
    "meridian_chargebacks_per_year_usd": round(cb_mer),
    "meridian_chargebacks_if_blue_anchor_usd": round(cb_mer_if),
    "peak_buffer_days": round(est["peak"], 1),
}
(CLEAN / "delivery_metrics.json").write_text(json.dumps(metrics, indent=2))
print(metrics)
