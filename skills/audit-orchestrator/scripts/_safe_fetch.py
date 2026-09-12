#!/usr/bin/env python3
"""Safe HTTP acquisition layer — the ONLY place Citely performs network egress to a target.

Everything here sits LEFT of the crawl-artifact boundary. Analyzers are pure consumers with zero
network access, so every URL-safety concern is centralized in this one module.

Threat model: the URL is attacker-influenced and the response is hostile input.

Defences (PLAN.md §10):
  * Scheme allowlist; URL userinfo rejected; port allowlist.
  * Every resolved A/AAAA record validated against private / loopback / link-local / reserved /
    multicast / ULA / IPv4-mapped-IPv6 ranges and the cloud metadata IP 169.254.169.254.
  * The validated IP is PINNED for the connection, closing the DNS-rebinding (TOCTOU) window
    between validation and connect. SNI and Host still carry the real hostname.
  * Redirects followed manually, re-validated at EVERY hop, with a hop cap and loop detection.
  * Response size capped, and decompressed size capped separately to stop compression bombs.
  * Connect/read timeouts; TLS failures recorded, never downgraded to plaintext.
  * Credentials and Cookie/Authorization headers redacted from every log line and result.

Concurrency note: IP pinning temporarily replaces socket.getaddrinfo, which is process-global.
Citely runs one fetch at a time per process (skills are separate subprocesses), so this is safe
here. Do NOT introduce threaded fetching in this process without replacing the pinning mechanism.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import time
import urllib.robotparser
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse

import requests

log = logging.getLogger("safe_fetch")

# Cloud metadata endpoints. Link-local already covers 169.254.0.0/16, but these are called out
# explicitly because they are the highest-value SSRF targets and deserve an unmissable rule.
METADATA_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})

# NAT64 prefixes (RFC 6052 well-known, RFC 8215 local-use). On an IPv6-only network a DNS64 resolver
# synthesizes these, embedding the real IPv4 in the low 32 bits. They must be DECODED, not blanket
# blocked: Python marks the whole prefix is_reserved, which would reject every public site on such a
# network. Only the embedded IPv4 tells us whether the destination is actually internal.
NAT64_PREFIXES = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)


def nat64_embedded_ipv4(addr):
    """Return the IPv4 embedded in a NAT64 address, or None if this is not one."""
    if not isinstance(addr, ipaddress.IPv6Address):
        return None
    for prefix in NAT64_PREFIXES:
        if addr in prefix:
            return ipaddress.ip_address(int(addr) & 0xFFFFFFFF)
    return None

REDACTED = "[REDACTED]"
SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "set-cookie", "proxy-authorization"})


class FetchError(Exception):
    """Base class for acquisition failures. Never escapes as a crash — callers record and continue."""


class UnsafeURLError(FetchError):
    """The URL was refused before any connection was attempted."""


class ResponseTooLargeError(FetchError):
    """Body or decompressed body exceeded its cap."""


class UnexpectedContentTypeError(FetchError):
    """Response was not one of the content types the caller asked for."""


def deadline_from_config(config: dict, started: float | None = None) -> float:
    """Absolute monotonic deadline derived from `budgets.global_deadline_s`.

    Callers should use this rather than inventing their own budget, so the 5-minute ceiling is
    enforced from ONE configured value instead of being silently unenforced when nobody passes one.
    """
    base = time.monotonic() if started is None else started
    return base + float(config.get("budgets", {}).get("global_deadline_s", 270))


def content_type_matches(content_type: str | None, allowed) -> bool:
    """Compare a Content-Type header against an allowlist, ignoring charset and parameters."""
    if not allowed:
        return True
    if not content_type:
        return False
    base = content_type.split(";", 1)[0].strip().lower()
    return base in {a.lower() for a in allowed}


@dataclass
class FetchResult:
    requested_url: str
    final_url: str = ""
    status: int | None = None
    content_type: str | None = None
    body: bytes = b""
    byte_size: int = 0
    headers: dict = field(default_factory=dict)
    redirect_chain: list = field(default_factory=list)
    elapsed_ms: int = 0
    error: str | None = None
    error_kind: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300


@dataclass
class RobotsResult:
    """Robots outcome, split into the two concerns PLAN.md §6.3.1 keeps separate."""
    checked: bool = False
    status: int | None = None
    # (1) Operational gate: may OUR auditor fetch? Never scored.
    audit_allowed: bool = True
    # (2) Scored signal: may the AI assistants' crawlers fetch?
    ai_crawlers: dict = field(default_factory=dict)   # token -> allowed
    ai_crawlers_determinable: bool = False
    crawl_delay: float | None = None
    sitemaps: list = field(default_factory=list)
    error: str | None = None
    # True when robots.txt could not be FETCHED at all (DNS, SSRF refusal, 5xx, timeout). Distinct
    # from a robots.txt that was read and says Disallow — conflating them tells the user a site
    # refuses crawlers when we simply never reached it.
    unreachable: bool = False

    @property
    def blocked_ai_crawlers(self) -> list:
        return sorted(t for t, allowed in self.ai_crawlers.items() if not allowed)


# --- Redaction --------------------------------------------------------------------------------
def redact_url(url: str) -> str:
    """Strip userinfo so credentials never reach a log line or the report."""
    try:
        p = urlparse(url)
    except ValueError:
        return "[UNPARSEABLE_URL]"
    if p.username or p.password:
        host = p.hostname or ""
        if p.port:
            host = f"{host}:{p.port}"
        return urlunparse((p.scheme, f"{REDACTED}@{host}", p.path, p.params, p.query, p.fragment))
    return url


def redact_headers(headers) -> dict:
    return {
        k: (REDACTED if k.lower() in SENSITIVE_HEADERS else v)
        for k, v in dict(headers or {}).items()
    }


# --- IP / URL validation ----------------------------------------------------------------------
def is_safe_ip(ip: str) -> bool:
    """False for any address that could reach internal infrastructure.

    Handles IPv4-mapped IPv6 (::ffff:10.0.0.1) explicitly — treating that as a plain global IPv6
    address is a classic SSRF bypass.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False

    if ip in METADATA_IPS:
        return False

    # IPv6 transition mechanisms embed an IPv4 address inside an IPv6 one, a standard SSRF bypass.
    # Verified against Python 3.11 (see tests/test_security.py::test_ipv6_transition_bypasses_rejected):
    #   ::ffff:a.b.c.d  (IPv4-mapped)     -> already caught by Python's is_private; unwrap is belt-and-braces
    #   ::a.b.c.d       (IPv4-compatible) -> caught by is_reserved
    #   64:ff9b::/96    (NAT64)           -> DECODED below; is_reserved would wrongly block the
    #                                        whole prefix, including public hosts on IPv6-only networks
    #   2002::/16       (6to4)            -> caught by NOTHING in Python's predicates.
    # The sixtofour branch below is therefore LOAD-BEARING: deleting it silently opens an SSRF hole
    # to loopback/RFC1918/metadata via 6to4. Do not "simplify" it away.
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = getattr(addr, "ipv4_mapped", None)
        if mapped is not None:
            return is_safe_ip(str(mapped))
        sixtofour = getattr(addr, "sixtofour", None)
        if sixtofour is not None:
            return is_safe_ip(str(sixtofour))
        nat64 = nat64_embedded_ipv4(addr)
        if nat64 is not None:
            # Judge the destination the packet actually reaches. Blanket-blocking the NAT64 prefix
            # would make every public site unreachable on an IPv6-only network.
            return is_safe_ip(str(nat64))

    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_url(url: str, config: dict) -> tuple:
    """Validate scheme/userinfo/host/port. Returns (scheme, host, port). Raises UnsafeURLError."""
    fetch_cfg = config.get("fetch", {})
    allowed_schemes = set(fetch_cfg.get("allowed_schemes", ["http", "https"]))
    allowed_ports = set(fetch_cfg.get("allowed_ports", [80, 443]))
    permissive = bool(fetch_cfg.get("allow_private_hosts", False))

    try:
        p = urlparse(url)
    except ValueError as exc:
        raise UnsafeURLError(f"unparseable URL: {exc}") from exc

    if p.scheme not in allowed_schemes:
        raise UnsafeURLError(f"scheme {p.scheme!r} not allowed (permitted: {sorted(allowed_schemes)})")

    # Credentials in a URL are never legitimate for a read-only public audit, and they are a common
    # way to smuggle a different authority past naive parsers.
    if p.username or p.password:
        raise UnsafeURLError("URL contains embedded credentials")

    try:
        host = p.hostname
    except ValueError as exc:
        raise UnsafeURLError(f"invalid host: {exc}") from exc
    if not host:
        raise UnsafeURLError("URL has no host")

    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURLError(f"invalid port: {exc}") from exc

    if not permissive and port not in allowed_ports:
        raise UnsafeURLError(f"port {port} not allowed (permitted: {sorted(allowed_ports)})")

    return p.scheme, host, port


