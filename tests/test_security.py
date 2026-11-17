import socket

import pytest

from app import security
from app.security import UnsafeURLError, check_media_url, is_public_ip, platform_for_url


@pytest.mark.parametrize("url,platform", [
    ("https://www.instagram.com/reel/C1a2b3c4d5/", "instagram"),
    ("https://instagram.com/p/C1a2b3c4d5/?igsh=abc", "instagram"),
    ("https://www.threads.com/@baraoonline.oficial/post/C1a2b3c4d5", "threads"),
    ("https://threads.net/@x/post/C1a2b3c4d5", "threads"),
])
def test_accepts_supported(url, platform):
    assert platform_for_url(url)[0] == platform


@pytest.mark.parametrize("url", [
    "http://www.instagram.com/p/abc/",            # sem HTTPS
    "https://www.instagram.com:8443/p/abc/",       # porta
    "https://user:pw@www.instagram.com/p/abc/",    # credenciais
    "https://instagram.com.evil.com/p/abc/",       # domínio parecido
    "https://evilinstagram.com/p/abc/",
    "https://127.0.0.1/p/abc/",                    # IP direto
    "https://[::1]/p/abc/",
    "https://localhost/p/abc/",
    "https://metadata.google.internal/computeMetadata/v1/",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "https://www.instagram.com/p/a b/",
    "",
])
def test_rejects_unsafe(url):
    with pytest.raises(UnsafeURLError):
        platform_for_url(url)


@pytest.mark.parametrize("url", [
    "https://scontent.cdninstagram.com/v/t51/abc.jpg?oe=1",
    "https://instagram.fgru1-1.fna.fbcdn.net/v/abc.mp4",
])
def test_media_cdn_ok(url):
    assert check_media_url(url) == url


@pytest.mark.parametrize("url", [
    "https://example.com/a.mp4", "https://fbcdn.net.evil.com/a.mp4", "http://scontent.cdninstagram.com/a.jpg",
    "https://169.254.169.254/latest/meta-data/",
])
def test_media_cdn_rejected(url):
    with pytest.raises(UnsafeURLError):
        check_media_url(url)


@pytest.mark.parametrize("ip,public", [
    ("8.8.8.8", True), ("157.240.1.1", True), ("10.0.0.1", False), ("192.168.1.1", False),
    ("172.16.5.4", False), ("127.0.0.1", False), ("169.254.169.254", False), ("100.64.0.1", False),
    ("0.0.0.0", False), ("::1", False), ("fd00::1", False), ("fe80::1", False), ("::ffff:127.0.0.1", False),
    ("::ffff:10.0.0.1", False), ("2a03:2880:f12f::1", True),
])
def test_is_public_ip(ip, public):
    assert is_public_ip(ip) is public


def test_socket_guard_blocks_internal(monkeypatch):
    def fake(host, port, *a, **k):
        table = {"interno.exemplo": "10.1.2.3", "publico.exemplo": "157.240.1.1"}
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (table[host], port))]

    monkeypatch.setattr(security, "_ORIGINAL_GETADDRINFO", fake)
    with pytest.raises(socket.gaierror):
        security._guarded_getaddrinfo("interno.exemplo", 443)
    assert security._guarded_getaddrinfo("publico.exemplo", 443)[0][4][0] == "157.240.1.1"
    with pytest.raises(UnsafeURLError):
        security.resolve_public("interno.exemplo")


def test_socket_guard_installed_blocks_loopback():
    """Instala o guarda de verdade no processo e tenta resolver localhost para conectar."""
    original = socket.getaddrinfo
    try:
        security._guard_installed = False
        security.install_socket_guard()
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("localhost", 80)
        with pytest.raises(socket.gaierror):
            socket.getaddrinfo("169.254.169.254", 80)
        # bind do próprio servidor (AI_PASSIVE) continua permitido
        assert socket.getaddrinfo("0.0.0.0", 8000, 0, socket.SOCK_STREAM, 0, socket.AI_PASSIVE)
    finally:
        socket.getaddrinfo = original
        security._guard_installed = False
