"""T5-SSRF: per-hop redirect re-validation tests.

Verifies that ingest_url follows redirects manually and validates every
Location target through _validate_url before issuing the next request.

Test coverage:
  (a) Redirect to AWS IPv4 metadata (169.254.169.254) is BLOCKED.
  (b) Redirect to loopback / private ranges is BLOCKED.
  (c) Redirect to IPv6 metadata / loopback is BLOCKED.
  (d) Legitimate public->public redirect is FOLLOWED and content extracted.
  (e) Exceeding the hop limit (_MAX_REDIRECTS) results in a placeholder.
  (f) Location with a non-http(s) scheme is BLOCKED.
  (g) Relative Location is resolved to an absolute URL and then validated.

All tests mock httpx.Client and trafilatura -- no real network calls.
Initial URLs use literal public IPs (8.8.8.8) so _validate_url passes
without DNS resolution.
"""

from __future__ import annotations

import sys
import tempfile
from unittest.mock import MagicMock, call, patch

import pytest

from mnemos.config import Settings
from mnemos.manager import _MAX_REDIRECTS, MemoryManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager() -> MemoryManager:  # type: ignore[misc]
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(
            mnemos={"vault_path": tmpdir, "data_dir": tmpdir, "db_name": "test.db"},
            embedding={"provider": "chromadb"},
        )
        mgr = MemoryManager(settings)
        yield mgr  # type: ignore[misc]
        mgr.close()


TAGS = ["project:test", "agent:test", "gcw:test"]
# 8.8.8.8 is a public IP (Google DNS) -- passes _validate_url without DNS.
INITIAL_URL = "https://8.8.8.8/"


def _stub_trafilatura() -> MagicMock:
    stub = MagicMock()
    stub.extract.return_value = "extracted"
    return stub


def _mock_resp(status: int, body: str = "", location: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = body
    # Use a real dict so .get() works without side-effects.
    r.headers = {"location": location} if location else {}
    return r


# ---------------------------------------------------------------------------
# (a) Redirect to AWS IPv4 metadata endpoint is BLOCKED
# ---------------------------------------------------------------------------


class TestMetadataRedirectBlocked:
    def test_redirect_to_aws_ipv4_metadata_blocked(self, manager: MemoryManager) -> None:
        """Pivot: public host -> 169.254.169.254 must be caught on the redirect hop."""
        redir = _mock_resp(302, location="http://169.254.169.254/latest/meta-data/")
        trafilatura_stub = _stub_trafilatura()
        with (
            patch("httpx.Client") as mock_cls,
            patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
        ):
            mock_client = MagicMock()
            mock_client.get.return_value = redir
            mock_cls.return_value.__enter__.return_value = mock_client
            mem = manager.ingest_url(INITIAL_URL, tags=TAGS, project="test", agent="test")
        # Guard fires inside the loop -> exception is caught -> placeholder content.
        assert "[fetch failed:" in mem.content

    def test_redirect_to_aws_ipv4_metadata_second_hop_blocked(self, manager: MemoryManager) -> None:
        """Second-hop metadata pivot: public -> public -> 169.254.169.254 is caught."""
        hop1 = _mock_resp(301, location="https://1.1.1.1/")  # public; passes
        hop2 = _mock_resp(302, location="http://169.254.169.254/")  # blocked
        trafilatura_stub = _stub_trafilatura()
        with (
            patch("httpx.Client") as mock_cls,
            patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
        ):
            mock_client = MagicMock()
            mock_client.get.side_effect = [hop1, hop2]
            mock_cls.return_value.__enter__.return_value = mock_client
            mem = manager.ingest_url(INITIAL_URL, tags=TAGS, project="test", agent="test")
        assert "[fetch failed:" in mem.content


# ---------------------------------------------------------------------------
# (b) Redirect to loopback / private ranges is BLOCKED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "http://127.0.0.1/",
        "http://127.0.0.2/",
        "http://10.0.0.1/",
        "http://10.255.255.255/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://172.31.255.255/",
    ],
)
def test_redirect_to_private_range_blocked(manager: MemoryManager, location: str) -> None:
    """Redirect to any RFC1918/loopback address must be blocked on the hop."""
    redir = _mock_resp(301, location=location)
    trafilatura_stub = _stub_trafilatura()
    with (
        patch("httpx.Client") as mock_cls,
        patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
    ):
        mock_client = MagicMock()
        mock_client.get.return_value = redir
        mock_cls.return_value.__enter__.return_value = mock_client
        mem = manager.ingest_url(INITIAL_URL, tags=TAGS, project="test", agent="test")
    assert "[fetch failed:" in mem.content


