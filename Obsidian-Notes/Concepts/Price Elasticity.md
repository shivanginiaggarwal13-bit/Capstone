**Price Elasticity** measures how much demand for a SKU changes in response to a change in its price — the percentage change in quantity demanded for a 1% change in price. In [[Supply Brain]] it's the input that determines whether a SKU is a good candidate for price-based demand shaping instead of holding extra inventory against it.

**How it's estimated — deliberately not fabricated.** Real price variation already exists in the transaction data through promotions: roughly 21% of transactions carry a promo, with discount depth ranging 5–50%. Elasticity is **derived** (a 🔵 status, distinct from 🔴 synthetic) via log-log regression on that real discount_pct/quantity variation in `sales_transactions` — not randomly generated, because there's no need to fabricate what the real data already lets you estimate directly.

**Benchmark ranges used to sanity-check the regression output:**
| Tier | Elasticity range | Interpretation |
|---|---|---|
| Staples (fruits/vegetables) | −0.26 to −0.8 | Most inelastic — demand barely moves with price |
| Premium / differentiated | −0.8 to −1.5 | Low elasticity |
| Mainstream FMCG | −1.5 to −2.5 | Moderate |
| Value tier / private label | −2.5 to −3.5 | Most elastic |
| Sugar-sweetened beverages / alcohol | −0.7 to −3.9 | Wide range, often high |

Brand-level elasticity typically runs 2–3x higher (more elastic) than the same product measured at category level, so elasticity gets assigned by category *and* price tier, not as one system-wide number.

**Why it's the hinge of the whole "buffer routing" idea.** A highly elastic SKU can absorb a demand shock with a small, cheap price nudge — a modest discount pulls in enough extra demand (or a modest price bump suppresses enough excess demand) to avoid a stockout or a write-off, at a fraction of the cost of carrying extra safety stock. An inelastic SKU can't be discounted its way out of trouble — a price move barely moves volume, so it needs the physical buffer instead. This distinction is what feeds directly into the **[[Price-Inventory Exchange Rate]]**.

Related: [[Supply Brain]], [[Price-Inventory Exchange Rate]]
