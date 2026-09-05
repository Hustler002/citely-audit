"""Phase 2 — security corpus for the safe acquisition layer.

Every test here encodes an attack the fetch layer must refuse. The SSRF cases matter most: Citely
fetches attacker-influenced URLs, so a bypass turns the audit into a probe of the operator's
internal network.

These tests never touch the real network. DNS and HTTP are stubbed so the guards are exercised
deterministically and offline.
"""
from __future__ import annotations

import io
import socket
import sys
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "skills" / "audit-orchestrator" / "scripts"))

import _safe_fetch as F  # noqa: E402
import _scoring as S  # noqa: E402


@pytest.fixture(scope="module")
def config():
    return S.load_config()


@pytest.fixture
def permissive_config(config):
    cfg = {**config, "fetch": {**config["fetch"], "allow_private_hosts": True}}
    return cfg


# --- is_safe_ip: the address blocklist ----------------------------------------------------------
@pytest.mark.parametrize("ip", [
    "127.0.0.1",            # loopback
    "0.0.0.0",              # unspecified
    "10.0.0.5",             # RFC1918
    "172.16.0.1",           # RFC1918
    "192.168.1.1",          # RFC1918
    "169.254.169.254",      # cloud metadata — the highest-value SSRF target
    "169.254.1.1",          # link-local
    "224.0.0.1",            # multicast
    "240.0.0.1",            # reserved
    "::1",                  # IPv6 loopback
    "fe80::1",              # IPv6 link-local
    "fc00::1",              # IPv6 unique-local (ULA)
    "::",                   # IPv6 unspecified
    "::ffff:127.0.0.1",     # IPv4-mapped loopback — classic bypass
    "::ffff:10.0.0.1",      # IPv4-mapped RFC1918 — classic bypass
    "::ffff:169.254.169.254",  # IPv4-mapped metadata
])
def test_unsafe_ips_rejected(ip):
    assert F.is_safe_ip(ip) is False


# IPv6 transition mechanisms. These encode an IPv4 address inside an IPv6 one and are a standard
# SSRF bypass route. Verified coverage as of Python 3.11:
#   * ::ffff:a.b.c.d (IPv4-mapped) -> caught by Python's own is_private (our unwrap is belt-and-braces)
#   * ::a.b.c.d (IPv4-compatible) and 64:ff9b::/96 (NAT64) -> caught by is_reserved
#   * 2002::/16 (6to4) -> caught by NOTHING in Python's predicates. Our explicit sixtofour unwrap in
#     is_safe_ip is the ONLY thing blocking these, so these cases must never lose coverage.
@pytest.mark.parametrize("ip,mechanism", [
    ("2002:7f00:0001::", "6to4 encoding 127.0.0.1"),
    ("2002:a9fe:a9fe::", "6to4 encoding 169.254.169.254 (metadata)"),
    ("2002:0a00:0001::", "6to4 encoding 10.0.0.1"),
    ("2002:c0a8:0101::", "6to4 encoding 192.168.1.1"),
    ("64:ff9b::10.0.0.1", "NAT64 encoding 10.0.0.1"),
    ("64:ff9b::169.254.169.254", "NAT64 encoding metadata"),
    ("::10.0.0.1", "IPv4-compatible encoding 10.0.0.1"),
    ("0:0:0:0:0:ffff:0a00:0001", "IPv4-mapped long form encoding 10.0.0.1"),
    ("2001:0000:4136:e378:8000:63bf:3fff:fdd2", "Teredo"),
])
def test_ipv6_transition_bypasses_rejected(ip, mechanism):
    assert F.is_safe_ip(ip) is False, f"SSRF bypass via {mechanism}"


def test_6to4_public_address_still_allowed():
    """Guard against over-blocking: 6to4 wrapping a PUBLIC IPv4 must stay reachable."""
    assert F.is_safe_ip("2002:5db8:d822::") is True   # 6to4 of 93.184.216.34


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
def test_public_ips_allowed(ip):
    assert F.is_safe_ip(ip) is True


@pytest.mark.parametrize("garbage", ["not-an-ip", "", "999.999.999.999", "10.0.0"])
def test_malformed_ip_is_unsafe(garbage):
    """Anything unparseable must fail closed."""
    assert F.is_safe_ip(garbage) is False


# --- validate_url: scheme, credentials, ports ---------------------------------------------------
@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://evil.test/",
    "ftp://evil.test/",
    "data:text/html,<script>alert(1)</script>",
    "javascript:alert(1)",
])
def test_non_http_schemes_rejected(url, config):
    with pytest.raises(F.UnsafeURLError, match="scheme"):
        F.validate_url(url, config)


def test_url_credentials_rejected(config):
    """user:pass@ can smuggle a different authority past naive parsers."""
    with pytest.raises(F.UnsafeURLError, match="credentials"):
        F.validate_url("https://user:pass@example.com/", config)