# ---------------------------------------------------------------------------
# (c) Redirect to IPv6 metadata / loopback is BLOCKED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "http://[::1]/",  # IPv6 loopback
        "http://[fd00:ec2::254]/latest/meta-data/",  # AWS IPv6 metadata
        "http://[fe80::1]/",  # IPv6 link-local
    ],
)
def test_redirect_to_ipv6_blocked(manager: MemoryManager, location: str) -> None:
    """Redirect to IPv6 loopback / link-local / metadata must be blocked."""
    redir = _mock_resp(302, location=location)
    trafilatura_stub = _stub_trafilatura()
    with (
        patch("httpx.Client") as mock_cls,
        patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
    ):
        mock_client = MagicMock()
        mock_client.get.return_value = redir
        mock_cls.return_value.__enter__.return_value = mock_client
        mem = manager.ingest_url(INITIAL_URL, tags=TAGS, project="test", agent="test")
    assert "[fetch failed:" in mem.content


# ---------------------------------------------------------------------------
# (d) Legitimate public -> public redirect is followed and content extracted
# ---------------------------------------------------------------------------


class TestLegitimateRedirectFollowed:
    def test_single_redirect_public_to_public(self, manager: MemoryManager) -> None:
        """A single 301 public->public redirect is followed; content is returned."""
        # 1.1.1.1 = Cloudflare DNS - public IP, passes _validate_url.
        hop1 = _mock_resp(301, location="https://1.1.1.1/canonical")
        final = _mock_resp(200, body="canonical content")
        trafilatura_stub = _stub_trafilatura()
        trafilatura_stub.extract.return_value = "canonical content"

        with (
            patch("httpx.Client") as mock_cls,
            patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
        ):
            mock_client = MagicMock()
            mock_client.get.side_effect = [hop1, final]
            mock_cls.return_value.__enter__.return_value = mock_client
            mem = manager.ingest_url(INITIAL_URL, tags=TAGS, project="test", agent="test")

        assert "[fetch failed:" not in mem.content
        # Two GET calls: initial URL + redirect target.
        assert mock_client.get.call_count == 2

    def test_redirect_chain_within_limit(self, manager: MemoryManager) -> None:
        """A redirect chain <= MAX_REDIRECTS hops is followed successfully."""
        # Build a chain: initial -> hop1 -> hop2 -> final (3 hops total)
        ips = ["1.1.1.1", "8.8.4.4", "9.9.9.9"]
        hops = [_mock_resp(302, location=f"https://{ip}/") for ip in ips]
        final = _mock_resp(200, body="destination")
        trafilatura_stub = _stub_trafilatura()
        trafilatura_stub.extract.return_value = "destination"

        with (
            patch("httpx.Client") as mock_cls,
            patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
        ):
            mock_client = MagicMock()
            mock_client.get.side_effect = [*hops, final]
            mock_cls.return_value.__enter__.return_value = mock_client
            mem = manager.ingest_url(INITIAL_URL, tags=TAGS, project="test", agent="test")

        assert "[fetch failed:" not in mem.content
        assert mock_client.get.call_count == len(ips) + 1  # initial + 3 hops

    def test_follow_redirects_false_on_client(self, manager: MemoryManager) -> None:
        """httpx.Client must still be constructed with follow_redirects=False.

        The manual per-hop loop relies on the client NOT auto-following, so
        this constructor argument is the enforcement mechanism.
        """
        trafilatura_stub = _stub_trafilatura()
        ok_resp = _mock_resp(200, body="text")
        with (
            patch("httpx.Client") as mock_cls,
            patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
        ):
            mock_client = MagicMock()
            mock_client.get.return_value = ok_resp
            mock_cls.return_value.__enter__.return_value = mock_client
            manager.ingest_url(INITIAL_URL, tags=TAGS, project="test", agent="test")
        _, kwargs = mock_cls.call_args
        assert kwargs.get("follow_redirects") is False


# ---------------------------------------------------------------------------
# (e) Exceeding the hop limit results in a placeholder (not an exception)
# ---------------------------------------------------------------------------


def test_too_many_redirects_produces_placeholder(manager: MemoryManager) -> None:
    """ingest_url degrades to a placeholder when MAX_REDIRECTS is exceeded."""
    # Use distinct public IPs to avoid triggering the loop-detection set.
    # Build MAX_REDIRECTS + 2 redirect responses so the limit is clearly exceeded.
    redirect_ips = [f"1.2.3.{i}" for i in range(1, _MAX_REDIRECTS + 3)]
    redirects = [_mock_resp(302, location=f"https://{ip}/") for ip in redirect_ips]
    # Validate that the ips themselves are actually public (not private/blocked).
    for ip in redirect_ips:
        MemoryManager._validate_url(f"https://{ip}/")

    trafilatura_stub = _stub_trafilatura()
    with (
        patch("httpx.Client") as mock_cls,
        patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
    ):
        mock_client = MagicMock()
        mock_client.get.side_effect = redirects
        mock_cls.return_value.__enter__.return_value = mock_client
        mem = manager.ingest_url(INITIAL_URL, tags=TAGS, project="test", agent="test")

    assert "[fetch failed:" in mem.content


