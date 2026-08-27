A **Multi-Armed Bandit (MAB)** is a class of sequential decision-making algorithms for repeatedly choosing among a fixed set of options ("arms") whose payoffs are unknown and must be learned from experience, while balancing **exploitation** (favor the option that's performed best so far) against **exploration** (keep testing the others, in case the current best guess is wrong or stops being right). See **[[Exploration-Exploitation Trade-off]]** for why this tension is structural, not a bug to be solved away.

**The name.** A row of slot machines ("one-armed bandits") with different, unknown payout rates — the player must decide which machine to pull each round to maximize cumulative winnings over a fixed budget of pulls.

**The three required ingredients**, present in every bandit formulation: **arms** (the discrete choice set), a **[[Reward Function (Bandit)|reward]]** (a scalar signal observed after playing an arm), and **rounds** (repeated opportunities to choose, with beliefs updated between them).

**Why it's being evaluated in [[Supply Brain]].** A standard optimization — a regression, an LP solve — produces one fixed answer computed once from historical data. A bandit produces an answer that keeps updating itself as new outcomes arrive; it is structurally a *learning loop*, not a static calculation. The project's own charter language — a system that "continuously re-learns from outcomes" and "automatically re-tunes targets... rather than remaining static after initial setup" — is, independent of intent, a description of bandit behavior. See **[[Multi-Armed Bandits — Supply Brain Strategy]]** for the full argument, the algorithm comparison, and the deployment plan.

**Algorithm family.** [[Epsilon-Greedy]] and [[Upper Confidence Bound (UCB)]] are simpler baselines; [[Thompson Sampling]] is the project's recommended default; [[Contextual Bandit|contextual bandits]] are required (not optional) here because 5,000 SKUs are too sparse for independent per-SKU beliefs.

**What it is not.** Not a demand forecasting model, and not the safety-stock or MEIO optimization itself — it's the layer *above* those calculations, choosing which policy or strategy template to apply and continuously re-checking whether that choice still holds.

Related: [[Supply Brain]], [[Multi-Armed Bandits — Supply Brain Strategy]]
