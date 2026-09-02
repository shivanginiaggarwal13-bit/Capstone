# Price Elasticity & Buffer Strategy Routing

Charter Phase 3 deliverable — *"estimate price elasticity per segment and compare the cost of
buffering with inventory vs. buffering with price"* (the price–inventory exchange rate).

## Contents

| File | What it is |
|---|---|
| `price_elasticity_analysis.ipynb` | **Source of record.** Documented, executed notebook: load → model → category & segment elasticity → benchmark check → exchange rate → findings. |
| `build_deck.py` | Builds `Price_Elasticity_Findings.pptx` from `output/findings.json` + the figures. Run *after* the notebook. |
| `Price_Elasticity_Findings.pptx` | 14-slide findings deck. |
| `output/price_elasticity_analysis.html` | Rendered read-only copy of the executed notebook. |
| `output/findings.json` | Every number the deck cites, machine-readable. |
| `output/elasticity_by_category.csv`, `elasticity_by_tier.csv`, `exchange_rate_by_category.csv` | Result tables. |
| `output/fig_*.png` | The three figures. |

## Reproduce

```bash
cd "Price Elasticity"
python -m nbconvert --to notebook --execute --inplace price_elasticity_analysis.ipynb
python -m nbconvert --to html --output-dir output price_elasticity_analysis.ipynb
python build_deck.py
```

Requires `pandas numpy statsmodels matplotlib python-pptx nbconvert`.

## Method (one paragraph)

Constant-elasticity (log–log) demand model with SKU × ISO-week fixed effects, demeaned before OLS
with HC1 robust standard errors — the same specification as
`Clustering & Segmentation/Segmentation_v2/segmentation_v2.py` §2, extended with inference and the
exchange-rate calculation. `elasticity_base` = response to shelf/list price; `elasticity_promo` =
response to promotional depth. The exchange rate prices the MEIO-recommended safety stock two ways:
inventory carrying cost vs. the discount cost required — given that segment's elasticity — to pull
the same number of units of demand forward across the baseline volume.

## Headline results (repo commit `8301f25`, 2025 actuals)

- All five categories price-elastic: β₁ from **−1.06** (Beverages) to **−3.60** (Snacks & Confct.).
- Segment matters: Dairy & Bakery Premium ≈ **−1.7**, Value ≈ **−0.3** (not significant).
- Exchange rate favours **inventory** for 4 of 5 categories; Frozen Foods ≈ parity (**1.23×**).
- Portfolio exchange rate ≈ **0.33×** — inventory is ~3× cheaper as a buffer at MEIO's SS levels.

See the notebook's §7 for caveats (low R², thin price variation, uniform-discount assumption).