MAX_HOSTNAME_LEN = 253          # RFC 1035 limit on a fully-qualified domain name
MAX_LABEL_LEN = 63              # RFC 1035 limit on a single dot-separated label


def resolve_host(host: str, port: int) -> list:
    """Resolve to every A/AAAA record. All of them must pass validation, not merely the first."""
    # Reject impossible hostnames before touching the resolver. An over-long name makes
    # socket.getaddrinfo raise UnicodeError from the IDNA codec — an exception type that is not a
    # gaierror and would therefore escape safe_get entirely, crashing the orchestrator.
    if len(host) > MAX_HOSTNAME_LEN:
        raise UnsafeURLError(f"hostname exceeds {MAX_HOSTNAME_LEN} characters")
    if any(len(label) > MAX_LABEL_LEN for label in host.split(".")):
        raise UnsafeURLError(f"hostname label exceeds {MAX_LABEL_LEN} characters")

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"DNS resolution failed for {host}: {exc}") from exc
    except UnicodeError as exc:
        # IDNA/punycode encoding failure on a malformed international hostname.
        raise UnsafeURLError(f"hostname could not be encoded for DNS: {exc}") from exc
    except Exception as exc:  # resolver quirks must never escape this boundary
        raise UnsafeURLError(f"DNS resolution failed for {host}: {exc}") from exc
    ips = []
    for info in infos:
        ip = info[4][0]
        if ip not in ips:
            ips.append(ip)
    if not ips:
        raise UnsafeURLError(f"no addresses resolved for {host}")
    return ips


