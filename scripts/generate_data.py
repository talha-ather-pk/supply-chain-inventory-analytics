"""
Synthetic operations data for "Larkspur Home Textiles" (fictional home-textile manufacturer and exporter).

150 finished-goods SKUs, 90 retail customers in 5 markets, weekly demand Jan 2023 - Dec 2025. A week-by-week
inventory simulation runs the company's current reorder rule (reorder point = 6 weeks of demand for every SKU),
so stock-outs, backorders, cancelled lines and late shipments come out of the operation itself. Transit times
follow known effects (carrier, market, peak season), so every finding can be checked against the truth.

Outputs (data_raw/):
  products.csv            SKU master (with the mess real master data has)
  customers.csv           retailers: market, channel, late-delivery penalty
  order_lines.csv         ERP order-line extract: dates, mode, carrier, ship / delivery dates, status
  production_orders.csv   replenishment orders: ordered, due and received week
  inventory_weekly.csv    weekly stock snapshot per SKU
  cycle_counts.csv        monthly warehouse counts: system vs counted quantity
  returns.csv             customer returns with reason
  freight_rates.csv       $ per kg by mode and carrier
  true_effects.json       the true transit effects, policy, defect rates and injected-problem counts

Run: python scripts/generate_data.py   (seeded; ~30 s)
"""
from collections import deque
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data_raw"
RAW.mkdir(exist_ok=True)
rng = np.random.default_rng(6)

START = pd.Timestamp("2023-01-02")              # a Monday
W = 156                                         # weeks: 2 Jan 2023 - 28 Dec 2025
weeks = pd.date_range(START, periods=W, freq="7D")
woy = weeks.isocalendar().week.to_numpy().astype(int)
year_frac = (weeks - START).days.to_numpy() / 365.25

# ---------------------------------------------------------------- products
CATS = {  # category: (code, price range, cost share, weight kg, demand cv range, season peak week, peak size, fabrics)
    "Bed sheets":    ("BS", (28, 64), 0.58, 1.6, (0.15, 0.35), 46, 0.30, ["Cotton percale", "Cotton sateen", "Poly-cotton", "Linen"]),
    "Duvet covers":  ("DC", (34, 82), 0.57, 1.9, (0.25, 0.45), 46, 0.35, ["Cotton percale", "Cotton sateen", "Linen"]),
    "Towels":        ("TW", (6, 22), 0.52, 0.7, (0.15, 0.30), 24, 0.25, ["Terry", "Organic terry"]),
    "Bathrobes":     ("BR", (26, 48), 0.55, 1.3, (0.40, 0.65), 48, 0.55, ["Terry", "Waffle"]),
    "Kitchen linen": ("KL", (4, 12), 0.50, 0.3, (0.20, 0.40), 46, 0.20, ["Cotton", "Linen"]),
    "Curtains":      ("CU", (22, 54), 0.56, 2.2, (0.45, 0.75), 14, 0.40, ["Poly-cotton", "Linen", "Blackout"]),
}
SIZES = {"Bed sheets": ["Twin", "Full", "Queen", "King"], "Duvet covers": ["Full", "Queen", "King"],
         "Towels": ["Hand", "Bath", "Bath sheet"], "Bathrobes": ["S/M", "L/XL"], "Kitchen linen": ["Set of 3", "Set of 6"],
         "Curtains": ["52x84", "52x96"]}
COLOURS = ["White", "Ivory", "Stone", "Indigo", "Sage", "Charcoal", "Blush", "Navy"]
N_SKU = 150
cat_share = {"Bed sheets": 38, "Duvet covers": 24, "Towels": 34, "Bathrobes": 14, "Kitchen linen": 20, "Curtains": 20}
cats = np.repeat(list(cat_share), list(cat_share.values()))
rng.shuffle(cats)
rows = []
for i, c in enumerate(cats):
    code, (plo, phi), cshare, wt, (cvlo, cvhi), _, _, fabrics = CATS[c]
    fabric = rng.choice(fabrics)
    size = rng.choice(SIZES[c]); colour = rng.choice(COLOURS)
    price = round(float(rng.uniform(plo, phi)) * (1.25 if fabric in ("Linen", "Organic terry") else 1.0), 2)
    rows.append({"sku": f"{code}-{i + 1:03d}", "product_name": f"{fabric} {c.lower()[:-1] if c.endswith('s') else c.lower()}, {size}, {colour}",
                 "category": c, "fabric": fabric, "size": size, "colour": colour, "list_price": price,
                 "unit_cost": round(price * cshare * rng.uniform(0.94, 1.06), 2),
                 "unit_weight_kg": round(wt * rng.uniform(0.8, 1.25) * (1.4 if size in ("King", "Bath sheet") else 1.0), 2),
                 "cv": rng.uniform(cvlo, cvhi)})