def test_username_only_credentials_rejected(config):
    with pytest.raises(F.UnsafeURLError, match="credentials"):
        F.validate_url("https://admin@example.com/", config)


def test_non_standard_port_rejected(config):
    with pytest.raises(F.UnsafeURLError, match="port"):
        F.validate_url("http://example.com:8080/", config)


@pytest.mark.parametrize("url,port", [
    ("http://example.com/", 80),
    ("https://example.com/", 443),
    ("https://example.com:443/x", 443),
])
def test_standard_ports_allowed(url, port, config):
    assert F.validate_url(url, config)[2] == port


def test_missing_host_rejected(config):
    with pytest.raises(F.UnsafeURLError):
        F.validate_url("http:///nohost", config)


def test_permissive_mode_allows_local_ports(permissive_config):
    """The documented test-only escape hatch, required for the localhost fixture server."""
    scheme, host, port = F.validate_url("http://127.0.0.1:8099/", permissive_config)
    assert (host, port) == ("127.0.0.1", 8099)


# --- Resolution: ALL records must be safe -------------------------------------------------------
def _stub_dns(monkeypatch, ips):
    def fake(host, port, *a, **k):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET,
                 socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 (ip, port) if ":" not in ip else (ip, port, 0, 0)) for ip in ips]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_all_resolved_records_must_be_safe(monkeypatch, config):
    """A public A record alongside an internal one must NOT pass — validating only the first is a bypass."""
    _stub_dns(monkeypatch, ["93.184.216.34", "10.0.0.1"])
    with pytest.raises(F.UnsafeURLError, match="non-public"):
        F.validate_and_resolve("https://split-horizon.test/", config)


def test_single_internal_record_rejected(monkeypatch, config):
    _stub_dns(monkeypatch, ["169.254.169.254"])
    with pytest.raises(F.UnsafeURLError, match="non-public"):
        F.validate_and_resolve("https://metadata.test/", config)


def test_public_resolution_accepted(monkeypatch, config):
    _stub_dns(monkeypatch, ["93.184.216.34"])
    host, port, ip = F.validate_and_resolve("https://example.test/", config)
    assert (host, port, ip) == ("example.test", 443, "93.184.216.34")


def test_dns_failure_is_unsafe(monkeypatch, config):
    def boom(*a, **k):
        raise socket.gaierror("nxdomain")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(F.UnsafeURLError, match="DNS resolution failed"):
        F.validate_and_resolve("https://nope.test/", config)


# --- DNS pinning defeats rebinding --------------------------------------------------------------
def test_pinned_dns_forces_validated_ip(monkeypatch):
    _stub_dns(monkeypatch, ["10.0.0.1"])  # what a rebinding attacker would return at connect time
    with F.pinned_dns("evil.test", "93.184.216.34"):
        infos = socket.getaddrinfo("evil.test", 443)
    assert infos[0][4][0] == "93.184.216.34"


def test_pinned_dns_restores_resolver_afterwards(monkeypatch):
    original = socket.getaddrinfo
    with F.pinned_dns("example.test", "93.184.216.34"):
        pass
    assert socket.getaddrinfo is original


def test_pinned_dns_restores_even_on_exception():
    original = socket.getaddrinfo
    with pytest.raises(RuntimeError):
        with F.pinned_dns("example.test", "93.184.216.34"):
            raise RuntimeError("boom")
    assert socket.getaddrinfo is original


def test_pinned_dns_leaves_other_hosts_alone(monkeypatch):
    _stub_dns(monkeypatch, ["8.8.8.8"])
    with F.pinned_dns("pinned.test", "93.184.216.34"):
        other = socket.getaddrinfo("other.test", 443)
    assert other[0][4][0] == "8.8.8.8"


# --- Redaction ----------------------------------------------------------------------------------
def test_redact_url_strips_credentials():
    out = F.redact_url("https://user:hunter2@example.com/path?q=1")
    assert "hunter2" not in out and "user" not in out
    assert "example.com" in out


def test_redact_url_preserves_port():
    assert "example.com:8443" in F.redact_url("https://u:p@example.com:8443/x")


def test_redact_url_passthrough_when_clean():
    url = "https://example.com/path?q=1"
    assert F.redact_url(url) == url


def test_redact_headers_masks_secrets():
    out = F.redact_headers({
        "Authorization": "Bearer supersecret",
        "Cookie": "session=abc",
        "Set-Cookie": "session=abc",
        "Content-Type": "text/html",
    })
    assert out["Authorization"] == F.REDACTED
    assert out["Cookie"] == F.REDACTED
    assert out["Set-Cookie"] == F.REDACTED
    assert out["Content-Type"] == "text/html"  # non-sensitive preserved