def validate_and_resolve(url: str, config: dict) -> tuple:
    """Full pre-connection check. Returns (host, port, pinned_ip)."""
    permissive = bool(config.get("fetch", {}).get("allow_private_hosts", False))
    _scheme, host, port = validate_url(url, config)
    ips = resolve_host(host, port)

    if not permissive:
        unsafe = [ip for ip in ips if not is_safe_ip(ip)]
        if unsafe:
            raise UnsafeURLError(f"{host} resolves to non-public address(es): {', '.join(unsafe)}")
    return host, port, ips[0]


@contextmanager
def pinned_dns(hostname: str, ip: str):
    """Force resolution of `hostname` to the already-validated `ip` for the duration of a request.

    This is what actually defeats DNS rebinding: without it an attacker can return a public IP for
    our validation lookup and an internal one microseconds later for the connect. The hostname stays
    in the URL, so TLS SNI and the Host header are unaffected.
    """
    original = socket.getaddrinfo
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET

    def patched(host, port, *args, **kwargs):
        if host == hostname:
            sockaddr = (ip, port) if family == socket.AF_INET else (ip, port, 0, 0)
            return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]
        return original(host, port, *args, **kwargs)

    socket.getaddrinfo = patched
    try:
        yield
    finally:
        socket.getaddrinfo = original


# --- Body reading -----------------------------------------------------------------------------
def read_capped(response, max_bytes: int, max_decompressed: int) -> bytes:
    """Stream the body, aborting past either cap.

    Two separate limits matter: `max_bytes` bounds what we agree to download, while
    `max_decompressed` bounds what a compression bomb can expand to in memory. iter_content yields
    DECODED bytes, so accumulating there is what catches the bomb.
    """
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                raise ResponseTooLargeError(f"Content-Length {declared} exceeds cap {max_bytes}")
        except ValueError:
            pass  # malformed header: fall through to streaming enforcement

    # iter_content yields DECODED bytes. When the response carries no Content-Encoding, decoded
    # size equals wire size, so max_bytes applies directly. When it IS encoded we cannot see the
    # wire size mid-stream, and max_decompressed is the meaningful guard (it is what a compression
    # bomb inflates). Enforcing both closes the gap where a chunked, unencoded response between the
    # two limits slipped through with only Content-Length checked.
    encoded = bool((response.headers or {}).get("Content-Encoding"))
    wire_cap = None if encoded else max_bytes

    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if wire_cap is not None and total > wire_cap:
            raise ResponseTooLargeError(f"body exceeded cap {wire_cap}")
        if total > max_decompressed:
            raise ResponseTooLargeError(
                f"decompressed body exceeded cap {max_decompressed} (possible compression bomb)"
            )
        chunks.append(chunk)
    return b"".join(chunks)


