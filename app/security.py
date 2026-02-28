"""SSRF protection: block private IP ranges and non-HTTPS URLs."""

import ipaddress
import socket
from urllib.parse import urlparse

# RFC1918 + link-local + loopback
BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("127.0.0.0/8"),     # loopback
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]


class URLSecurityError(ValueError):
    pass


def validate_input_url(url: str) -> str:
    """
    Validate that a URL is safe to fetch:
    - Must be HTTPS
    - Hostname must not resolve to a private/loopback IP
    Returns the URL unchanged if safe, raises URLSecurityError otherwise.
    """
    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise URLSecurityError(f"Only https:// URLs are allowed, got: {parsed.scheme}://")

    hostname = parsed.hostname
    if not hostname:
        raise URLSecurityError("URL has no hostname")

    try:
        # Resolve all addresses for the hostname
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise URLSecurityError(f"Cannot resolve hostname '{hostname}': {e}")

    for family, *_, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue

        for net in BLOCKED_NETWORKS:
            if ip in net:
                raise URLSecurityError(
                    f"URL resolves to a private/reserved IP address: {ip_str}"
                )

    return url

# ── Raw FFmpeg command security ───────────────────────────────────────────────

import re as _re

# Patterns that are blocked in raw ffmpeg commands
_BLOCKED_CMD_PATTERNS = [
    # Direct network access (all inputs must go through input_files)
    r'https?://',
    r'rtsp://',
    r'rtmp://',
    r'ftp://',
    r'tcp://',
    r'udp://',
    # Filesystem escapes
    r'/etc/',
    r'/proc/',
    r'/sys/',
    r'/root/',
    r'/home/',
    r'/var/',
    r'/tmp/',
    r'\.\.',   # path traversal
    # Dangerous lavfi/filter features
    r'script=',
    r'aeval=.*exec',
    # Shell injection via pipe
    r'pipe:.*\|',
    r'\$\(',   # command substitution
    r'`',      # backtick execution
]

_BLOCKED_CMD_RE = _re.compile('|'.join(_BLOCKED_CMD_PATTERNS), _re.IGNORECASE)

PLACEHOLDER_RE = _re.compile(r'\{\{(in_\w+|out_\w+)\}\}')


class CommandSecurityError(ValueError):
    pass


def validate_ffmpeg_command(cmd: str, input_aliases: set[str], output_aliases: set[str]) -> None:
    """Validate a raw ffmpeg command string for security issues."""
    if _BLOCKED_CMD_RE.search(cmd):
        match = _BLOCKED_CMD_RE.search(cmd)
        raise CommandSecurityError(f"Blocked pattern in ffmpeg_command: '{match.group()}'")

    # Check all placeholders reference declared aliases
    found = set(PLACEHOLDER_RE.findall(cmd))
    all_aliases = input_aliases | output_aliases
    unknown = found - all_aliases
    if unknown:
        raise CommandSecurityError(
            f"Unknown placeholders in ffmpeg_command: {unknown}. "
            f"Declared: {all_aliases}"
        )

    # Check all declared aliases are used
    missing = all_aliases - found
    if missing:
        raise CommandSecurityError(
            f"Declared aliases not used in ffmpeg_command: {missing}"
        )
