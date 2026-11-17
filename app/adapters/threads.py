"""Threads: fotos, vídeos e publicações com várias mídias.

Nem yt-dlp nem gallery-dl têm extrator para Threads (verificado em nov/2026:
yt-dlp 2026.10.26, gallery-dl 1.32.20). A página pública de uma publicação traz os
dados do post em blocos <script type="application/json" data-sjs> (o mesmo padrão
"hidden web data" usado pelo próprio extrator do Instagram no yt-dlp). Este
adaptador baixa a página, lê esses blocos e localiza o objeto do post pelo código.
É a parte mais sujeita a mudanças da Meta — ver README, "Atualizar extratores".
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterator
from urllib.parse import urlsplit

from app.adapters.base import ExtractionError, MediaItem, PlatformAdapter, PostResult
from app.httpfetch import follow_redirects
from app.security import UnsafeURLError, check_media_url, platform_for_url

log = logging.getLogger("postdown.threads")

POST_RE = re.compile(r"^/(?:@(?P<user>[A-Za-z0-9._]{1,30})/post|t)/(?P<code>[A-Za-z0-9_-]{5,64})/?(?:media/?)?$")
SJS_RE = re.compile(r"<script[^>]*\bdata-sjs\b[^>]*>(.*?)</script>", re.S | re.I)
JSON_SCRIPT_RE = re.compile(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S | re.I)


def parse_code(url: str) -> str:
    m = POST_RE.match(urlsplit(url).path)
    if not m:
        raise ExtractionError("INVALID_URL", "Este link do Threads não aponta para uma publicação.")
    return m.group("code")


def _walk(obj: Any) -> Iterator[dict[str, Any]]:
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def _has_media(d: dict[str, Any]) -> bool:
    return bool(d.get("carousel_media") or d.get("video_versions")
                or (d.get("image_versions2") or {}).get("candidates"))


def find_post(html: str, code: str) -> dict[str, Any] | None:
    blobs = SJS_RE.findall(html) or JSON_SCRIPT_RE.findall(html)
    fallback = None
    for blob in blobs:
        if code not in blob:
            continue
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        for d in _walk(data):
            if d.get("code") == code:
                if _has_media(d):
                    return d
                fallback = fallback or d
    return fallback


def _best(cands: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    valid = [c for c in cands or [] if isinstance(c, dict) and c.get("url")]
    if not valid:
        return None
    # Algumas versões de vídeo não trazem dimensões; nesse caso a primeira é a principal.
    if all(not c.get("width") for c in valid):
        return valid[0]
    return max(valid, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0))


def _safe(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return check_media_url(url)
    except UnsafeURLError:
        return None


def media_to_item(index: int, m: dict[str, Any]) -> MediaItem | None:
    img = _best((m.get("image_versions2") or {}).get("candidates"))
    thumb = _safe(img and img.get("url"))
    video = _best(m.get("video_versions"))
    if video and _safe(video["url"]):
        return MediaItem(index=index, kind="video",
                         width=video.get("width") or m.get("original_width"),
                         height=video.get("height") or m.get("original_height"),
                         duration=m.get("video_duration"), ext="mp4",
                         thumbnail_url=thumb, direct_url=video["url"])
    if thumb:
        return MediaItem(index=index, kind="photo", width=img.get("width") or m.get("original_width"),
                         height=img.get("height") or m.get("original_height"), ext="jpg",
                         thumbnail_url=thumb, direct_url=thumb)
    return None


def post_to_result(post: dict[str, Any], code: str, canonical: str, max_items: int) -> PostResult:
    medias = post.get("carousel_media") or [post]
    if len(medias) > max_items:
        raise ExtractionError("TOO_MANY_ITEMS", f"A publicação tem mais de {max_items} itens.")
    items = [it for i, m in enumerate(medias) if isinstance(m, dict) and (it := media_to_item(i, m))]
    if not items:
        raise ExtractionError("NO_MEDIA", "Esta publicação do Threads não tem fotos ou vídeos.")
    for n, it in enumerate(items):
        it.index = n
    caption = (post.get("caption") or {}).get("text") if isinstance(post.get("caption"), dict) else None
    author = (post.get("user") or {}).get("username") if isinstance(post.get("user"), dict) else None
    return PostResult("threads", canonical, code, author, caption, items, "threads-html")


class ThreadsAdapter(PlatformAdapter):
    name = "threads"
    referer = "https://www.threads.com/"

    def __init__(self, *, timeout: int = 30, max_items: int = 20):
        self.timeout = timeout
        self.max_items = max_items

    def extract(self, url: str) -> PostResult:
        code = parse_code(url)

        def validate(u: str) -> None:
            platform, _ = platform_for_url(u)
            if platform != "threads":
                raise UnsafeURLError("Redirecionou para fora do Threads")

        try:
            resp = follow_redirects(url, validate, timeout=self.timeout)
        except UnsafeURLError as exc:
            raise ExtractionError("INVALID_URL", "O link redirecionou para um destino não permitido.", str(exc)) from exc
        except Exception as exc:  # httpx
            if "timed out" in str(exc).lower() or exc.__class__.__name__.endswith("Timeout"):
                raise ExtractionError("TIMEOUT", "O Threads demorou demais para responder.", repr(exc)) from exc
            raise ExtractionError("EXTRACTION_FAILED", "Não foi possível acessar o Threads.", repr(exc)) from exc
        if resp.status_code == 404:
            raise ExtractionError("NOT_FOUND", "A publicação não foi encontrada. Ela pode ter sido removida.")
        if resp.status_code == 429:
            raise ExtractionError("RATE_LIMITED", "O Threads limitou consultas deste servidor. Tente em alguns minutos.")
        if resp.status_code != 200:
            raise ExtractionError("EXTRACTION_FAILED", f"O Threads respondeu {resp.status_code}.")
        final_path = urlsplit(str(resp.url)).path
        if final_path.startswith("/login"):
            raise ExtractionError("RESTRICTED", "O Threads exigiu login para esta publicação. O Post-Down não contorna essa restrição.")
        post = find_post(resp.text, code)
        if not post:
            raise ExtractionError("EXTRACTION_FAILED",
                                  "A página do Threads não trouxe os dados da publicação (formato pode ter mudado ou o post é restrito).")
        host = urlsplit(str(resp.url)).hostname or "www.threads.com"
        canonical = f"https://{host}{final_path}"
        return post_to_result(post, code, canonical, self.max_items)