P = pd.DataFrame(rows)
P["base_units"] = np.round(np.exp(rng.normal(5.3, 0.95, N_SKU)))          # median ~200 units a week
P.loc[P.category.isin(["Bathrobes", "Curtains"]), "base_units"] *= 0.5
P["production_line"] = rng.choice(["L1", "L2", "L3", "L4"], N_SKU, p=[0.3, 0.3, 0.22, 0.18])
LINE_LT = {"L1": 3, "L2": 4, "L3": 5, "L4": 7}                             # production lead time, weeks
P["lead_time_wk"] = P.production_line.map(LINE_LT) + rng.integers(-1, 2, N_SKU)
# launches (Apr 2024) and discontinued lines (Mar 2025)
launch_idx = rng.choice(N_SKU, 15, replace=False)
rest = np.setdiff1d(np.arange(N_SKU), launch_idx)
disc_idx = rng.choice(rest, 10, replace=False)
P["launch_week"] = 0; P.loc[launch_idx, "launch_week"] = 65                 # 1 Apr 2024
P["end_week"] = W; P.loc[disc_idx, "end_week"] = 113                         # 3 Mar 2025
P["status"] = np.where(P.end_week < W, "Discontinued", "Active")

# ---------------------------------------------------------------- weekly demand (units)
D = np.zeros((W, N_SKU), dtype=int)
for s, r in P.iterrows():
    _, _, _, _, _, peak, size, _ = CATS[r.category]
    dist = np.minimum(np.abs(woy - peak), 52 - np.abs(woy - peak))
    season = 1 + size * np.exp(-(dist / 4.0) ** 2) + 0.12 * np.exp(-(np.minimum(woy, 53 - woy) / 1.5) ** 2)
    trend = 1 + 0.07 * year_frac
    mu = r.base_units * season * trend
    sig = np.sqrt(np.log(1 + r.cv ** 2))
    d = rng.lognormal(np.log(mu) - sig ** 2 / 2, sig)
    d[: r.launch_week] = 0; d[r.end_week:] = 0
    D[:, s] = np.round(d)

# ---------------------------------------------------------------- customers
PRE = ["Alder", "Brookline", "Cedarwood", "Dunmore", "Elmstead", "Fernhill", "Glenrock", "Harbourside", "Ivybridge",
       "Juniper", "Kestrel", "Lindenfield", "Marlow", "Northgate", "Oakhaven", "Pinecrest", "Quayside", "Rowanberry",
       "Saltmarsh", "Thornbury", "Upton", "Valemont", "Westbrook", "Yarrowdale"]
SUF = ["Home", "Living", "Stores", "Interiors", "Linen Co", "Retail Group", "Home & Bath", "Outlet"]
names = rng.permutation([f"{a} {b}" for a in PRE for b in SUF])[:90]
MARKETS = ["USA", "UK", "EU", "GCC", "Domestic"]
C = pd.DataFrame({"customer_id": [f"C{i + 1:03d}" for i in range(90)], "customer_name": names,
                  "market": rng.choice(MARKETS, 90, p=[0.3, 0.2, 0.25, 0.12, 0.13]),
                  "channel": rng.choice(["Big-box", "Department store", "Online", "Wholesale"], 90)})
C["late_penalty_rate"] = C.channel.map({"Big-box": 0.03, "Department store": 0.02, "Online": 0.0, "Wholesale": 0.0})
C["cancel_if_short"] = C.channel.map({"Big-box": 0.30, "Department store": 0.35, "Online": 0.60, "Wholesale": 0.20})
C["discount"] = C.channel.map({"Big-box": 0.12, "Department store": 0.08, "Online": 0.05, "Wholesale": 0.15})
C["weight"] = rng.pareto(1.6, 90) + 0.3

