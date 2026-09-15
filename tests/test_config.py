def test_search_hybrid_alpha_default_is_tuned() -> None:
    """#300: the fusion weight default is 0.5 (balanced RRF) — the re-tune
    is a registered composition change; this pin fails on any silent
    re-tune of the retrieval behavior the benches and baselines assume."""
    from vesmaro.config import SearchConfig

    assert SearchConfig().hybrid_alpha == 0.5
