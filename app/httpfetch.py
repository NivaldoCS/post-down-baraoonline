"""Cliente HTTP restrito: redirecionamentos validados a cada salto, limite de tamanho e de tempo."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from app.adapters.base import ExtractionError
from app.security import UnsafeURLError, check_media_url, resolve_public

BROWSER_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1"
)
MAX_REDIRECTS = 5


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(
        follow_redirects=False, trust_env=False,
        timeout=httpx.Timeout(timeout, connect=10.0),
        headers={"User-Agent": BROWSER_UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
    )


def follow_redirects(url: str, validate: Callable[[str], object], *, timeout: float = 15.0,
                     method: str = "GET") -> httpx.Response:
    """Faz a requisição seguindo redirecionamentos manualmente; cada destino passa por `validate`."""
    current = url
    with _client(timeout) as client:
        for _ in range(MAX_REDIRECTS + 1):
            validate(current)
            resolve_public(urlsplit(current).hostname or "")
            resp = client.request(method, current)
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise ExtractionError("EXTRACTION_FAILED", "Redirecionamento sem destino.")
                current = urljoin(current, location)
                continue
            resp.read()
            return resp
    raise ExtractionError("EXTRACTION_FAILED", "Redirecionamentos demais.")


def download_media(url: str, dest: Path, *, max_bytes: int, timeout: int, referer: str | None = None) -> Path:
    """Baixa um arquivo de CDN permitido para `dest`, sem recompressão."""
    deadline = time.monotonic() + timeout
    current = url
    headers = {"Referer": referer} if referer else {}
    with _client(min(timeout, 60)) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                check_media_url(current)
                resolve_public(urlsplit(current).hostname or "")
            except UnsafeURLError as exc:
                raise ExtractionError("EXTRACTION_FAILED", "Endereço de mídia recusado pela proteção do servidor.", str(exc)) from exc
            with client.stream("GET", current, headers=headers) as resp:
                if resp.is_redirect:
                    current = urljoin(current, resp.headers.get("location", ""))
                    continue
                if resp.status_code in (403, 410):
                    raise ExtractionError("EXPIRED", "O link temporário da mídia expirou. Busque a publicação novamente.")
                if resp.status_code == 429:
                    raise ExtractionError("RATE_LIMITED", "A plataforma limitou as requisições. Tente em alguns minutos.")
                if resp.status_code != 200:
                    raise ExtractionError("EXTRACTION_FAILED", f"O CDN respondeu {resp.status_code}.")
                declared = int(resp.headers.get("content-length") or 0)
                if declared > max_bytes:
                    raise ExtractionError("TOO_LARGE", "O arquivo excede o limite configurado no servidor.")
                total = 0
                tmp = dest.with_suffix(dest.suffix + ".part")
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_bytes(64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            fh.close(); tmp.unlink(missing_ok=True)
                            raise ExtractionError("TOO_LARGE", "O arquivo excede o limite configurado no servidor.")
                        if time.monotonic() > deadline:
                            fh.close(); tmp.unlink(missing_ok=True)
                            raise ExtractionError("TIMEOUT", "O download demorou mais que o permitido.")
                        fh.write(chunk)
                if total == 0:
                    tmp.unlink(missing_ok=True)
                    raise ExtractionError("EXTRACTION_FAILED", "O CDN devolveu um arquivo vazio.")
                tmp.replace(dest)
                return dest
    raise ExtractionError("EXTRACTION_FAILED", "Redirecionamentos demais.")
