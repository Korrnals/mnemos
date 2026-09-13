"""Experiment strata — repo-artifact corpora for pre-registered runs.

E0 (``docs/experiments/e0-meta-level.md``) pre-registers experiments
whose corpora must be committed BEFORE any run. This package holds
those corpora as repository artifacts (generators + committed JSON),
strictly OUTSIDE the stands' measured corpora: nothing under
``benchmarks/strata`` feeds S1/S2/S3/S4, and no live store is ever
written by preparation code.

Current contents: ``e2_gov`` — the lanes-strata corpus wave (E0 §3.1-
§3.3, §4.3-§4.4).
"""
