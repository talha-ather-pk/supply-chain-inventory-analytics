"""
Inventory policy: ABC-XYZ classes, a recommended reorder point per SKU, and a 2025 backtest against the current rule.

Current rule: reorder point = 6 weeks of (old) average demand for every SKU, order 8 weeks.
Recommended:  reorder point = demand over the lead time + safety stock, where
              safety stock = z(service level) x sqrt(L x sd_d^2 + d^2 x sd_L^2)
              d, sd_d   = mean and sd of weekly demand in 2024 (the latest full year)
              L, sd_L   = planned lead time and the spread of actual receipt delays by production line, 2023-24
              service level by class: A 98%, B 95%, C 90% (Z items one step lower to keep stock sensible)
              order quantity unchanged (8 weeks), so the test isolates the reorder point.
Everything is estimated on 2023-24 only. 2025 is then replayed twice from the same starting stock, with the same
actual weekly demand and the same supplier delays: once with each rule.

Outputs: data_clean/sku_policy.csv, data_clean/policy_backtest.csv, data_clean/policy_backtest_by_line.csv,
         data_clean/policy_weekly.csv, data_clean/inventory_metrics.json
Run: python scripts/inventory.py
"""
from pathlib import Path
import json
import sqlite3
import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data_clean"
rng = np.random.default_rng(11)
with sqlite3.connect(CLEAN / "larkspur.db") as con:
    P = pd.read_sql_query("SELECT * FROM products", con)
    INV = pd.read_sql_query("SELECT * FROM inventory_weekly", con, parse_dates=["week_start"])
    PO = pd.read_sql_query("SELECT * FROM production_orders", con, parse_dates=["order_date", "due_date", "received_date"])
    O = pd.read_sql_query("SELECT sku, order_date, revenue, unit_price, unit_cost, status FROM order_lines", con,
                          parse_dates=["order_date"])

# ------------------------------------------------------------------ classes from 2023-24
hist = O[O.order_date < "2025-01-01"]
rev = hist.groupby("sku").revenue.sum().reindex(P.sku, fill_value=0).sort_values(ascending=False)
cum = rev.cumsum() / rev.sum()
abc = pd.Series(np.where(cum <= 0.80, "A", np.where(cum <= 0.95, "B", "C")), index=rev.index)
d24 = INV[(INV.week_start >= "2024-01-01") & (INV.week_start < "2025-01-01")].pivot(index="week_start", columns="sku",
                                                                                        values="demand_units")
active = d24.gt(0).sum() >= 26                                     # SKUs selling for at least half of 2024
mean_d, sd_d = d24.where(d24 > 0).mean(), d24.where(d24 > 0).std()
cv = sd_d / mean_d
xyz = pd.Series(np.where(cv < 0.25, "X", np.where(cv < 0.5, "Y", "Z")), index=cv.index)

S = P.set_index("sku")[["category", "production_line", "lead_time_wk", "unit_cost", "status", "rop_current",
                        "order_qty_current"]].copy()
S["abc"] = abc; S["xyz"] = xyz; S["mean_weekly_demand"] = mean_d.round(1); S["sd_weekly_demand"] = sd_d.round(1)
S["cv"] = cv.round(2)
# lead-time spread by production line, from 2023-24 receipts
po_h = PO[(PO.due_date < "2025-01-01") & PO.received_date.notna()].copy()
po_h["delay_wk"] = (po_h.received_date - po_h.due_date).dt.days / 7
delay_mean = po_h.groupby("production_line").delay_wk.mean()
delay_sd = po_h.groupby("production_line").delay_wk.std()
SL = {"A": 0.98, "B": 0.95, "C": 0.90}
S["service_level"] = S.abc.map(SL) - np.where(S.xyz == "Z", 0.02, 0.0)
S["z"] = norm.ppf(S.service_level)
L = S.lead_time_wk + S.production_line.map(delay_mean)
sdL = S.production_line.map(delay_sd)
S["safety_stock"] = np.round(S.z * np.sqrt(L * S.sd_weekly_demand ** 2 + S.mean_weekly_demand ** 2 * sdL ** 2))
S["rop_recommended"] = np.round(S.mean_weekly_demand * L + S.safety_stock)
S["rop_change_pct"] = ((S.rop_recommended / S.rop_current - 1) * 100).round(0)
S["class"] = S.abc + S.xyz
plan = S[active.reindex(S.index, fill_value=False) & (S.status == "Active")].copy()     # SKUs the policy applies to

# ------------------------------------------------------------------ 2025 backtest (same demand, same supplier delays)
W25 = INV[INV.week_start >= "2025-01-01"]
D = W25.pivot(index="week_start", columns="sku", values="demand_units")[plan.index].to_numpy()
start = INV[INV.week_start == "2024-12-30"].set_index("sku")          # first 2025 week's opening position
Wn, N = D.shape
lt = plan.lead_time_wk.to_numpy().astype(int)
l4 = (plan.production_line == "L4").to_numpy()
delays = np.where(l4[None, :] & (rng.random((Wn, N)) < 0.35), rng.integers(1, 4, (Wn, N)), 0)   # common random numbers
LOST_SHARE = 0.33                                                     # share of unmet demand customers cancel (2023-24 average)
cost = plan.unit_cost.to_numpy()
price = (O[O.status == "Shipped"].groupby("sku").unit_price.mean()).reindex(plan.index).to_numpy()


