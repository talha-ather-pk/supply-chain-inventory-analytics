# Manufacturing: Supply Chain & Inventory Dashboard

**Status:** not started

**Question:** Mirrors your textile work: late deliveries, inventory accuracy and profit leaks.

**Dataset:** [DataCo Smart Supply Chain](https://www.kaggle.com/datasets/shashwatwork/dataco-smart-supply-chain-for-big-data-analysis). Download into `data_raw/` (raw data stays out of Git if over 50 MB; see the root README).

**Tools:** Python, Power BI, Excel

## Steps
- [ ] Clean the order file (encoding is latin-1) and drop personal columns you don't need
- [ ] Late-delivery rate by shipping mode, region and product
- [ ] Profit by category and market
- [ ] Excel reorder-alert template: stock level vs reorder point with conditional formatting
- [ ] Power BI operations dashboard
- [ ] Write key findings (numbers) in this README
- [ ] Save screenshots in `images/`
- [ ] Add to Upwork portfolio

## Folders
- `data_raw/` original files, never edited
- `data_clean/` outputs of the scripts
- `notebooks/` exploration
- `scripts/` final, re-runnable code
- `powerbi/` .pbix and DAX notes
- `images/` screenshots for README and portfolio
