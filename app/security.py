"""Validação de URLs e proteção contra SSRF.

Camadas:
1. Links de entrada: só HTTPS, porta 443, sem usuário/senha, domínio na lista
   das plataformas suportadas.
2. URLs de mídia devolvidas pelos extratores: só CDNs conhecidos da Meta.
3. Toda resolução DNS do processo Python passa por `install_socket_guard`,
   que recusa endereços privados, locais, reservados e de metadados de nuvem —
   isso cobre httpx, urllib/requests usados pelo yt-dlp e o Instaloader.
   (curl_cffi, usado pelo yt-dlp para "impersonation", resolve DNS em C; por
   isso as URLs são validadas antes e os hosts consultados por ele são fixos
   do próprio Instagram.)
4. Redirecionamentos são seguidos manualmente e cada salto é revalidado.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
from urllib.parse import urlsplit, urlunsplit

PLATFORM_HOSTS: dict[str, set[str]] = {
    "instagram": {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am", "www.instagr.am"},
    "threads": {"threads.com", "www.threads.com", "threads.net", "www.threads.net"},
}

# Sufixos de CDN aceitos para baixar mídia (sempre comparados por sufixo de domínio).
MEDIA_HOST_SUFFIXES = ("cdninstagram.com", "fbcdn.net")

METADATA_HOSTS = {"metadata.google.internal", "metadata", "instance-data", "metadata.azure.com"}


class UnsafeURLError(ValueError):
    """URL recusada pelas regras de segurança."""


def _host_matches_suffix(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
    )


def _parse_https(url: str) -> tuple[str, object]:
    if not isinstance(url, str) or len(url) > 2048:
        raise UnsafeURLError("URL ausente ou longa demais")
    url = url.strip()
    if any(c in url for c in "\r\n\t\x00 "):
        raise UnsafeURLError("URL com caracteres inválidos")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UnsafeURLError("Somente HTTPS é aceito")
    if parts.username or parts.password:
        raise UnsafeURLError("URL com credenciais")
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError("Porta inválida") from exc
    if port not in (None, 443):
        raise UnsafeURLError("Porta não permitida")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("Domínio ausente")
    try:
        ipaddress.ip_address(host)
        raise UnsafeURLError("Endereço IP direto não é aceito")
    except ValueError:
        pass
    if host in METADATA_HOSTS or host.endswith(".internal") or host.endswith(".local") or host == "localhost":
        raise UnsafeURLError("Domínio interno")
    return host, parts


def platform_for_url(url: str) -> tuple[str, str]:
    """Valida um link de publicação. Retorna (plataforma, url_normalizada)."""
    host, parts = _parse_https(url)
    for platform, hosts in PLATFORM_HOSTS.items():
        if host in hosts:
            normalized = urlunsplit(("https", host, parts.path or "/", parts.query, ""))
            return platform, normalized
    raise UnsafeURLError("Plataforma não suportada")


def check_media_url(url: str) -> str:
    """Valida uma URL de mídia vinda do extrator (nunca do usuário)."""
    host, _ = _parse_https(url)
    if not _host_matches_suffix(host, MEDIA_HOST_SUFFIXES):
        raise UnsafeURLError(f"Host de mídia não permitido: {host}")
    return url


def resolve_public(host: str, port: int = 443) -> list[str]:
    """Resolve o host e exige que TODOS os endereços sejam públicos."""
    infos = _ORIGINAL_GETADDRINFO(host, port, 0, socket.SOCK_STREAM)
    addrs = sorted({i[4][0] for i in infos})
    if not addrs:
        raise UnsafeURLError("Domínio sem endereço")
    bad = [a for a in addrs if not is_public_ip(a)]
    if bad:
        raise UnsafeURLError("Domínio resolve para endereço interno")
    return addrs


_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_guard_lock = threading.Lock()
_guard_installed = False


def _guarded_getaddrinfo(host, port, *args, **kwargs):
    results = _ORIGINAL_GETADDRINFO(host, port, *args, **kwargs)
    safe = [r for r in results if is_public_ip(r[4][0])]
    if not safe:
        raise socket.gaierror(socket.EAI_NONAME, f"Endereço bloqueado pela política de segurança: {host}")
    return safe


def install_socket_guard(allow_local: tuple[str, ...] = ()) -> None:
    """Substitui socket.getaddrinfo no processo para recusar destinos internos.

    `allow_local` existe apenas para que o servidor de testes local continue
    acessível; em produção fica vazio.
    """
    global _guard_installed
    with _guard_lock:
        if _guard_installed:
            return

        def guard(host, port, family=0, type=0, proto=0, flags=0):
            # AI_PASSIVE = resolução para *escutar* (bind do próprio servidor), não para conectar.
            if host is None or (flags & socket.AI_PASSIVE) or host in allow_local:
                return _ORIGINAL_GETADDRINFO(host, port, family, type, proto, flags)
            return _guarded_getaddrinfo(host, port, family, type, proto, flags)

        socket.getaddrinfo = guard
        _guard_installed = True