# ---------------------------------------------------------------- transit model (the truth)
SEA_BASE = {"USA": 26, "UK": 23, "EU": 21, "GCC": 13}
CUSTOMS = {"USA": 0.0, "UK": 0.3, "EU": 0.6, "GCC": 1.0, "Domestic": 0.0}
CARRIERS = {"Sea": {"Blue Anchor Line": 0.0, "Coral Shipping": 0.5, "Meridian Freight": 3.0},
            "Air": {"SkyBridge Cargo": 0.0, "Falcon Air Freight": 1.0},
            "Road": {"Highway Express": 0.0, "Metro Haulers": 0.8}}
CARRIER_SHARE = {"Sea": [0.4, 0.3, 0.3], "Air": [0.6, 0.4], "Road": [0.55, 0.45]}
RATE = {"Blue Anchor Line": 0.36, "Coral Shipping": 0.34, "Meridian Freight": 0.29, "SkyBridge Cargo": 3.9,
        "Falcon Air Freight": 3.6, "Highway Express": 0.24, "Metro Haulers": 0.22}      # $ per kg
PEAK_SEA = 3.5                                  # Oct-Dec departures: port congestion, days
BUFFER = {"Sea": 5, "Air": 2, "Road": 2}        # quoted transit = base + buffer
PROCESS_QUOTED = 3                              # days from order to dispatch in the quote


def base_transit(mode, market):
    return {"Sea": SEA_BASE.get(market, 0), "Air": 3 if market == "GCC" else 5, "Road": 3}[mode]


# ---------------------------------------------------------------- order lines (before fulfilment)
lines = []
cw = C.weight.to_numpy() / C.weight.sum()
for w in range(W):
    for s in np.nonzero(D[w])[0]:
        k = int(min(12, 1 + rng.poisson(D[w, s] / 150)))
        qty = np.maximum(1, np.round(rng.dirichlet(np.ones(k) * 2) * D[w, s])).astype(int)
        cust = rng.choice(90, k, p=cw)
        day = rng.integers(0, 6, k)
        for q, c_, d_ in zip(qty, cust, day):
            lines.append((w, s, c_, int(q), int(d_)))
L = pd.DataFrame(lines, columns=["w", "s", "c", "qty", "day"]).sort_values(["s", "w", "day"]).reset_index(drop=True)
L["order_date"] = weeks[L.w] + pd.to_timedelta(L.day, "D")

# ---------------------------------------------------------------- inventory simulation, current policy
ROP_WEEKS, Q_WEEKS = 6, 8                       # current rule: reorder at 6 weeks of demand, order 8 weeks
plan_rate = P.base_units.to_numpy() * 1.0       # planners use the 2022 average, never updated
P["rop_current"] = np.round(ROP_WEEKS * plan_rate)
P["order_qty_current"] = np.round(Q_WEEKS * plan_rate)
L4_DELAY_P = 0.35                               # truth: L4 yarn supplier delays a third of its orders by 1-3 weeks

