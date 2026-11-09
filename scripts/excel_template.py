"""
Excel reorder-alert template: live formulas, editable service levels, conditional formatting.

Sheets
  Summary   status counts, value of suggested orders and alerts by production line (all formulas)
  Stock     one row per active SKU: demand, lead time, stock position -> safety stock, reorder point, status,
            suggested order. Inputs are blue; everything else is a formula, so a planner can paste next week's
            stock figures and the alerts update.
  Settings  service level by ABC class, Z-item step-down, order cover in weeks, carrying rate
  Notes     what each column means

Stock position = 31 Dec 2025. Demand = 2025 weekly demand (mean and spread). Lead time = planned weeks plus the
average receipt delay of the production line; lead-time spread from production orders 2023-25.
Output: excel/Larkspur_reorder_alerts.xlsx
Run: python scripts/excel_template.py
"""
from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import CellIsRule, DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.table import Table, TableStyleInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "excel"; OUT.mkdir(exist_ok=True)
INDIGO, INK, SLATE, PALE = "22313A", "1E2A30", "5E6B73", "F4F1EA"   # the dashboard's "Mill floor" palette
RED, AMBER, GREEN = "F8D7D5", "FCE9C6", "D8F0E3"
H_FILL = PatternFill("solid", fgColor=INDIGO)
IN_FONT = Font(color="1D4ED8")                      # inputs in blue, the usual modelling convention
thin = Side(style="thin", color="D1D5DB")

with sqlite3.connect(ROOT / "data_clean" / "larkspur.db") as con:
    P = pd.read_sql_query("SELECT * FROM products WHERE status = 'Active'", con)
    INV = pd.read_sql_query("SELECT * FROM inventory_weekly", con, parse_dates=["week_start"])
    PO = pd.read_sql_query("SELECT * FROM production_orders WHERE received_date IS NOT NULL", con,
                           parse_dates=["due_date", "received_date"])
cls = pd.read_csv(ROOT / "data_clean" / "sku_policy.csv")[["sku", "abc", "xyz"]]
d25 = INV[INV.week_start >= "2025-01-01"].pivot(index="week_start", columns="sku", values="demand_units")
d25 = d25.where(d25 > 0)
PO["delay_wk"] = (PO.received_date - PO.due_date).dt.days / 7
dm, ds = PO.groupby("production_line").delay_wk.mean(), PO.groupby("production_line").delay_wk.std()
last = INV[INV.week_start == INV.week_start.max()].set_index("sku")
S = P.merge(cls, on="sku")
S = S[S.sku.isin(d25.columns[d25.count() >= 26])].copy()
S["mean_d"] = S.sku.map(d25.mean()).round(1); S["sd_d"] = S.sku.map(d25.std()).round(1)
S["lead_wk"] = (S.lead_time_wk + S.production_line.map(dm)).round(2); S["lt_sd"] = S.production_line.map(ds).round(2)
for c in ["on_hand_units", "on_order_units", "backlog_units"]:
    S[c] = S.sku.map(last[c])
S = S.sort_values(["abc", "sku"]).reset_index(drop=True)

wb = Workbook()
# ------------------------------------------------------------------ Settings
st = wb.active; st.title = "Settings"
st["A1"] = "Settings"; st["A1"].font = Font(bold=True, size=14, color=INDIGO)
st["A2"] = "Change the blue cells; every alert in the Stock sheet recalculates."; st["A2"].font = Font(italic=True, color=SLATE)
st.append([]); st.append(["ABC class", "Target service level", "Meaning"])
for c in st[4]:
    c.font = Font(bold=True, color="FFFFFF"); c.fill = H_FILL
for k, sl, txt in [("A", 0.98, "Top 80% of revenue"), ("B", 0.95, "Next 15%"), ("C", 0.90, "Last 5%")]:
    st.append([k, sl, txt]); st.cell(st.max_row, 2).font = IN_FONT; st.cell(st.max_row, 2).number_format = "0%"
st.append([])
for label, val, fmt in [("Z items: service level step-down", 0.02, "0%"), ("Order cover (weeks of demand)", 8, "0"),
                        ("Watch band (weeks above reorder point)", 2, "0"), ("Inventory carrying rate (per year)", 0.22, "0%")]:
    st.append([label, val]); st.cell(st.max_row, 2).font = IN_FONT; st.cell(st.max_row, 2).number_format = fmt
row_of = {st.cell(r, 1).value: r for r in range(1, st.max_row + 1)}      # named ranges follow the labels' real rows
for name, ref in [("SL_Table", f"Settings!$A${row_of['A']}:$B${row_of['C']}"),
                  ("Z_Step", f"Settings!$B${row_of['Z items: service level step-down']}"),
                  ("Order_Weeks", f"Settings!$B${row_of['Order cover (weeks of demand)']}"),
                  ("Watch_Weeks", f"Settings!$B${row_of['Watch band (weeks above reorder point)']}"),
                  ("Carry_Rate", f"Settings!$B${row_of['Inventory carrying rate (per year)']}")]:
    wb.defined_names[name] = DefinedName(name, attr_text=ref)
