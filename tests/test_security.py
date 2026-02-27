"""Tests for SSRF protection."""

import pytest
from unittest.mock import patch
import socket

from app.security import validate_input_url, URLSecurityError


def test_rejects_http():
    with pytest.raises(URLSecurityError, match="Only https://"):
        validate_input_url("http://example.com/video.mp4")


def test_rejects_ftp():
    with pytest.raises(URLSecurityError, match="Only https://"):
        validate_input_url("ftp://example.com/video.mp4")


def test_rejects_no_scheme():
    with pytest.raises(URLSecurityError):
        validate_input_url("example.com/video.mp4")


def _mock_getaddrinfo(ip: str):
    """Returns a mock getaddrinfo result for the given IP."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))]


@pytest.mark.parametrize("private_ip", [
    "10.0.0.1",
    "172.16.0.1",
    "192.168.1.1",
    "169.254.169.254",  # AWS metadata endpoint
    "127.0.0.1",
])
def test_rejects_private_ips(private_ip):
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo(private_ip)):
        with pytest.raises(URLSecurityError, match="private/reserved"):
            validate_input_url(f"https://internal-host/{private_ip}/video.mp4")


def test_accepts_public_url():
    with patch("socket.getaddrinfo", return_value=_mock_getaddrinfo("93.184.216.34")):
        result = validate_input_url("https://example.com/video.mp4")
    assert result == "https://example.com/video.mp4"
