"""Detector-independent danger labelling of the benchmark corpus.

ADR-0020 requires the corpus danger labelling to be INDEPENDENT of the
detectors, so the detector-quarantine-fp rate is observable: a label is
a human judgement about intent, ``danger_detectors.detect`` is a
machine signal, and the two may disagree — the disagreement is the
metric.

Labelling rule (closed-world, fixture ground truth):

* an entry is DANGEROUS iff it carries a planted FAKE secret
  (``GoldenEntry.planted``) — those entries exist to be caught;
* every other entry — plain prose/code/logs AND the legitimate
  tech-pattern class (``tech_patterns``) — is BENIGN; a detector firing
  there is a false positive.

The label never inspects detector output; it derives from the fixture
metadata authored together with the content.
"""

from __future__ import annotations

from benchmarks.corpus.corpus import CORPUS
from benchmarks.corpus.tech_patterns import TECH_PATTERN_ENTRIES

#: Entries judged dangerous (planted-secret carriers). Everything else
#: in the labelled universe (CORPUS + TECH_PATTERN_ENTRIES) is benign.
DANGEROUS_SLUGS: frozenset[str] = frozenset(e.slug for e in CORPUS if e.planted)

#: The full labelled universe: ranked corpus + the tech-pattern class.
LABELLED_ENTRIES = CORPUS + TECH_PATTERN_ENTRIES


def danger_label(slug: str) -> bool | None:
    """Danger judgement for ``slug``; ``None`` when outside the universe."""
    if slug in DANGEROUS_SLUGS:
        return True
    if any(e.slug == slug for e in LABELLED_ENTRIES):
        return False
    return None
