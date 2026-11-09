-- Larkspur Home Textiles: operations questions, answered in SQL (SQLite) on data_clean/larkspur.db.
-- On time = delivered on or before the promised date. Lines without proof of delivery are left out of on-time rates.
-- Run all: python scripts/run_sql.py  (writes sql/results.md)

-- name: 01_business_summary
-- How big is the business, how much was lost to stock-outs, and how reliable is delivery?
SELECT COUNT(*)                                                         AS order_lines,
       ROUND(SUM(revenue) / 1e6, 1)                                     AS revenue_m,
       ROUND(100.0 * (SUM(revenue) - SUM(cogs)) / SUM(revenue), 1)      AS gross_margin_pct,
       ROUND(100.0 * AVG(status = 'Cancelled - stock-out'), 2)          AS lines_lost_to_stockout_pct,
       ROUND(SUM(lost_revenue) / 1e6, 2)                                AS lost_revenue_m,
       ROUND(100.0 * AVG(on_time), 1)                                   AS on_time_pct,
       ROUND(100.0 * SUM(CASE WHEN status = 'Shipped' AND on_time = 1 THEN qty ELSE 0 END)
             / SUM(CASE WHEN status <> 'Open backorder' THEN qty END), 1) AS otif_units_pct
FROM order_lines;

-- name: 02_on_time_by_mode_and_carrier
-- Which carriers miss the promised date, and what do they cost per kg?
SELECT o.ship_mode, o.carrier,
       COUNT(*)                                         AS lines,
       ROUND(100.0 * AVG(o.on_time), 1)                 AS on_time_pct,
       ROUND(AVG(o.transit_days), 1)                    AS avg_transit_days,
       ROUND(AVG(CASE WHEN o.on_time = 0 THEN o.days_late END), 1) AS avg_days_late_when_late,
       r.usd_per_kg
FROM order_lines o JOIN freight_rates r ON r.carrier = o.carrier
WHERE o.on_time IS NOT NULL
GROUP BY o.ship_mode, o.carrier ORDER BY o.ship_mode, on_time_pct;

-- name: 03_late_cause_split
-- Is lateness a warehouse problem (waiting for stock) or a transport problem?
SELECT CASE WHEN waited_for_stock = 1 THEN 'Waited for stock' ELSE 'Shipped from stock' END AS dispatch,
       COUNT(*)                                             AS lines,
       ROUND(100.0 * AVG(on_time), 1)                       AS on_time_pct,
       SUM(on_time = 0)                                     AS late_lines,
       ROUND(100.0 * SUM(on_time = 0) / (SELECT SUM(on_time = 0) FROM order_lines), 1) AS share_of_all_late_pct
FROM order_lines WHERE on_time IS NOT NULL
GROUP BY dispatch;

-- name: 04_peak_season_sea
-- Do the sea quotes allow for Oct-Dec port congestion?
SELECT CASE WHEN CAST(strftime('%m', ship_date) AS INT) >= 10 THEN 'Oct-Dec' ELSE 'Jan-Sep' END AS season,
       COUNT(*) AS sea_lines,
       ROUND(AVG(transit_days), 1)          AS avg_transit_days,
       ROUND(100.0 * AVG(on_time), 1)       AS on_time_pct
FROM order_lines
WHERE ship_mode = 'Sea' AND on_time IS NOT NULL AND waited_for_stock = 0
GROUP BY season;

-- name: 05_on_time_by_market
-- Which markets get the worst service?
SELECT market,
       COUNT(*)                              AS lines,
       ROUND(SUM(revenue) / 1e6, 1)          AS revenue_m,
       ROUND(100.0 * AVG(on_time), 1)        AS on_time_pct,
       ROUND(SUM(chargeback) / 1e3, 0)       AS chargebacks_k
FROM order_lines WHERE status = 'Shipped'
GROUP BY market ORDER BY on_time_pct;

-- name: 06_stock_shortage_by_production_line
-- Does one reorder rule fit every production line? (weeks short = weeks with lost or backordered demand)
SELECT p.production_line,
       ROUND(AVG(p.lead_time_wk), 1)                                         AS avg_lead_time_wk,
       COUNT(DISTINCT p.sku)                                                 AS skus,
       ROUND(100.0 * AVG(i.short_week), 1)                                   AS weeks_short_pct,
       ROUND(1.0 * SUM(i.on_hand_units) / SUM(i.demand_units), 1)            AS weeks_of_cover,
       ROUND(SUM(i.lost_units) * 100.0 / SUM(i.demand_units), 2)             AS demand_lost_pct
FROM inventory_weekly i JOIN products p USING (sku)
GROUP BY p.production_line ORDER BY p.production_line;

-- name: 07_supplier_delays
-- How often does each production line deliver replenishment late?
SELECT production_line,
       COUNT(*)                                                AS production_orders,
       ROUND(100.0 * AVG(days_late > 0), 1)                     AS late_pct,
       ROUND(AVG(CASE WHEN days_late > 0 THEN days_late END), 1) AS avg_days_late_when_late
FROM production_orders WHERE received_date IS NOT NULL
GROUP BY production_line ORDER BY production_line;

-- name: 08_profit_by_category
-- Which categories make money once freight, chargebacks and returns are counted?
WITH r AS (SELECT category, SUM(refund_value + return_freight) AS returns_cost FROM returns GROUP BY category)
SELECT o.category,
       ROUND(SUM(o.revenue) / 1e6, 2)                                       AS revenue_m,
       ROUND(100.0 * (SUM(o.revenue) - SUM(o.cogs)) / SUM(o.revenue), 1)    AS gross_margin_pct,
       ROUND(SUM(o.freight_cost) / 1e6, 2)                                  AS freight_m,
       ROUND(SUM(o.chargeback) / 1e3, 0)                                    AS chargebacks_k,
       ROUND(r.returns_cost / 1e3, 0)                                       AS returns_k,
       ROUND(100.0 * (SUM(o.revenue) - SUM(o.cogs) - SUM(o.freight_cost) - SUM(o.chargeback) - r.returns_cost)
             / SUM(o.revenue), 1)                                           AS contribution_pct
FROM order_lines o JOIN r USING (category)
GROUP BY o.category ORDER BY contribution_pct;

-- name: 09_returns_by_line
-- Is one production line behind the quality returns?
SELECT p.production_line,
       COUNT(DISTINCT o.order_id || '-' || o.line_no)                                         AS shipped_lines,
       (SELECT COUNT(*) FROM returns r WHERE r.production_line = p.production_line
          AND r.reason LIKE 'Defect%')                                                         AS defect_returns,
       ROUND(100.0 * (SELECT COUNT(*) FROM returns r WHERE r.production_line = p.production_line
          AND r.reason LIKE 'Defect%') / COUNT(*), 2)                                         AS defect_rate_pct
FROM order_lines o JOIN products p USING (sku)
WHERE o.status = 'Shipped'
GROUP BY p.production_line ORDER BY p.production_line;

-- name: 10_inventory_record_accuracy
-- Can the stock figures be trusted? (accurate = within +/-2% or 1 unit of the count)
SELECT zone,
       COUNT(*)                              AS counts,
       ROUND(100.0 * AVG(accurate), 1)        AS accurate_pct,
       SUM(ABS(variance_units))               AS abs_variance_units
FROM cycle_counts GROUP BY zone ORDER BY zone;