cust_cancel = C.cancel_if_short.to_numpy()
status = np.empty(len(L), dtype=object); ship_w = np.full(len(L), -1); backordered = np.zeros(len(L), bool)
snap, pos = [], []
inv_rows = []
for s in range(N_SKU):
    r = P.iloc[s]
    lt = int(r.lead_time_wk); rop = r.rop_current; q = r.order_qty_current
    arrivals = np.zeros(W + 20)
    on_hand = 0 if r.launch_week > 0 else rop + q / 2
    if r.launch_week > 0:
        arrivals[r.launch_week] += q + rop          # launch stock
        on_order = q + rop
    else:
        on_order = 0
    backlog = deque()
    idx_s = np.nonzero(L.s.to_numpy() == s)[0]
    by_week = pd.Series(idx_s).groupby(L.w.to_numpy()[idx_s]).apply(list).to_dict()
    for w in range(W):
        rec = arrivals[w]; on_hand += rec; on_order -= rec
        shipped = lost = 0
        while backlog and on_hand >= L.qty.iat[backlog[0]]:
            i = backlog.popleft(); on_hand -= L.qty.iat[i]; ship_w[i] = w; status[i] = "Shipped"; shipped += L.qty.iat[i]
        for i in by_week.get(w, []):
            qi = L.qty.iat[i]
            if not backlog and on_hand >= qi:
                on_hand -= qi; ship_w[i] = w; status[i] = "Shipped"; shipped += qi
            elif rng.random() < cust_cancel[L.c.iat[i]]:
                status[i] = "Cancelled - stock-out"; lost += qi
            else:
                backlog.append(i); backordered[i] = True
        while backlog and w - L.w.iat[backlog[0]] > 6:          # customers give up after 6 weeks
            i = backlog.popleft(); status[i] = "Cancelled - stock-out"; lost += L.qty.iat[i]
        bl = sum(L.qty.iat[i] for i in backlog)
        ordering = r.launch_week <= w < r.end_week       # discontinued lines: orders already placed still arrive
        if ordering and on_hand + on_order - bl <= rop:
            delay = int(rng.integers(1, 4)) if (r.production_line == "L4" and rng.random() < L4_DELAY_P) else 0
            arrivals[w + lt + delay] += q; on_order += q
            pos.append((s, w, int(q), w + lt, w + lt + delay))
        inv_rows.append((w, s, int(D[w, s]), int(shipped), int(lost), int(rec), int(on_hand), int(bl), int(on_order)))
    for i in backlog:                                    # still waiting at the end of 2025
        status[i] = "Open backorder"

L["status"] = status
L["backordered"] = backordered
L["ship_w"] = ship_w
inv = pd.DataFrame(inv_rows, columns=["w", "s", "demand_units", "shipped_units", "lost_units", "received_units",
                                      "on_hand_units", "backlog_units", "on_order_units"])

# ---------------------------------------------------------------- shipping, transit, delivery
L = L.merge(C[["market", "channel", "late_penalty_rate", "discount"]].reset_index().rename(columns={"index": "c"}), on="c")
L = L.merge(P[["sku", "list_price", "unit_cost", "unit_weight_kg", "production_line", "category", "fabric"]]
            .reset_index().rename(columns={"index": "s"}), on="s")
n = len(L)
export = L.market != "Domestic"
mode = np.where(~export, "Road", np.where(rng.random(n) < np.where(L.channel == "Online", 0.35, 0.10), "Air", "Sea"))
sh = L.status == "Shipped"
wait_wk = np.where(sh, L.ship_w - L.w, 0)
# expediting: backordered export lines for penalty customers that waited 2+ weeks are switched from sea to air
expedite = sh & (mode == "Sea") & (wait_wk >= 2) & (L.late_penalty_rate > 0)
mode = np.where(expedite, "Air", mode)
L["ship_mode"] = mode
L["expedited"] = expedite
carrier = np.empty(n, dtype=object)
for m, opts in CARRIERS.items():
    idx = np.nonzero(mode == m)[0]
    carrier[idx] = rng.choice(list(opts), len(idx), p=CARRIER_SHARE[m])
L["carrier"] = carrier
proc = rng.integers(1, 4, n)                          # 1-3 days to pick and dispatch
ship_date = np.where(L.backordered & sh, weeks[np.clip(L.ship_w, 0, W - 1)] + pd.to_timedelta(proc, "D"),
                     L.order_date + pd.to_timedelta(proc, "D"))
ship_date = pd.to_datetime(ship_date)
base = np.array([base_transit(m, k) for m, k in zip(mode, L.market)], float)
carrier_eff = np.array([CARRIERS[m][c] for m, c in zip(mode, carrier)])
peak = np.where((mode == "Sea") & ship_date.month.isin([10, 11, 12]), PEAK_SEA, 0.0)
customs = np.where(mode == "Road", 0.0, L.market.map(CUSTOMS).to_numpy())
noise = np.where(mode == "Sea", rng.gamma(2, 1.2, n), rng.gamma(2, 0.4, n))
transit = np.round(base + carrier_eff + peak + customs + noise)
quoted = np.array([base_transit("Sea" if (m == "Air" and e) else m, k) + BUFFER["Sea" if (m == "Air" and e) else m]
                   for m, k, e in zip(mode, L.market, expedite)])        # the quote was made for the original mode
