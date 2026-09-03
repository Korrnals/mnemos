"""S3 long-lived-session stand (ADR-0020, wave BF-3).

Seeded deterministic simulation of an agent's whole session lifecycle
against one long-lived manager: fact-retention@N,k, recall-drift,
checkpoint-return-integrity, sufficiency@task, context-growth-factor,
stage-discard-profile. Nightly class — not in ``make verify``.
"""
