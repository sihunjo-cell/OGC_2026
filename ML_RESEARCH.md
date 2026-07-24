# ML insertion points for the OGC solver

## Short answer

The best first target is per-instance configuration selection:

```
prob_info features -> best existing solver arm / parameter preset
```

This is the cheapest useful ML problem because labels are produced by running
the current solver arms and comparing their verified objectives. No human labels
and no state-level counterfactual oracle are required.

## Ranking

1. Per-instance algorithm/config selection
   - Data: cheap instance features + one run per candidate arm.
   - Label: best objective among arms.
   - Fit to current code: direct match to `Outer.portfolio.default_portfolio`.
   - Risk: moderate upside if the current 4-worker min-wins portfolio already
     covers the best arm; still useful for reallocating time, choosing extended
     arms, and pre-selecting expensive knobs.

2. In-bay / dispatch priority policy
   - Data: state snapshots plus counterfactual realizations for alternative
     order hints.
   - Label: best order candidate by realized objective.
   - Fit to current code: matches `Outer.inbay` and `Phase2.dispatch_order_hint`.
   - Risk: higher data cost because each label needs extra `realize` calls.

3. Online ALNS operator/parameter control
   - Data: iteration-level search state and delayed rewards.
   - Label: reinforcement-learning or contextual-bandit reward.
   - Fit to current code: possible around `AOS.select`, acceptance temperature,
     restart, and repair/destroy choice.
   - Risk: highest training complexity and noisy credit assignment.

## Implemented data path

`ml/dataset.py` generates:

- `features.jsonl`: fixed-width features from problem JSON.
- `runs.jsonl`: objective and stats for each arm.
- `labels.jsonl`: best arm per instance and timelimit.

Default command:

```
python -m ml.dataset --prob-dir train --timelimit 60
```

Larger arm sweep:

```
python -m ml.dataset --prob-dir train --timelimit 60 --arm-set extended
```