def replay(rop):
    q = plan.order_qty_current.to_numpy()
    on_hand = start.on_hand_units.reindex(plan.index).to_numpy().astype(float)
    backlog = start.backlog_units.reindex(plan.index).to_numpy().astype(float)
    arrivals = np.zeros((Wn + 20, N))
    # open production orders at the start arrive as they did in reality
    open_po = PO[(PO.order_date < "2025-01-01") & ((PO.received_date >= "2025-01-01") | PO.received_date.isna())]
    for _, r in open_po[open_po.sku.isin(plan.index)].iterrows():
        wk = int((r.received_date - pd.Timestamp("2024-12-30")).days // 7) if pd.notna(r.received_date) else Wn
        arrivals[max(wk, 0), plan.index.get_loc(r.sku)] += r.qty
    on_order = arrivals.sum(0)
    out = []
    for w in range(Wn):
        on_hand += arrivals[w]; on_order -= arrivals[w]
        need = backlog + D[w]
        ship = np.minimum(on_hand, need)
        short = need - ship
        lost = LOST_SHARE * np.minimum(short, D[w])
        backlog = short - lost
        on_hand -= ship
        order = (on_hand + on_order - backlog) <= rop
        for j in np.nonzero(order)[0]:
            arrivals[w + lt[j] + delays[w, j], j] += q[j]
        on_order += order * q
        out.append((ship, lost, on_hand.copy(), (short > 0)))
    ship = np.array([o[0] for o in out]); lost = np.array([o[1] for o in out])
    oh = np.array([o[2] for o in out]); shortw = np.array([o[3] for o in out])
    return ship, lost, oh, shortw


res = {}
for name, rop in [("Current rule", plan.rop_current.to_numpy()), ("Recommended", plan.rop_recommended.to_numpy())]:
    ship, lost, oh, shortw = replay(rop)
    res[name] = dict(ship=ship, lost=lost, oh=oh, short=shortw)

rows, by_line = [], []
for name, r in res.items():
    rows.append({"policy": name,
                 "fill_rate_pct": round(100 * r["ship"].sum() / D.sum(), 2),
                 "weeks_short_pct": round(100 * r["short"].mean(), 1),
                 "lost_units": int(r["lost"].sum()),
                 "lost_margin_usd": round(float((r["lost"] * (price - cost)).sum())),
                 "avg_stock_value_usd": round(float((r["oh"] * cost).sum(1).mean())),
                 "carrying_cost_usd": round(float((r["oh"] * cost).sum(1).mean() * 0.22))})
    for line in ["L1", "L2", "L3", "L4"]:
        m = (plan.production_line == line).to_numpy()
        by_line.append({"policy": name, "production_line": line,
                        "fill_rate_pct": round(100 * r["ship"][:, m].sum() / D[:, m].sum(), 2),
                        "weeks_short_pct": round(100 * r["short"][:, m].mean(), 1),
                        "avg_stock_value_usd": round(float((r["oh"][:, m] * cost[m]).sum(1).mean()))})
B = pd.DataFrame(rows); BL = pd.DataFrame(by_line)
B.to_csv(CLEAN / "policy_backtest.csv", index=False); BL.to_csv(CLEAN / "policy_backtest_by_line.csv", index=False)
weeks = W25.week_start.drop_duplicates().sort_values().to_numpy()
pd.DataFrame({"week_start": weeks,
              "stock_value_current": (res["Current rule"]["oh"] * cost).sum(1).round(0),
              "stock_value_recommended": (res["Recommended"]["oh"] * cost).sum(1).round(0),
              "lost_units_current": res["Current rule"]["lost"].sum(1).round(0),
              "lost_units_recommended": res["Recommended"]["lost"].sum(1).round(0)}).to_csv(CLEAN / "policy_weekly.csv", index=False)
S["in_policy"] = S.index.isin(plan.index).astype(int)
S.reset_index().to_csv(CLEAN / "sku_policy.csv", index=False)

# check: the current-rule replay should look like what actually happened in 2025
act = W25[W25.sku.isin(plan.index)]
actual_fill = 100 * act.shipped_units.sum() / act.demand_units.sum()
cur, rec = B.iloc[0], B.iloc[1]
metrics = {"skus_in_policy": len(plan), "class_counts": S["class"].value_counts().sort_index().to_dict(),
           "actual_2025_fill_pct": round(actual_fill, 2), "replay_current_fill_pct": cur.fill_rate_pct,
           "recommended_fill_pct": rec.fill_rate_pct,
           "lost_margin_saved_usd": int(cur.lost_margin_usd - rec.lost_margin_usd),
           "stock_value_change_usd": int(rec.avg_stock_value_usd - cur.avg_stock_value_usd),
           "carrying_cost_change_usd": int(rec.carrying_cost_usd - cur.carrying_cost_usd),
           "l4_delay_weeks_mean": round(float(delay_mean.get("L4", 0)), 2)}
(CLEAN / "inventory_metrics.json").write_text(json.dumps(metrics, indent=2))
print(S.groupby("class").size().to_dict())
print(B.to_string(index=False)); print(BL.to_string(index=False)); print(metrics)