L["promised_date"] = L.order_date + pd.to_timedelta(PROCESS_QUOTED + quoted, "D")
L["ship_date"] = ship_date.where(sh.to_numpy())
L["delivery_date"] = (ship_date + pd.to_timedelta(transit, "D")).where(sh.to_numpy())
L["unit_price"] = np.round(L.list_price * (1 - L.discount) * rng.choice([1, 1, 1, 0.9], n), 2)   # 1 in 4 lines on promo
L["freight_cost"] = np.where(sh, np.round(L.qty * L.unit_weight_kg * L.carrier.map(RATE), 2), 0.0)
L["order_id"] = "SO-" + (L.groupby(["c", "w"]).ngroup() + 100001).astype(str)

# ---------------------------------------------------------------- returns (the truth: line L3 has a shade-matching fault)
DEFECT = {"L1": 0.008, "L2": 0.008, "L3": 0.035, "L4": 0.010}
DAMAGE = {"Sea": 0.004, "Air": 0.002, "Road": 0.006}
p_def = L.production_line.map(DEFECT) * np.where(L.fabric == "Linen", 1.5, 1.0)
u = rng.random(n)
reason = np.where(~sh, None, np.where(u < p_def, "Defect - shade / stitching",
                 np.where(u < p_def + L.ship_mode.map(DAMAGE), "Damaged in transit",
                          np.where(u < p_def + L.ship_mode.map(DAMAGE) + 0.002, "Wrong item shipped", None))))
R = L[pd.notna(reason)].copy(); R["reason"] = reason[pd.notna(reason)]
share = np.where(R.reason == "Wrong item shipped", 1.0, rng.uniform(0.1, 0.6, len(R)))
R["return_qty"] = np.maximum(1, np.round(R.qty * share)).astype(int)
R["return_date"] = R.delivery_date + pd.to_timedelta(rng.integers(7, 31, len(R)), "D")
R = R[R.return_date <= "2025-12-31"]
R["refund_value"] = np.round(R.return_qty * R.unit_price, 2)
R["return_freight"] = np.round(R.freight_cost * R.return_qty / R.qty * 0.5, 2)
R = R.reset_index(drop=True)
R["return_id"] = [f"RT-{i + 1:05d}" for i in range(len(R))]

# ---------------------------------------------------------------- cycle counts (truth: mezzanine zone C is badly kept)
rank = P.base_units.rank(ascending=False)
P["zone"] = np.where(rank <= 35, "A - fast pick", np.where(rank <= 105, "B - bulk rack", "C - mezzanine"))
ERR = {"A - fast pick": 0.04, "B - bulk rack": 0.07, "C - mezzanine": 0.20}
oh = inv.pivot(index="w", columns="s", values="on_hand_units").to_numpy()
cc = []
for m_start in pd.date_range("2023-01-01", "2025-12-01", freq="MS"):
    w = int(np.searchsorted(weeks, m_start + pd.Timedelta(days=9)))
    for s in rng.choice(N_SKU, 20, replace=False):
        sys_q = int(oh[w, s])
        if sys_q == 0:
            continue
        wrong = rng.random() < ERR[P.zone.iat[s]]
        err = int(np.sign(rng.normal()) * max(1, round(sys_q * rng.uniform(0.01, 0.08)))) if wrong else 0
        cc.append((weeks[w] + pd.Timedelta(days=int(rng.integers(0, 5))), P.sku.iat[s], P.zone.iat[s], sys_q, sys_q + err))
CC = pd.DataFrame(cc, columns=["count_date", "sku", "zone", "system_qty", "counted_qty"])

# ---------------------------------------------------------------- production orders
PO = pd.DataFrame(pos, columns=["s", "w_order", "qty", "w_due", "w_received"])
PO = PO.merge(P[["sku", "production_line"]].reset_index().rename(columns={"index": "s"}), on="s")
PO["po_id"] = [f"PO-{i + 1:05d}" for i in range(len(PO))]
PO["order_date"] = weeks[PO.w_order].date
PO["due_date"] = (START + pd.to_timedelta(PO.w_due * 7, "D")).dt.date
PO["received_date"] = (START + pd.to_timedelta(PO.w_received * 7, "D")).dt.date
PO.loc[PO.w_received >= W, "received_date"] = None            # still open at year end