# --- Fetch ------------------------------------------------------------------------------------
DEFAULT_USER_AGENT = "CitelyAuditBot/0.1"


def user_agent(config: dict) -> str:
    """The identifying User-Agent sent on every request, composed in exactly one place.

    `fetch.contact_url` is appended as " (+URL)" only when it is set. It is empty by default, and
    deliberately so: the field used to carry an example.com address, which IANA reserves for
    documentation, so a site operator investigating the bot reached a placeholder page that read
    like a real contact. An invented URL would be worse still — this project's own rule is never to
    present an unobserved value as a fact, and a User-Agent is the one thing a third-party operator
    actually sees.

    Composed here rather than read straight from config at four call sites, so the product token
    the robots parser matches on and the string actually sent can never drift apart.
    """
    fetch_cfg = (config or {}).get("fetch") or {}
    base = (fetch_cfg.get("user_agent") or DEFAULT_USER_AGENT).strip()
    contact = (fetch_cfg.get("contact_url") or "").strip()
    return f"{base} (+{contact})" if contact else base


def safe_get(url: str, config: dict, *, deadline: float | None = None,
             extra_headers: dict | None = None,
             require_content_types=None) -> FetchResult:
    """Fetch a URL with every guard applied. Never raises: failures land in FetchResult.error.

    `require_content_types` rejects anything outside the allowlist AFTER the response headers
    arrive but BEFORE the body is read, so a large non-HTML payload is never downloaded. Left None
    for robots.txt and sitemaps, which legitimately declare their own types.
    """
    fetch_cfg = config.get("fetch", {})
    max_redirects = int(fetch_cfg.get("max_redirects", 5))
    max_bytes = int(fetch_cfg.get("max_body_bytes", 5 * 1024 * 1024))
    max_decompressed = int(fetch_cfg.get("max_decompressed_bytes", 20 * 1024 * 1024))
    timeout = (float(fetch_cfg.get("connect_timeout_s", 10)),
               float(fetch_cfg.get("read_timeout_s", 15)))

    headers = {
        "User-Agent": user_agent(config),
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    if extra_headers:
        headers.update(extra_headers)

    result = FetchResult(requested_url=redact_url(url))
    started = time.monotonic()
    current = url
    seen = set()

    try:
        for hop in range(max_redirects + 1):
            if deadline is not None and time.monotonic() > deadline:
                raise FetchError("global deadline exceeded")

            normalized = current.split("#", 1)[0]
            if normalized in seen:
                raise UnsafeURLError(f"redirect loop detected at {redact_url(normalized)}")
            seen.add(normalized)

            # Re-validated at EVERY hop: hop 0 being safe says nothing about hop 3.
            host, _port, pinned_ip = validate_and_resolve(current, config)

            with pinned_dns(host, pinned_ip):
                response = requests.get(
                    current, headers=headers, timeout=timeout,
                    allow_redirects=False, stream=True,
                )

            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise FetchError(f"{response.status_code} redirect with no Location header")
                    if hop >= max_redirects:
                        raise FetchError(f"exceeded {max_redirects} redirects")
                    result.redirect_chain.append(
                        {"from": redact_url(current), "status": response.status_code}
                    )
                    current = urljoin(current, location)
                    continue

                declared_type = response.headers.get("Content-Type")
                if not content_type_matches(declared_type, require_content_types):
                    raise UnexpectedContentTypeError(
                        f"content-type {declared_type!r} not in {sorted(require_content_types)}"
                    )

                body = read_capped(response, max_bytes, max_decompressed)
                result.final_url = redact_url(response.url)
                result.status = response.status_code
                result.content_type = response.headers.get("Content-Type")
                result.body = body
                result.byte_size = len(body)
                result.headers = redact_headers(response.headers)
                return result
            finally:
                response.close()

        raise FetchError(f"exceeded {max_redirects} redirects")

    except UnsafeURLError as exc:
        result.error, result.error_kind = str(exc), "unsafe_url"
    except ResponseTooLargeError as exc:
        result.error, result.error_kind = str(exc), "too_large"
    except UnexpectedContentTypeError as exc:
        result.error, result.error_kind = str(exc), "content_type"
    except requests.exceptions.SSLError as exc:
        # Recorded, never retried over plaintext: silently downgrading would defeat the point.
        result.error, result.error_kind = f"TLS failure: {exc}", "tls"
    except requests.exceptions.Timeout as exc:
        result.error, result.error_kind = f"timeout: {exc}", "timeout"
    except requests.exceptions.RequestException as exc:
        result.error, result.error_kind = f"request failed: {exc}", "network"
    except FetchError as exc:
        result.error, result.error_kind = str(exc), "fetch"
    except Exception as exc:
        # Last-resort net. safe_get is the orchestrator's network boundary and its contract is that
        # it NEVER raises: an unanticipated exception here (a resolver quirk, a urllib3 edge case)
        # would otherwise abort the whole audit instead of degrading one page.
        log.exception("unexpected error fetching %s", result.requested_url)
        result.error, result.error_kind = f"unexpected error: {type(exc).__name__}", "unexpected"
    finally:
        result.elapsed_ms = int((time.monotonic() - started) * 1000)

    log.warning("fetch failed for %s: %s", result.requested_url, result.error)
    return result


# --- robots.txt -------------------------------------------------------------------------------
def check_robots(base_url: str, config: dict, *, deadline: float | None = None) -> RobotsResult:
    """Evaluate robots.txt for BOTH concerns: our operational gate and AI-crawler access.

    Policy when robots.txt itself is unavailable:
      * 4xx -> no rules published, so allow-all (RFC 9309).
      * 5xx / network failure -> conservative: refuse to fetch. A transient server error must not
        be read as permission.
    AI-crawler access is only determinable when robots.txt was actually read; otherwise the scored
    check resolves to `unknown` rather than guessing.
    """
    robots_cfg = config.get("robots", {})
    tokens = [c["token"] for c in robots_cfg.get("ai_crawlers", []) if "token" in c]
    audit_ua = user_agent(config)
    # RobotFileParser matches on the product token, not the full UA string.
    audit_token = audit_ua.split("/", 1)[0]

    parsed = urlparse(base_url)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))

    out = RobotsResult()
    fetched = safe_get(robots_url, config, deadline=deadline)
    out.status = fetched.status

    if fetched.error is not None:
        out.checked = False
        out.error = fetched.error
        out.audit_allowed = False   # conservative: unreachable != permitted (PLAN §6.3.1)
        # ...but record WHY. Without this the caller cannot tell "the site refuses crawlers" from
        # "we could not reach robots.txt", and would report the former about a site it never read.
        out.unreachable = True
        return out

    if fetched.status is not None and 400 <= fetched.status < 500:
        out.checked = True
        out.audit_allowed = True          # no rules published
        out.ai_crawlers = {t: True for t in tokens}
        out.ai_crawlers_determinable = True
        return out

    if fetched.status is None or fetched.status >= 500:
        out.checked = False
        out.error = f"robots.txt returned {fetched.status}"
        out.audit_allowed = False
        out.unreachable = True
        return out

    parser = urllib.robotparser.RobotFileParser()
    try:
        text = fetched.body.decode("utf-8", errors="replace")
        parser.parse(text.splitlines())
    except Exception as exc:  # malformed robots.txt must not break the audit
        out.checked = True
        out.error = f"malformed robots.txt: {exc}"
        out.audit_allowed = True          # unparseable == no enforceable rules
        out.ai_crawlers = {t: True for t in tokens}
        out.ai_crawlers_determinable = False
        return out

    out.checked = True
    try:
        out.audit_allowed = parser.can_fetch(audit_token, base_url)
        out.ai_crawlers = {t: parser.can_fetch(t, base_url) for t in tokens}
        out.ai_crawlers_determinable = True
    except Exception as exc:
        out.error = f"robots evaluation failed: {exc}"
        out.audit_allowed = True
        out.ai_crawlers_determinable = False

    try:
        delay = parser.crawl_delay(audit_token)
        out.crawl_delay = float(delay) if delay is not None else None
    except Exception:
        out.crawl_delay = None

    try:
        out.sitemaps = list(parser.site_maps() or [])
    except Exception:
        out.sitemaps = []

    return out