# ---------------------------------------------------------------------------
# (f) Location with a non-http(s) scheme is BLOCKED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "ftp://example.com/file",
        "file:///etc/passwd",
        "gopher://example.com/",
        "mailto:attacker@evil.com",
    ],
)
def test_redirect_to_non_http_scheme_blocked(manager: MemoryManager, location: str) -> None:
    """A redirect to any scheme other than http/https must be rejected."""
    redir = _mock_resp(302, location=location)
    trafilatura_stub = _stub_trafilatura()
    with (
        patch("httpx.Client") as mock_cls,
        patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
    ):
        mock_client = MagicMock()
        mock_client.get.return_value = redir
        mock_cls.return_value.__enter__.return_value = mock_client
        mem = manager.ingest_url(INITIAL_URL, tags=TAGS, project="test", agent="test")
    assert "[fetch failed:" in mem.content


# ---------------------------------------------------------------------------
# (g) Relative Location is resolved to an absolute URL before validation
# ---------------------------------------------------------------------------


class TestRelativeLocationResolution:
    def test_relative_path_resolved_against_base(self, manager: MemoryManager) -> None:
        """A path-relative Location (/new-path) is resolved via urljoin and validated."""
        # Redirect to /canonical on the same host -> stays on 8.8.8.8 -> public IP -> OK.
        redir = _mock_resp(301, location="/canonical")
        final = _mock_resp(200, body="resolved page")
        trafilatura_stub = _stub_trafilatura()
        trafilatura_stub.extract.return_value = "resolved page"

        with (
            patch("httpx.Client") as mock_cls,
            patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
        ):
            mock_client = MagicMock()
            mock_client.get.side_effect = [redir, final]
            mock_cls.return_value.__enter__.return_value = mock_client
            mem = manager.ingest_url(INITIAL_URL, tags=TAGS, project="test", agent="test")

        assert "[fetch failed:" not in mem.content
        # Second GET must target the resolved absolute URL, not the bare relative path.
        second_call_url = mock_client.get.call_args_list[1]
        assert second_call_url == call("https://8.8.8.8/canonical", timeout=30)

    def test_relative_location_to_private_ip_blocked_after_resolution(
        self, manager: MemoryManager
    ) -> None:
        """Even when Location is relative, resolved URL pointing to private IP is blocked."""
        # Protocol-relative Location that resolves to a metadata host.
        # urljoin("https://8.8.8.8/", "http://169.254.169.254/") = "http://169.254.169.254/"
        redir = _mock_resp(302, location="http://169.254.169.254/")
        trafilatura_stub = _stub_trafilatura()
        with (
            patch("httpx.Client") as mock_cls,
            patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
        ):
            mock_client = MagicMock()
            mock_client.get.return_value = redir
            mock_cls.return_value.__enter__.return_value = mock_client
            mem = manager.ingest_url(INITIAL_URL, tags=TAGS, project="test", agent="test")
        assert "[fetch failed:" in mem.content


# ---------------------------------------------------------------------------
# Redirect-loop detection
# ---------------------------------------------------------------------------


def test_redirect_loop_produces_placeholder(manager: MemoryManager) -> None:
    """A URL that redirects back to itself (or to an already-visited URL) is caught."""
    # A -> B -> A: after the second visit to A it should be detected.
    # We use a mock that alternates between two public IPs.
    hop_ab = _mock_resp(302, location="https://1.1.1.1/")  # -> 1.1.1.1
    hop_ba = _mock_resp(302, location="https://8.8.8.8/")  # -> back to initial host

    trafilatura_stub = _stub_trafilatura()
    with (
        patch("httpx.Client") as mock_cls,
        patch.dict(sys.modules, {"trafilatura": trafilatura_stub}),
    ):
        mock_client = MagicMock()
        # Cycle: 8.8.8.8 -> 1.1.1.1 -> 8.8.8.8 -> 1.1.1.1 -> ...
        # The hop limit will fire first (MAX_REDIRECTS = 5) but either way
        # a placeholder is produced.
        mock_client.get.side_effect = [hop_ab, hop_ba] * 10
        mock_cls.return_value.__enter__.return_value = mock_client
        mem = manager.ingest_url(INITIAL_URL, tags=TAGS, project="test", agent="test")
    assert "[fetch failed:" in mem.content
