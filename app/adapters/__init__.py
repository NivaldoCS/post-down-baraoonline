"""Registro de adaptadores por plataforma.

Facebook, TikTok, X e YouTube estão preparados como entradas desativadas: a interface
não os anuncia e a API recusa os links até que um adaptador seja implementado E
validado com downloads reais (ver docs/ADAPTADORES.md).
"""
from __future__ import annotations

from app.adapters.base import ExtractionError, MediaItem, PlatformAdapter, PostResult
from app.config import Settings

PLANNED_PLATFORMS = {
    "facebook": {"facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch"},
    "tiktok": {"tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"},
    "x": {"x.com", "twitter.com", "www.x.com", "mobile.twitter.com"},
    "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"},
}


def build_registry(settings: Settings) -> dict[str, PlatformAdapter]:
    from app.adapters.instagram import InstagramAdapter
    from app.adapters.threads import ThreadsAdapter

    timeout = min(settings.extract_timeout_seconds, 40)
    return {
        "instagram": InstagramAdapter(cookies_file=settings.instagram_cookies_file, timeout=timeout,
                                      max_items=settings.max_items_per_post),
        "threads": ThreadsAdapter(timeout=timeout, max_items=settings.max_items_per_post),
    }


__all__ = ["ExtractionError", "MediaItem", "PlatformAdapter", "PostResult", "build_registry", "PLANNED_PLATFORMS"]