# --- Body caps ----------------------------------------------------------------------------------
class FakeResponse:
    """Minimal stand-in for a streamed requests.Response."""
    def __init__(self, chunks, headers=None, status=200, url="https://example.test/"):
        self._chunks = chunks
        self.headers = headers or {}
        self.status_code = status
        self.url = url

    def iter_content(self, chunk_size=65536):
        return iter(self._chunks)

    def close(self):
        pass


def test_content_length_over_cap_rejected_before_download():
    resp = FakeResponse([b"x"], headers={"Content-Length": "999999999"})
    with pytest.raises(F.ResponseTooLargeError, match="Content-Length"):
        F.read_capped(resp, max_bytes=1000, max_decompressed=10000)


def test_decompression_bomb_caught_while_streaming():
    """Small declared size, enormous decoded stream — the classic zip-bomb shape."""
    resp = FakeResponse([b"A" * 8192] * 200, headers={"Content-Length": "512"})
    with pytest.raises(F.ResponseTooLargeError, match="compression bomb"):
        F.read_capped(resp, max_bytes=1_000_000, max_decompressed=100_000)


def test_malformed_content_length_falls_through_to_streaming_cap():
    resp = FakeResponse([b"A" * 100], headers={"Content-Length": "not-a-number"})
    assert F.read_capped(resp, max_bytes=1000, max_decompressed=1000) == b"A" * 100


def test_body_within_caps_returned():
    resp = FakeResponse([b"hello ", b"world"], headers={"Content-Length": "11"})
    assert F.read_capped(resp, max_bytes=1000, max_decompressed=1000) == b"hello world"


# --- safe_get: never raises, always records -----------------------------------------------------
def test_safe_get_returns_error_not_exception_for_bad_scheme(config):
    r = F.safe_get("file:///etc/passwd", config)
    assert r.error_kind == "unsafe_url"
    assert r.ok is False


def test_safe_get_records_metadata_ip_refusal(monkeypatch, config):
    _stub_dns(monkeypatch, ["169.254.169.254"])
    r = F.safe_get("https://metadata.test/", config)
    assert r.error_kind == "unsafe_url"
    assert "non-public" in r.error


def test_safe_get_redacts_credentials_in_result(config):
    r = F.safe_get("https://user:secret@example.com:8080/", config)
    assert "secret" not in r.requested_url


def test_safe_get_tls_failure_recorded_not_downgraded(monkeypatch, config):
    _stub_dns(monkeypatch, ["93.184.216.34"])

    def boom(*a, **k):
        raise requests.exceptions.SSLError("certificate verify failed")
    monkeypatch.setattr(requests, "get", boom)

    r = F.safe_get("https://badtls.test/", config)
    assert r.error_kind == "tls"
    assert r.ok is False


def test_safe_get_timeout_recorded(monkeypatch, config):
    _stub_dns(monkeypatch, ["93.184.216.34"])

    def boom(*a, **k):
        raise requests.exceptions.ConnectTimeout("too slow")
    monkeypatch.setattr(requests, "get", boom)

    assert F.safe_get("https://slow.test/", config).error_kind == "timeout"


def test_safe_get_deadline_enforced(monkeypatch, config):
    import time
    _stub_dns(monkeypatch, ["93.184.216.34"])
    r = F.safe_get("https://example.test/", config, deadline=time.monotonic() - 1)
    assert r.error_kind == "fetch"
    assert "deadline" in r.error


# --- Redirect handling --------------------------------------------------------------------------
def _redirect_chain(monkeypatch, responses):
    """Serve a scripted sequence of responses from requests.get."""
    it = iter(responses)
    monkeypatch.setattr(requests, "get", lambda *a, **k: next(it))


def test_redirect_to_internal_address_refused(monkeypatch, config):
    """The critical case: hop 0 is public, hop 1 points at the metadata service."""
    calls = {"n": 0}

    def fake_dns(host, port, *a, **k):
        ip = "93.184.216.34" if host == "public.test" else "169.254.169.254"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)

    def fake_get(url, **kwargs):
        calls["n"] += 1
        return FakeResponse([], headers={"Location": "http://metadata.test/latest/meta-data/"},
                            status=302, url=url)
    monkeypatch.setattr(requests, "get", fake_get)

    r = F.safe_get("https://public.test/", config)
    assert r.error_kind == "unsafe_url"
    assert "non-public" in r.error
    assert calls["n"] == 1  # refused before the second request was ever issued


def test_redirect_loop_detected(monkeypatch, config):
    _stub_dns(monkeypatch, ["93.184.216.34"])
    monkeypatch.setattr(requests, "get", lambda url, **k: FakeResponse(
        [], headers={"Location": "https://loop.test/a"}, status=302, url=url))
    r = F.safe_get("https://loop.test/a", config)
    assert r.error_kind == "unsafe_url"
    assert "loop" in r.error