# ================================================================= write, with the mess real extracts have
INJ = {}
OL = L[["order_id", "c", "sku", "order_date", "qty", "unit_price", "ship_mode", "carrier", "expedited", "promised_date",
        "ship_date", "delivery_date", "freight_cost", "status"]].copy()
OL.insert(2, "customer_id", C.customer_id.to_numpy()[OL.pop("c")])
OL = OL.sort_values(["order_date", "order_id", "sku"]).reset_index(drop=True)
OL.insert(1, "line_no", OL.groupby("order_id").cumcount() + 1)
for c_ in ["order_date", "promised_date", "ship_date", "delivery_date"]:
    OL[c_] = pd.to_datetime(OL[c_]).dt.strftime("%Y-%m-%d")
OL = OL.astype({"expedited": int})
OL = OL.astype(object)

# 1. GCC customers send orders by EDI with dd/mm/yyyy dates
gcc = OL.customer_id.isin(C.customer_id[C.market == "GCC"])
OL.loc[gcc, "order_date"] = pd.to_datetime(OL.loc[gcc, "order_date"]).dt.strftime("%d/%m/%Y")
INJ["order_date_ddmmyyyy (GCC EDI feed)"] = int(gcc.sum())
# 2. ship mode typed by hand
i = rng.choice(len(OL), 1800, replace=False)
OL.loc[i, "ship_mode"] = [{"Sea": rng.choice(["SEA", "sea ", "Ocean"]), "Air": rng.choice(["AIR", "air"]),
                           "Road": rng.choice(["ROAD", "Truck"])}[m] for m in OL.loc[i, "ship_mode"]]
INJ["ship_mode_variants"] = 1800
# 3. proof of delivery not uploaded
dl = np.nonzero(OL.delivery_date.notna().to_numpy())[0]
i = rng.choice(dl, 900, replace=False); OL.loc[i, "delivery_date"] = None
INJ["delivery_date_missing (POD not uploaded)"] = 900
# 4. customer ids with stray case / spaces
i = rng.choice(len(OL), 600, replace=False)
OL.loc[i, "customer_id"] = [rng.choice([x.lower(), f" {x}", f"{x} "]) for x in OL.loc[i, "customer_id"]]
INJ["customer_id_case_spaces"] = 600
# 5. free samples booked at zero price
i = rng.choice(np.nonzero((OL.status == "Shipped").to_numpy())[0], 200, replace=False)
OL.loc[i, "unit_price"] = 0.0
INJ["zero_price_samples"] = 200
# 6. credit notes booked as negative order lines
neg = OL.sample(120, random_state=1).copy(); neg["qty"] = -neg.qty.astype(int)
neg["order_id"] = "CN-" + neg.order_id.str[3:]; neg["status"] = "Credit note"
INJ["negative_qty_credit_notes"] = 120
# 7. test lines from the ERP go-live
test = OL.sample(15, random_state=2).copy(); test["sku"] = "TEST-001"; test["order_id"] = "SO-TEST"
INJ["test_lines"] = 15
# 8. duplicated lines from a re-run export
dup = OL.sample(350, random_state=3)
INJ["duplicate_lines"] = 350
OL = pd.concat([OL, neg, test, dup]).sample(frac=1, random_state=4).reset_index(drop=True)

PR = P[["sku", "product_name", "category", "fabric", "size", "colour", "list_price", "unit_cost", "unit_weight_kg",
        "production_line", "lead_time_wk", "zone", "status", "rop_current", "order_qty_current"]].copy().astype(object)
