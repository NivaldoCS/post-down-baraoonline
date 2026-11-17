"""Tipos comuns a todos os adaptadores de plataforma."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

MediaKind = Literal["video", "photo"]


class ExtractionError(Exception):
    """Erro com código estável exibido ao usuário pelo frontend."""

    def __init__(self, code: str, message: str, detail: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail  # apenas para log do servidor; nunca contém credenciais


@dataclass
class MediaItem:
    index: int
    kind: MediaKind
    # Metadados informados pela plataforma (podem faltar; o arquivo real é medido depois).
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    ext: str | None = None
    thumbnail_url: str | None = None
    # Para fotos e vídeos progressivos: URL direta no CDN (validada antes do uso).
    direct_url: str | None = None
    # Para vídeos via yt-dlp: info dict já resolvido (evita nova consulta à plataforma).
    ytdlp_info: dict[str, Any] | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "reported": {"width": self.width, "height": self.height, "duration": self.duration, "ext": self.ext},
        }


@dataclass
class PostResult:
    platform: str
    source_url: str
    shortcode: str
    author: str | None
    caption: str | None
    items: list[MediaItem]
    extractor: str


class PlatformAdapter:
    name: str = "base"
    enabled: bool = True

    def extract(self, url: str) -> PostResult:  # pragma: no cover - interface
        raise NotImplementedError

    def download(self, item: MediaItem, dest_dir: Path, base_name: str, *, max_bytes: int,
                 timeout: int, http_download: Callable[..., Path]) -> Path:
        """Baixa um item para dest_dir. Implementação padrão: URL direta no CDN."""
        if not item.direct_url:
            raise ExtractionError("EXTRACTION_FAILED", "A plataforma não informou o arquivo deste item.")
        ext = item.ext or ("mp4" if item.kind == "video" else "jpg")
        return http_download(item.direct_url, dest_dir / f"{base_name}.{ext}", max_bytes=max_bytes, timeout=timeout,
                             referer=self.referer)

    referer: str | None = None
