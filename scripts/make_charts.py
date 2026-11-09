"""
Larkspur Home Textiles: README / site charts.

Input : data_clean/powerbi/*.csv (scripts/export_powerbi.py) and the analysis outputs in data_clean/
Output: images/chart_*.png and a smaller copy in site/img/
Run   : python scripts/make_charts.py   (from the project root)
"""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, PercentFormatter
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
P, C = ROOT / "data_clean" / "powerbi", ROOT / "data_clean"
IMG, SITE = ROOT / "images", ROOT / "site" / "img"
IMG.mkdir(exist_ok=True); SITE.mkdir(parents=True, exist_ok=True)
# the dashboard's palette: terracotta lead, industrial blue-grey, warm taupe text, green / amber / red status
INDIGO, PRIMARY, COMPARE, GOOD, WARN, BAD, SLATE, SKY = ("#2C2A29", "#D96B43", "#C9BFB5", "#2E7D32", "#D9A036", "#C62828",
                                                         "#8C7A6B", "#3B6E8C")
plt.rcParams.update({"font.family": "Segoe UI", "font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.titleweight": "bold", "axes.titlesize": 12, "axes.titlelocation": "left",
                     "axes.edgecolor": "#E5E0DA"})
pct = PercentFormatter(1.0, decimals=0)


def save(fig, name):
    fig.tight_layout()
    fig.savefig(IMG / name, dpi=150)
    fig.savefig(SITE / name, dpi=110)
    plt.close(fig)
    print(f"wrote images/{name}")


O = pd.read_csv(P / "fact_order_line.csv", parse_dates=["ship_date"])
sku = pd.read_csv(P / "dim_sku.csv")

# 1. on time by carrier, with the price per kg -----------------------------------------------
car = pd.read_csv(P / "dim_carrier.csv")
g = O.dropna(subset=["on_time"]).groupby("carrier").on_time.mean().rename("on_time").reset_index().merge(car, on="carrier")
g = g.sort_values("on_time")
fig, ax = plt.subplots(figsize=(9, 3.8))
colors = [BAD if v < 0.8 else (WARN if v < 0.9 else GOOD) for v in g.on_time]
ax.barh(g.carrier, g.on_time, color=colors)
for y, (v, r, m) in enumerate(zip(g.on_time, g.usd_per_kg, g.ship_mode)):
    ax.text(v + 0.01, y, f"{v:.0%}   {m} · ${r:.2f}/kg", va="center", fontsize=9, color=SLATE)
ax.set_xlim(0, 1.25); ax.set_xticks(np.arange(0, 1.01, 0.2)); ax.xaxis.set_major_formatter(pct)
ax.set_title("On time by carrier: the cheapest sea line is late half the time")
save(fig, "chart_carriers.png")

# 2. sea on time by month: the Oct-Dec congestion the quotes ignore --------------------------
sea = O[(O.ship_mode == "Sea") & O.on_time.notna()].copy()
sea["m"] = sea.ship_date.dt.to_period("M").dt.to_timestamp()
s = sea.groupby("m").on_time.mean()
fig, ax = plt.subplots(figsize=(9, 3.6))
ax.plot(s.index, s.values, color=PRIMARY, lw=2.2)
for y in (2023, 2024, 2025):
    ax.axvspan(pd.Timestamp(f"{y}-10-01"), pd.Timestamp(f"{y}-12-31"), color=BAD, alpha=0.08, lw=0)
ax.yaxis.set_major_formatter(pct); ax.set_ylim(0, 1)
ax.set_title("Sea freight on time by month: every October to December the quotes are missed")
ax.text(pd.Timestamp("2023-10-05"), 0.06, "Oct–Dec", color=BAD, fontsize=9)
save(fig, "chart_sea_season.png")

# 3. weeks short by production line under each rule (2025 replay) -----------------------------
bl = pd.read_csv(C / "policy_backtest_by_line.csv")
pv = bl.pivot(index="production_line", columns="policy", values="weeks_short_pct") / 100
lt = sku.groupby("production_line").lead_time_wk.mean()
fig, ax = plt.subplots(figsize=(9, 3.8))
x = np.arange(len(pv)); w = 0.38
ax.bar(x - w / 2, pv["Current rule"], w, color=COMPARE, label="Current rule (6 weeks of demand for every SKU)")
ax.bar(x + w / 2, pv["Recommended"], w, color=PRIMARY, label="Recommended (lead-time demand + safety stock)")
for i, (a, b) in enumerate(zip(pv["Current rule"], pv["Recommended"])):
    ax.text(i - w / 2, a + 0.008, f"{a:.0%}", ha="center", fontsize=9); ax.text(i + w / 2, b + 0.008, f"{b:.0%}", ha="center", fontsize=9)
ax.set_xticks(x, [f"{l}\n{lt[l]:.0f}-week lead time" for l in pv.index])
ax.yaxis.set_major_formatter(pct); ax.legend(frameon=False, fontsize=9, loc="upper left")
ax.set_title("2025 replay: share of SKU-weeks short of stock, by production line")
save(fig, "chart_policy.png")

# 4. profit leak waterfall, 2025 ---------------------------------------------------------------
lk = pd.read_csv(P / "leaks.csv")
lk = lk[(lk.year == 2025) & lk.kind.isin(["bridge", "cost"])].sort_values("step")
vals = lk.amount.to_numpy() / 1e6
start = np.concatenate([[0], np.cumsum(vals)[:-1]])
fig, ax = plt.subplots(figsize=(9, 4))
for i, (s0, v, name) in enumerate(zip(start, vals, lk.item)):
    ax.bar(i, v, bottom=s0 if i else 0, color=SKY if v > 0 else BAD)
    if i:
        ax.text(i, s0 + v - 0.15, f"{v:+.2f}", ha="center", va="top", fontsize=8.5, color=BAD)
    else:
        ax.text(i, v + 0.15, f"{v:.1f}", ha="center", fontsize=8.5)
total = vals.sum()
ax.bar(len(vals), total, color=INDIGO); ax.text(len(vals), total + 0.15, f"{total:.1f}", ha="center", fontsize=8.5)
ax.set_xticks(range(len(vals) + 1), [n.replace(" ", "\n", 1) for n in lk.item] + ["Contri-\nbution"], fontsize=8.5)
ax.set_ylabel("$M"); ax.set_ylim(22, vals[0] * 1.04)
ax.set_title(r"2025: gross margin to contribution (\$M, axis starts at \$22M)")
save(fig, "chart_leaks.png")

# 5. defect returns by production line --------------------------------------------------------
R = pd.read_csv(P / "fact_return.csv").merge(sku[["sku", "production_line"]], on="sku")
d = R[R.reason.str.startswith("Defect")].groupby("production_line").size()
n = O[O.status == "Shipped"].merge(sku[["sku", "production_line"]], on="sku").groupby("production_line").size()
rate = (d / n).sort_index()
fig, ax = plt.subplots(figsize=(9, 3.4))
ax.bar(rate.index, rate.values, color=[BAD if v > 0.02 else SKY for v in rate.values], width=0.55)
for i, v in enumerate(rate.values):
    ax.text(i, v + 0.0007, f"{v:.2%}", ha="center", fontsize=9)
ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=1))
ax.set_title("Defect returns per shipped line: line L3's shade matching")
save(fig, "chart_defects.png")