st.column_dimensions["A"].width = 40; st.column_dimensions["B"].width = 20; st.column_dimensions["C"].width = 22

# ------------------------------------------------------------------ Stock
ws = wb.create_sheet("Stock")
ws["A1"] = "Larkspur Home Textiles · reorder alerts"; ws["A1"].font = Font(bold=True, size=14, color=INDIGO)
ws["A2"] = "Stock position 31 Dec 2025 · demand = 2025 weekly average and spread · blue = input, black = formula"
ws["A2"].font = Font(italic=True, color=SLATE)
head = ["SKU", "Product", "Category", "Line", "ABC", "XYZ", "Unit cost", "Avg weekly demand", "SD weekly demand",
        "Lead time (wk)", "Lead time SD (wk)", "On hand", "On order", "Backorders", "Service level", "Z", "Safety stock",
        "Reorder point", "Inventory position", "Weeks of cover", "Status", "Suggested order", "Order value"]
HR = 4
for j, h in enumerate(head, 1):
    c = ws.cell(HR, j, h); c.font = Font(bold=True, color="FFFFFF"); c.fill = H_FILL
    c.alignment = Alignment(wrap_text=True, vertical="center")
ws.row_dimensions[HR].height = 32
for i, r in S.iterrows():
    x = HR + 1 + i
    vals = [r.sku, r.product_name, r.category, r.production_line, r.abc, r.xyz, r.unit_cost, r.mean_d, r.sd_d, r.lead_wk, r.lt_sd,
            int(r.on_hand_units), int(r.on_order_units), int(r.backlog_units)]
    for j, v in enumerate(vals, 1):
        c = ws.cell(x, j, v)
        if j >= 7:
            c.font = IN_FONT
    f = {
        15: f'=VLOOKUP(E{x},SL_Table,2,FALSE)-IF(F{x}="Z",Z_Step,0)',
        16: f"=_xlfn.NORM.S.INV(O{x})",
        17: f"=ROUNDUP(P{x}*SQRT(J{x}*I{x}^2+H{x}^2*K{x}^2),0)",
        18: f"=ROUND(H{x}*J{x}+Q{x},0)",
        19: f"=L{x}+M{x}-N{x}",
        20: f'=IF(H{x}>0,L{x}/H{x},"")',
        21: f'=IF(S{x}<=R{x},"REORDER NOW",IF(S{x}<=R{x}+Watch_Weeks*H{x},"WATCH","OK"))',
        22: f'=IF(U{x}="REORDER NOW",MAX(ROUND(H{x}*Order_Weeks,0),R{x}-S{x}),0)',
        23: f"=V{x}*G{x}",
    }
    for j, formula in f.items():
        ws.cell(x, j, formula)
last_row = HR + len(S)
fmts = {7: "$#,##0.00", 8: "#,##0.0", 9: "#,##0.0", 10: "0.0", 11: "0.0", 12: "#,##0", 13: "#,##0", 14: "#,##0",
        15: "0.0%", 16: "0.00", 17: "#,##0", 18: "#,##0", 19: "#,##0", 20: "0.0", 22: "#,##0", 23: "$#,##0"}
for j, fm in fmts.items():
    for x in range(HR + 1, last_row + 1):
        ws.cell(x, j).number_format = fm
widths = [9, 40, 13, 6, 5, 5, 9, 10, 10, 9, 9, 9, 9, 10, 9, 6, 9, 9, 10, 9, 14, 10, 11]
for j, wdt in enumerate(widths, 1):
    ws.column_dimensions[ws.cell(HR, j).column_letter].width = wdt
tab = Table(displayName="tblStock", ref=f"A{HR}:W{last_row}")
tab.tableStyleInfo = TableStyleInfo(name="TableStyleLight15", showRowStripes=True)
ws.add_table(tab)
rng_ = f"U{HR + 1}:U{last_row}"
ws.conditional_formatting.add(rng_, CellIsRule(operator="equal", formula=['"REORDER NOW"'], fill=PatternFill(bgColor=RED),
                                                font=Font(bold=True, color="B42318")))
ws.conditional_formatting.add(rng_, CellIsRule(operator="equal", formula=['"WATCH"'], fill=PatternFill(bgColor=AMBER),
                                                font=Font(bold=True, color="B54708")))
ws.conditional_formatting.add(rng_, CellIsRule(operator="equal", formula=['"OK"'], fill=PatternFill(bgColor=GREEN),
                                                font=Font(color="067647")))
ws.conditional_formatting.add(f"T{HR + 1}:T{last_row}", DataBarRule(start_type="num", start_value=0, end_type="num", end_value=12,
                                                                     color="2B5876"))
ws.conditional_formatting.add(f"A{HR + 1}:A{last_row}", FormulaRule(formula=[f'$U{HR + 1}="REORDER NOW"'], font=Font(bold=True)))
ws.freeze_panes = ws.cell(HR + 1, 3)
ws.sheet_view.zoomScale = 90