def test_redirect_without_location_is_an_error(monkeypatch, config):
    _stub_dns(monkeypatch, ["93.184.216.34"])
    monkeypatch.setattr(requests, "get", lambda url, **k: FakeResponse([], status=302, url=url))
    assert "Location" in F.safe_get("https://example.test/", config).error


def test_redirect_cap_enforced(monkeypatch, config):
    _stub_dns(monkeypatch, ["93.184.216.34"])
    counter = {"n": 0}

    def fake_get(url, **k):
        counter["n"] += 1
        return FakeResponse([], headers={"Location": f"https://example.test/{counter['n']}"},
                            status=302, url=url)
    monkeypatch.setattr(requests, "get", fake_get)

    r = F.safe_get("https://example.test/start", config)
    assert "redirect" in r.error
    assert counter["n"] <= config["fetch"]["max_redirects"] + 1


def test_successful_fetch_populates_result(monkeypatch, config):
    _stub_dns(monkeypatch, ["93.184.216.34"])
    monkeypatch.setattr(requests, "get", lambda url, **k: FakeResponse(
        [b"<html>hi</html>"],
        headers={"Content-Type": "text/html", "Set-Cookie": "s=1"},
        status=200, url=url))

    r = F.safe_get("https://example.test/", config)
    assert r.ok is True
    assert r.body == b"<html>hi</html>"
    assert r.byte_size == 15
    assert r.content_type == "text/html"
    assert r.headers["Set-Cookie"] == F.REDACTED  # secrets never survive into the result


# --- robots.txt ---------------------------------------------------------------------------------
def _robots(monkeypatch, body, status=200):
    monkeypatch.setattr(F, "safe_get", lambda url, cfg, **k: F.FetchResult(
        requested_url=url, final_url=url, status=status, body=body,
        content_type="text/plain", byte_size=len(body)))


def test_robots_allows_everything_by_default(monkeypatch, config):
    _robots(monkeypatch, b"User-agent: *\nDisallow:\n")
    r = F.check_robots("https://example.test/", config)
    assert r.checked and r.audit_allowed
    assert r.blocked_ai_crawlers == []


def test_robots_blocking_ai_crawlers_is_detected(monkeypatch, config):
    """The signal the whole check exists for: crawlable by us, invisible to ChatGPT."""
    _robots(monkeypatch, b"User-agent: *\nDisallow:\n\nUser-agent: GPTBot\nDisallow: /\n")
    r = F.check_robots("https://example.test/", config)
    assert r.audit_allowed is True          # we may still fetch
    assert "GPTBot" in r.blocked_ai_crawlers
    assert r.ai_crawlers_determinable is True


def test_robots_blocking_our_auditor_sets_gate(monkeypatch, config):
    _robots(monkeypatch, b"User-agent: CitelyAuditBot\nDisallow: /\n")
    assert F.check_robots("https://example.test/", config).audit_allowed is False


def test_robots_404_means_allow_all(monkeypatch, config):
    _robots(monkeypatch, b"", status=404)
    r = F.check_robots("https://example.test/", config)
    assert r.audit_allowed is True
    assert r.ai_crawlers_determinable is True
    assert r.blocked_ai_crawlers == []


def test_robots_500_is_conservative(monkeypatch, config):
    """A transient server error must not be read as permission."""
    _robots(monkeypatch, b"", status=503)
    r = F.check_robots("https://example.test/", config)
    assert r.audit_allowed is False
    assert r.ai_crawlers_determinable is False


def test_robots_unreachable_is_conservative(monkeypatch, config):
    monkeypatch.setattr(F, "safe_get", lambda url, cfg, **k: F.FetchResult(
        requested_url=url, error="connection refused", error_kind="network"))
    r = F.check_robots("https://example.test/", config)
    assert r.audit_allowed is False
    assert r.checked is False


def test_robots_crawl_delay_and_sitemaps_extracted(monkeypatch, config):
    _robots(monkeypatch, (
        b"User-agent: *\nCrawl-delay: 2\nDisallow:\n"
        b"Sitemap: https://example.test/sitemap.xml\n"
    ))
    r = F.check_robots("https://example.test/", config)
    assert r.crawl_delay == 2.0
    assert "https://example.test/sitemap.xml" in r.sitemaps


def test_robots_disallow_path_specific(monkeypatch, config):
    _robots(monkeypatch, b"User-agent: *\nDisallow: /private/\n")
    assert F.check_robots("https://example.test/", config).audit_allowed is True
    assert F.check_robots("https://example.test/private/x", config).audit_allowed is False


def test_malformed_robots_does_not_crash(monkeypatch, config):
    _robots(monkeypatch, b"\xff\xfe not really robots \x00\x01\nUser-agent\n Disallow")
    r = F.check_robots("https://example.test/", config)
    assert r.checked is True  # degraded, but the audit continues
