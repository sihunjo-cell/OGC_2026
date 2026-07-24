# MIP-learning patterns relevant to OGC

This is not a recommendation to reformulate the full OGC solver as a MILP.
The useful part is the design pattern:

```
expensive expert decision -> cheap learned ranker/classifier
```

## Relevant literature patterns

- Learning to branch in MIP: imitate strong branching with a cheaper
  learning-to-rank model over candidate variables.
- GNN branch-and-bound: represent variables/constraints as a graph and learn
  variable/node selection policies.
- ML-guided primal heuristics: use solved instances to predict partial or full
  assignments, then repair/check feasibility with the exact solver.
- Warm-started constraint generation: predict useful starting constraints while
  retaining final exact guarantees.

## Translation to this solver

The same pattern applies without using a MILP:

1. `realize()` is the expensive expert.
   - Learn a cheap model that ranks candidate ALNS/inbay alternatives before
     spending full Phase2 time.

2. Exact crane/space checking is an expensive expert inside dispatch.
   - Learn a cheap candidate-anchor ranker, but keep the exact check as the
     final gate.

3. Phase1/Outer bay assignment is a partial assignment problem.
   - A learned warm start is possible, but recent local evidence says the
     dynamic Phase2 dispatch washes out much of the bay-assignment signal.

## Current read

The MIP-learning literature strengthens the case for pairwise/listwise ranking
models. The next nontrivial ML target should probably be:

```
dispatch/inbay candidate features -> pairwise winner / best order mode
```

rather than direct objective regression or direct coordinate prediction.