# ------------------------------------------------------------------ Summary
sm = wb.create_sheet("Summary", 0)
sm["A1"] = "Reorder alerts · summary"; sm["A1"].font = Font(bold=True, size=16, color=INDIGO)
sm["A2"] = "Larkspur Home Textiles · stock position 31 Dec 2025 · all figures are formulas on the Stock sheet"
sm["A2"].font = Font(italic=True, color=SLATE)
sm.append([]); sm.append(["Status", "SKUs", "Suggested order value"])
for c in sm[4]:
    c.font = Font(bold=True, color="FFFFFF"); c.fill = H_FILL
for k, fill in [("REORDER NOW", RED), ("WATCH", AMBER), ("OK", GREEN)]:
    x = sm.max_row + 1
    sm.append([k, f'=COUNTIF(tblStock[Status],A{x})', f'=SUMIF(tblStock[Status],A{x},tblStock[Order value])'])
    sm.cell(x, 1).fill = PatternFill("solid", fgColor=fill); sm.cell(x, 3).number_format = "$#,##0"
x = sm.max_row + 1
sm.append(["Total", f"=SUM(B5:B{x - 1})", f"=SUM(C5:C{x - 1})"])
for c in sm[x]:
    c.font = Font(bold=True); c.border = Border(top=thin)
sm.cell(x, 3).number_format = "$#,##0"
sm.append([]); sm.append(["Production line", "Reorder now", "Watch", "OK", "Stock on hand (value)"])
hl = sm.max_row
for c in sm[hl]:
    c.font = Font(bold=True, color="FFFFFF"); c.fill = H_FILL
for line in ["L1", "L2", "L3", "L4"]:
    y = sm.max_row + 1
    sm.append([line, f'=COUNTIFS(tblStock[Line],A{y},tblStock[Status],"REORDER NOW")',
               f'=COUNTIFS(tblStock[Line],A{y},tblStock[Status],"WATCH")', f'=COUNTIFS(tblStock[Line],A{y},tblStock[Status],"OK")',
               f'=SUMPRODUCT((tblStock[Line]=A{y})*tblStock[On hand]*tblStock[Unit cost])'])
    sm.cell(y, 5).number_format = "$#,##0"
ch = BarChart(); ch.type = "bar"; ch.grouping = "stacked"; ch.overlap = 100
ch.height = 7.5; ch.width = 16
sm["G3"] = "Alerts by production line (SKUs)"; sm["G3"].font = Font(bold=True, color=INDIGO)
ch.x_axis.delete = False; ch.y_axis.delete = False
ch.legend.position = "t"; ch.legend.overlay = False; ch.y_axis.majorGridlines = None
ch.add_data(Reference(sm, min_col=2, max_col=4, min_row=hl, max_row=hl + 4), titles_from_data=True)
ch.set_categories(Reference(sm, min_col=1, min_row=hl + 1, max_row=hl + 4))
for s_, col in zip(ch.series, ["B83A30", "C99A06", "2E8B57"]):
    s_.graphicalProperties.solidFill = col; s_.graphicalProperties.line.solidFill = col
sm.add_chart(ch, "G4")
for col_, wdt in zip("ABCDE", [18, 13, 22, 10, 22]):
    sm.column_dimensions[col_].width = wdt

# ------------------------------------------------------------------ Notes
nt = wb.create_sheet("Notes")
notes = [("Column", "Meaning"),
         ("Avg / SD weekly demand", "Weekly units ordered in 2025 (weeks with demand), including lines later cancelled"),
         ("Lead time (wk)", "Planned production lead time + the line's average receipt delay (2023-25)"),
         ("Lead time SD (wk)", "Spread of receipt delays for the production line"),
         ("Service level", "Target chance of not running out during a lead time; from Settings by ABC class, lower for Z (erratic) items"),
         ("Safety stock", "Z x sqrt(lead time x demand SD^2 + demand^2 x lead time SD^2)"),
         ("Reorder point", "Average demand over the lead time + safety stock"),
         ("Inventory position", "On hand + on order - backorders"),
         ("Status", "REORDER NOW when inventory position <= reorder point; WATCH within the watch band above it"),
         ("Suggested order", "Order cover x weekly demand, or enough to get back to the reorder point if that is more"),
         ("ABC / XYZ", "ABC: share of 2023-24 revenue. XYZ: demand variability (X steady, Y variable, Z erratic)")]
for r in notes:
    nt.append(r)
for c in nt[1]:
    c.font = Font(bold=True, color="FFFFFF"); c.fill = H_FILL
nt.column_dimensions["A"].width = 24; nt.column_dimensions["B"].width = 110
for ws_ in wb.worksheets:
    ws_.sheet_view.showGridLines = False
path = OUT / "Larkspur_reorder_alerts.xlsx"
wb.save(path)
S.to_csv(ROOT / "data_clean" / "reorder_inputs.csv", index=False)
print(f"wrote {path}: {len(S)} SKUs")