PR["launch_date"] = weeks[P.launch_week].date
PR["discontinued_date"] = [weeks[e].date() if e < W else None for e in P.end_week]
# 9. category spelled several ways in the master
i = rng.choice(np.nonzero((PR.category == "Bed sheets").to_numpy())[0], 8, replace=False)
PR.loc[i, "category"] = rng.choice(["Bedsheets", "Bed Sheet", "bed sheets"], 8)
INJ["category_spelling_variants"] = 8
# 10. unit cost missing (new SKUs not costed yet)
i = rng.choice(launch_idx, 3, replace=False); PR.loc[i, "unit_cost"] = None
INJ["unit_cost_missing"] = 3
# 11. counts keyed in but never finished
i = rng.choice(len(CC), 40, replace=False); CC = CC.astype(object); CC.loc[i, "counted_qty"] = None
INJ["cycle_count_not_finished"] = 40

PR.to_csv(RAW / "products.csv", index=False)
C[["customer_id", "customer_name", "market", "channel", "late_penalty_rate"]].to_csv(RAW / "customers.csv", index=False)
OL.to_csv(RAW / "order_lines.csv", index=False)
PO[["po_id", "sku", "production_line", "order_date", "qty", "due_date", "received_date"]].to_csv(RAW / "production_orders.csv", index=False)
INV = inv.assign(week_start=weeks[inv.w].date, sku=P.sku.to_numpy()[inv.s]).drop(columns=["w", "s"])
INV[["week_start", "sku", "demand_units", "shipped_units", "lost_units", "received_units", "on_hand_units",
     "backlog_units", "on_order_units"]].to_csv(RAW / "inventory_weekly.csv", index=False)
CC["count_date"] = pd.to_datetime(CC.count_date).dt.date
CC.to_csv(RAW / "cycle_counts.csv", index=False)
R["order_date"] = R.order_date.dt.date; R["return_date"] = R.return_date.dt.date
R[["return_id", "order_id", "sku", "return_date", "reason", "return_qty", "refund_value", "return_freight"]].to_csv(
    RAW / "returns.csv", index=False)
pd.DataFrame([{"ship_mode": m, "carrier": c, "usd_per_kg": RATE[c]} for m, o in CARRIERS.items() for c in o]).to_csv(
    RAW / "freight_rates.csv", index=False)

truth = {
    "transit_days": {"sea_base": SEA_BASE, "air_base": {"GCC": 3, "other": 5}, "road_base": 3, "customs": CUSTOMS,
                     "carrier_extra_days": CARRIERS, "peak_sea_oct_dec": PEAK_SEA,
                     "noise_mean": {"Sea": 2.4, "Air": 0.8, "Road": 0.8}, "quote_buffer": BUFFER,
                     "quoted_processing_days": PROCESS_QUOTED},
    "policy_current": {"reorder_point_weeks": ROP_WEEKS, "order_qty_weeks": Q_WEEKS, "plan_rate": "2022 average, not updated",
                       "trend_per_year": 0.07},
    "production": {"lead_time_weeks": LINE_LT, "L4_delay_probability": L4_DELAY_P, "L4_delay_weeks": "1-3"},
    "returns": {"defect_rate_by_line": DEFECT, "linen_multiplier": 1.5, "damage_rate_by_mode": DAMAGE, "wrong_item": 0.002},
    "cycle_count_error_rate_by_zone": ERR,
    "customer_cancel_if_short": C.groupby("channel").cancel_if_short.first().to_dict(),
    "injected": INJ,
}
(RAW / "true_effects.json").write_text(json.dumps(truth, indent=2, default=str))
sh_ = L.status == "Shipped"
late = (L.delivery_date > L.promised_date) & sh_
print(f"lines {len(L):,}  shipped {sh_.mean():.1%}  cancelled {(L.status == 'Cancelled - stock-out').mean():.1%}  "
      f"backordered {L.backordered.mean():.1%}  late (of shipped) {late.sum() / sh_.sum():.1%}  expedited {L.expedited.sum():,}")
print(f"revenue ${(L.qty * L.unit_price)[sh_].sum() / 1e6:,.1f}M  returns {len(R):,}  POs {len(PO):,}  counts {len(CC):,}")
print("late by mode:", (late[sh_].groupby(L.ship_mode[sh_]).mean().round(3)).to_dict())
print("late by carrier:", (late[sh_].groupby(L.carrier[sh_]).mean().round(3)).to_dict())
print("late if backordered:", late[sh_].groupby(L.backordered[sh_]).mean().round(3).to_dict())
