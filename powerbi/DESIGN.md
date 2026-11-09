# Larkspur operations dashboard: design and implementation notes

The dashboard is generated, not hand-built: [`scripts/build_pbip.py`](../scripts/build_pbip.py) writes the Power BI
Project (TMDL semantic model + PBIR report) from the tables in `data_clean/powerbi/`. Open `Larkspur.pbip` in Power BI
Desktop and click Refresh.

## Look: indigo operations

* **Palette.** Deep indigo canvas (#0B0F20), raised cards (#121834), a darker sidebar (#080B18) and hairline borders
  (#242C55). Indigo (#6574F5) is the main data series, periwinkle (#8C9EFF) and sky (#5BC8EC) are the accents, and
  green / amber / red mean one thing everywhere: on target, watch, off target. Indigo is the textile trade's own dye.
* **No white anywhere**: charts, tables, matrices, slicers and tooltips use the theme's dark surfaces.
* **KPI tiles**: label with an accent marker, large value, and a status or comparison line. Green / amber / red status
  lines compare the value with an operating target (on time 95%, fill rate 98.5%, weeks short ≤ 5%, defects ≤ 1%,
  late production orders ≤ 5%, record accuracy 95%). Tiles that show fixed whole-company results (policy backtest,
  cleaning log) carry a **WHOLE BOOK** tag so nobody expects them to follow the filters.
* Segoe UI throughout; Semibold for titles and numbers.

## Data model

| Table | Rows | Role |
|---|---:|---|
| `dim_sku` | 150 | product, category, fabric, production line, lead time, warehouse zone, ABC-XYZ class, current and recommended reorder points, reorder status at 31 Dec 2025 |
| `dim_customer` | 90 | market, channel, late-delivery penalty |
| `dim_week` | 157 | week start (Monday), month, year |
| `dim_carrier` | 7 | mode, carrier, $ per kg |
| `fact_order_line` | 69,471 | one row per order line: revenue, cost, freight, expedite premium, chargeback, on time, days late, status |
| `fact_inventory` | 23,400 | one row per SKU per week: demand, shipped, lost, on hand, backlog, stock value |
| `fact_return`, `fact_count`, `fact_po` | 1,493 / 654 / 3,103 | returns, cycle counts, production orders |
| result tables | | 2025 policy replay, profit leaks by year, transit effects vs truth, delivery what-ifs, data-quality log, findings |

Single-direction relationships from every fact to `dim_sku` and `dim_week`; order lines also to `dim_customer` and
`dim_carrier`. The year, category and production-line slicers therefore reach every fact table.

## Measures worth noting

* **On-time %** averages a 0/1 column that is blank for lines not yet delivered or without proof of delivery, so those
  lines never count as late.
* **OTIF %** is units shipped on time ÷ units ordered (excluding lines still on backorder), so cancelled lines count
  against it.
* **Avg Stock Value** averages the weekly stock totals (`AVERAGEX` over weeks), so it is right at week, month or year level.
* **Leak Year** = the year picked in the filter, else 2025; the waterfall and the leak tiles use it. The waterfall starts
  from a **Gross margin** bridge row so the leaks are visible next to it, not next to $92M of revenue.
* Filters on a column the visual also groups by use `KEEPFILTERS`.

## Pages

| Page | Content |
|---|---|
| Overview | KPIs; revenue by month; on time by month and mode; scorecard by category (shaded against target) |
| Delivery | on time by carrier (with $/kg) and market; what-if scenarios; transit model vs the true effects |
| Inventory Health | weeks short by production line; stock value by week; ABC-XYZ matrix; SKUs most often short |
| Reorder Policy | 2025 replay by line and by week; reorder points by SKU (current vs recommended, status at 31 Dec) |
| Profit Leaks | waterfall from gross margin to contribution; chargebacks by channel; returns by reason |
| Quality & Suppliers | defect returns by line and fabric; late production orders; stock-record accuracy by zone |
| Data Quality | every cleaning fix next to the number injected |
| SKU Detail | drill-through on an SKU: stock on hand vs weekly demand, production orders, order lines by status |

## Interactivity

* **Reset filters**: a sidebar button using Power BI's *Clear all slicers* action (slicers are synced across pages).
* **SKU tooltip** on the "SKUs most often short" and reorder-point tables: fill rate, weeks short, cover, both
  reorder points, lost margin.
* **Drill-through**: right-click an SKU → SKU Detail, or select a row and use *Open SKU detail*.
* **Cross-filtering** is on for every visual.

## Validation

* PBIR JSON validated against Microsoft's published schemas (0 errors).
* After refresh, DAX checked against SQL and Python: 69,471 lines, $255,044,856 revenue, 34.6% gross margin, 77.9% on
  time, OTIF 75.1%, every carrier, production line and zone figure, the 2025 leaks ($1,616,490 avoidable), the policy
  replay and 11 of 11 cleaning checks.
