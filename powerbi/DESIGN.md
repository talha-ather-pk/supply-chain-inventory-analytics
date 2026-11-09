# Larkspur operations dashboard: design and implementation notes

The dashboard is generated, not hand-built: [`scripts/build_pbip.py`](../scripts/build_pbip.py) writes the Power BI
Project (TMDL semantic model + PBIR report) from the tables in `data_clean/powerbi/`. Open `Larkspur.pbip` in Power BI
Desktop and click Refresh.

## Look: "Mill floor", a light industrial identity

Larkspur Home Textiles is fictional (synthetic data): it has no logo, website or official colours, so this is a
**proposed** identity built for its industry (textile manufacturing), audience (operations and supply-chain teams in
weekly reviews, often projected or printed) and data (many green / amber / red status signals). Light mode keeps those
signals legible on a projector and on paper.

| Role | HEX | |
|---|---|---|
| Primary (main data series) | `#2B5876` | industrial steel blue |
| Secondary | `#5E6B73` | steel grey |
| Accent (navigation, markers only) | `#D9622B` | thread orange; never used in charts, so it cannot be read as "watch" |
| Page background | `#F4F1EA` | greige, the colour of undyed cotton |
| Cards, charts, tables | `#FFFFFF` | hairline border `#E2DDD2` |
| Top navigation band | `#22313A` | charcoal steel; inactive tabs `#B8C2C8`, active tab white with an orange underline |
| Text | `#1E2A30` / `#5E6B73` / `#8A949A` | primary / secondary / muted |
| On target / watch / off target | `#2E8B57` / `#C99A06` / `#B83A30` | |
| Chart sequence | `#2B5876` `#7FA7C2` `#3E8E7E` `#5E6B73` `#B7A98B` `#9C6B98` | |
| Table shading (target) | `#DCEFE3` / `#F6EBC8` / `#F4D9D5` | light tints of the status colours |

* **Layout:** a top navigation band (logo, page tabs, reporting date) instead of a sidebar; the page title, the filter
  context and the three synced slicers with a reset button share the row below; content uses the full width.
* **KPI tiles:** white card, a 3 px top edge in the tile's status colour (driven by the same measure as its status
  line), the value in DIN-style numerals (DIN, falling back to Bahnschrift and Segoe UI) and a monthly trend line for
  measures that move over time. Fixed whole-company results carry a WHOLE BOOK tag instead.
* **Logo:** an original mark for the fictional company: three orange warp threads crossed by a white weft, with the
  name in DIN-style capitals.
* Segoe UI for text; Semibold for titles.

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

* **Reset filters**: a button beside the filters using Power BI's *Clear all slicers* action (slicers are synced across pages).
* **SKU tooltip** on the "SKUs most often short" and reorder-point tables: fill rate, weeks short, cover, both
  reorder points, lost margin.
* **Drill-through**: right-click an SKU → SKU Detail, or select a row and use *Open SKU detail*.
* **Cross-filtering** is on for every visual.

## Validation

* PBIR JSON validated against Microsoft's published schemas (0 errors).
* After refresh, DAX checked against SQL and Python: 69,471 lines, $255,044,856 revenue, 34.6% gross margin, 77.9% on
  time, OTIF 75.1%, every carrier, production line and zone figure, the 2025 leaks ($1,616,490 avoidable), the policy
  replay and 11 of 11 cleaning checks.
