"""Instagram: Reels, vídeos, fotos e carrosséis (inclusive mistos).

Estratégia avaliada (nov/2026):
- yt-dlp (extrator reescrito em jul/2026) consulta a API GraphQL pública "logged out"
  com impersonation (curl_cffi) e devolve o carrossel completo e ordenado: vídeos com
  formatos (progressivo e DASH) e fotos com as variantes de imagem. É o extrator primário.
- Instaloader é usado como alternativa quando o yt-dlp falha. O acesso anônimo dele
  tem histórico de erro 401 "Please wait a few minutes" (issues #2511, #2532), por isso
  não é o primário.
Nenhum dos dois recebe senha. Opcionalmente o servidor pode usar um arquivo de cookies
(INSTAGRAM_COOKIES_FILE) de uma conta secundária — ver README.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from app.adapters.base import ExtractionError, MediaItem, PlatformAdapter, PostResult
from app.security import PLATFORM_HOSTS, UnsafeURLError, check_media_url, platform_for_url, resolve_public

log = logging.getLogger("postdown.instagram")

POST_RE = re.compile(r"^/(?:[A-Za-z0-9._]{1,30}/)?(?P<kind>p|reel|reels|tv)/(?P<code>[A-Za-z0-9_-]{5,64})/?$")
SHARE_RE = re.compile(r"^/share/(?:(?:p|reel|reels|tv)/)?[A-Za-z0-9_-]+/?$")
STORY_RE = re.compile(r"^/stories/")
YTDLP_VIDEO_FORMAT = "bv*[protocol^=http]+ba[protocol^=http]/b[protocol^=http]"


def parse_shortcode(url: str) -> str:
    path = urlsplit(url).path
    if STORY_RE.match(path):
        raise ExtractionError("UNSUPPORTED_CONTENT", "Stories ainda não são suportados nesta versão.")
    m = POST_RE.match(path)
    if not m:
        raise ExtractionError("INVALID_URL", "Este link do Instagram não aponta para uma publicação, Reel ou vídeo.")
    return m.group("code")


def resolve_share_link(url: str, timeout: float = 15.0) -> str:
    """Resolve links /share/... e instagr.am seguindo redirecionamentos validados."""
    current = url
    with httpx.Client(follow_redirects=False, trust_env=False, timeout=timeout,
                      headers={"User-Agent": "Mozilla/5.0"}) as client:
        for _ in range(5):
            platform, current = platform_for_url(current)
            if platform != "instagram":
                raise ExtractionError("INVALID_URL", "O link redirecionou para fora do Instagram.")
            path = urlsplit(current).path
            if POST_RE.match(path) or STORY_RE.match(path):
                return current
            resolve_public(urlsplit(current).hostname or "")
            resp = client.get(current)
            if not resp.is_redirect:
                break
            current = urljoin(current, resp.headers.get("location", ""))
    raise ExtractionError("INVALID_URL", "Não foi possível identificar a publicação a partir deste link.")


def classify_error(message: str) -> ExtractionError:
    low = message.lower()
    if "rate-limit" in low or "rate limit" in low or "please wait" in low or "429" in low:
        return ExtractionError("RATE_LIMITED",
                               "O Instagram limitou consultas deste servidor. Tente novamente em alguns minutos.", message)
    if "only available for registered users" in low or "private" in low or "restricted" in low or "login" in low:
        return ExtractionError("RESTRICTED",
                               "A publicação é privada, restrita ou exige login. O Post-Down não contorna essas restrições.", message)
    if "not found" in low or "404" in low or "unavailable" in low or "does not exist" in low or "deleted" in low:
        return ExtractionError("NOT_FOUND", "A publicação não foi encontrada. Ela pode ter sido removida.", message)
    if "timed out" in low or "timeout" in low:
        return ExtractionError("TIMEOUT", "O Instagram demorou demais para responder.", message)
    return ExtractionError("EXTRACTION_FAILED", "Não foi possível extrair as mídias desta publicação.", message)


def _best_image(candidates: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    valid = [c for c in candidates or [] if isinstance(c, dict) and c.get("url")]
    if not valid:
        return None
    return max(valid, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0))


def _safe_media_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return check_media_url(url)
    except UnsafeURLError:
        log.warning("URL de mídia descartada (host fora da lista)")
        return None


def _entry_to_item(index: int, entry: dict[str, Any]) -> MediaItem | None:
    formats = [f for f in entry.get("formats") or [] if f.get("url")]
    thumb = _best_image(entry.get("thumbnails"))
    thumb_url = _safe_media_url(thumb and thumb.get("url"))
    if formats:
        for f in formats:
            if not _safe_media_url(f["url"]):
                raise ExtractionError("EXTRACTION_FAILED", "O extrator devolveu uma origem de vídeo não reconhecida.")
        progressive = [f for f in formats if f.get("acodec") != "none" and not str(f.get("format_id", "")).startswith("dash")]
        best_prog = max(progressive, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0), default=None)
        best_any = max(formats, key=lambda f: (f.get("width") or 0) * (f.get("height") or 0))
        return MediaItem(
            index=index, kind="video", width=best_any.get("width"), height=best_any.get("height"),
            duration=entry.get("duration"), ext="mp4", thumbnail_url=thumb_url,
            direct_url=best_prog and best_prog["url"], ytdlp_info=entry,
        )
    if thumb_url:
        return MediaItem(index=index, kind="photo", width=thumb.get("width"), height=thumb.get("height"),
                         ext="jpg", thumbnail_url=thumb_url, direct_url=thumb_url)
    return None


class InstagramAdapter(PlatformAdapter):
    name = "instagram"
    referer = "https://www.instagram.com/"

    def __init__(self, *, cookies_file: str = "", timeout: int = 30, max_items: int = 20):
        self.cookies_file = cookies_file
        self.timeout = timeout
        self.max_items = max_items

    # ---------- extração ----------
    def extract(self, url: str) -> PostResult:
        if urlsplit(url).hostname in {"instagr.am", "www.instagr.am"} or SHARE_RE.match(urlsplit(url).path):
            url = resolve_share_link(url)
        code = parse_shortcode(url)
        canonical = f"https://www.instagram.com/p/{code}/"
        try:
            return self._extract_ytdlp(canonical, code)
        except ExtractionError as first:
            if first.code in {"NOT_FOUND", "RESTRICTED", "UNSUPPORTED_CONTENT", "TOO_MANY_ITEMS"}:
                raise
            log.info("yt-dlp falhou (%s); tentando Instaloader", first.code)
            try:
                return self._extract_instaloader(canonical, code)
            except ExtractionError as second:
                log.info("Instaloader falhou (%s)", second.code)
                raise first from second

    def _ytdlp_opts(self) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "quiet": True, "no_warnings": True, "noprogress": True,
            "skip_download": True, "ignore_no_formats_error": True,
            "socket_timeout": self.timeout, "getcomments": False,
            "extractor_retries": 1, "noplaylist": False, "cachedir": False,
        }
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        return opts

    def _extract_ytdlp(self, canonical: str, code: str) -> PostResult:
        import yt_dlp

        try:
            with yt_dlp.YoutubeDL(self._ytdlp_opts()) as ydl:
                info = ydl.extract_info(canonical, download=False, process=False)
        except yt_dlp.utils.DownloadError as exc:
            raise classify_error(str(exc)) from exc
        except Exception as exc:  # erros internos do extrator
            raise classify_error(repr(exc)) from exc
        if not info:
            raise ExtractionError("EXTRACTION_FAILED", "O extrator não devolveu dados.")
        entries = list(info.get("entries") or []) if info.get("_type") == "playlist" else [info]
        if len(entries) > self.max_items:
            raise ExtractionError("TOO_MANY_ITEMS", f"A publicação tem mais de {self.max_items} itens.")
        items: list[MediaItem] = []
        for i, entry in enumerate(entries):
            entry = {**entry, "extractor": "instagram", "extractor_key": "Instagram", "webpage_url": canonical,
                     "title": entry.get("title") or f"{code}_{i + 1}", "id": entry.get("id") or f"{code}_{i + 1}"}
            item = _entry_to_item(i, entry)
            if item:
                items.append(item)
        if not items:
            raise ExtractionError("NO_MEDIA", "Nenhuma foto ou vídeo foi encontrado nesta publicação.")
        for n, it in enumerate(items):
            it.index = n
        return PostResult("instagram", canonical, code, info.get("channel"), info.get("description"), items, "yt-dlp")

    def _extract_instaloader(self, canonical: str, code: str) -> PostResult:
        import instaloader
        from instaloader.exceptions import (
            BadResponseException,
            ConnectionException,
            InstaloaderException,
            LoginRequiredException,
            QueryReturnedNotFoundException,
            TooManyRequestsException,
        )

        class FailFastRateController(instaloader.RateController):
            def sleep(self, secs):  # nunca segurar a requisição do usuário por minutos
                raise TooManyRequestsException("rate limit")

        L = instaloader.Instaloader(quiet=True, download_pictures=False, download_videos=False,
                                    download_video_thumbnails=False, save_metadata=False, compress_json=False,
                                    max_connection_attempts=1, request_timeout=self.timeout,
                                    rate_controller=lambda ctx: FailFastRateController(ctx))
        try:
            post = instaloader.Post.from_shortcode(L.context, code)
            nodes = list(post.get_sidecar_nodes()) if post.typename == "GraphSidecar" else None
            if nodes is None:
                raw = [(post.is_video, post.video_url if post.is_video else None, post.url)]
            else:
                raw = [(n.is_video, n.video_url, n.display_url) for n in nodes]
            caption, author = post.caption, post.owner_username
        except QueryReturnedNotFoundException as exc:
            raise ExtractionError("NOT_FOUND", "A publicação não foi encontrada. Ela pode ter sido removida.", str(exc)) from exc
        except LoginRequiredException as exc:
            raise ExtractionError("RESTRICTED", "A publicação é privada, restrita ou exige login.", str(exc)) from exc
        except TooManyRequestsException as exc:
            raise ExtractionError("RATE_LIMITED",
                                  "O Instagram limitou consultas deste servidor. Tente novamente em alguns minutos.", str(exc)) from exc
        except (ConnectionException, BadResponseException, InstaloaderException) as exc:
            raise classify_error(str(exc)) from exc
        if len(raw) > self.max_items:
            raise ExtractionError("TOO_MANY_ITEMS", f"A publicação tem mais de {self.max_items} itens.")
        items = []
        for i, (is_video, video_url, display_url) in enumerate(raw):
            display = _safe_media_url(display_url)
            if is_video:
                vurl = _safe_media_url(video_url)
                if vurl:
                    items.append(MediaItem(i, "video", ext="mp4", thumbnail_url=display, direct_url=vurl))
            elif display:
                items.append(MediaItem(i, "photo", ext="jpg", thumbnail_url=display, direct_url=display))
        if not items:
            raise ExtractionError("NO_MEDIA", "Nenhuma foto ou vídeo foi encontrado nesta publicação.")
        for n, it in enumerate(items):
            it.index = n
        return PostResult("instagram", canonical, code, author, caption, items, "instaloader")

    # ---------- download ----------
    def download(self, item: MediaItem, dest_dir: Path, base_name: str, *, max_bytes: int, timeout: int,
                 http_download: Callable[..., Path], max_duration: int | None = None) -> Path:
        if item.kind == "video" and item.ytdlp_info:
            try:
                return ytdlp_download(item.ytdlp_info, dest_dir, base_name, max_bytes=max_bytes,
                                      timeout=timeout, cookies_file=self.cookies_file, max_duration=max_duration)
            except ExtractionError as exc:
                if exc.code in {"TOO_LARGE", "TOO_LONG"} or not item.direct_url:
                    raise
                log.info("download via yt-dlp falhou (%s); usando arquivo progressivo", exc.code)
        return super().download(item, dest_dir, base_name, max_bytes=max_bytes, timeout=timeout,
                                http_download=http_download)


def ytdlp_download(info: dict[str, Any], dest_dir: Path, base_name: str, *, max_bytes: int, timeout: int,
                   cookies_file: str = "", max_duration: int | None = None) -> Path:
    """Baixa um vídeo já resolvido pelo yt-dlp, unindo vídeo+áudio com FFmpeg (cópia, sem recompressão)."""
    import yt_dlp

    for f in info.get("formats") or []:
        if f.get("url"):
            check_media_url(f["url"])
    if max_duration and (info.get("duration") or 0) > max_duration:
        raise ExtractionError("TOO_LONG", "O vídeo excede a duração máxima configurada.")
    opts: dict[str, Any] = {
        "quiet": True, "no_warnings": True, "noprogress": True,
        "format": YTDLP_VIDEO_FORMAT, "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / f"{base_name}.%(ext)s"),
        "max_filesize": max_bytes, "socket_timeout": min(timeout, 60),
        "overwrites": True, "noplaylist": True, "cachedir": False, "retries": 2, "fragment_retries": 2,
        "postprocessors": [],  # nenhum reprocessamento além da união de faixas
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            result = ydl.process_ie_result(dict(info), download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise classify_error(str(exc)) from exc
    downloads = (result or {}).get("requested_downloads") or []
    path = Path(downloads[0]["filepath"]) if downloads and downloads[0].get("filepath") else None
    if not path or not path.exists():
        raise ExtractionError("TOO_LARGE" if result else "EXTRACTION_FAILED",
                              "O arquivo não foi gerado (pode exceder o limite de tamanho).")
    return path


__all__ = ["InstagramAdapter", "parse_shortcode", "classify_error", "ytdlp_download", "PLATFORM_HOSTS"]
