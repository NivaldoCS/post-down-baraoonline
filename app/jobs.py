"""Jobs isolados por sessão, arquivos temporários e limpeza automática."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.adapters.base import ExtractionError, PlatformAdapter, PostResult
from app.config import Settings
from app.httpfetch import download_media
from app.media import probe, sniff_ext

log = logging.getLogger("postdown.jobs")


@dataclass
class ItemState:
    state: str = "listed"  # listed | preparing | ready | error
    path: Path | None = None
    info: dict[str, Any] | None = None
    error: dict[str, str] | None = None


@dataclass
class Job:
    id: str
    session_id: str
    post: PostResult
    dir: Path
    created_at: float
    expires_at: float
    items: dict[int, ItemState] = field(default_factory=dict)
    zip: ItemState = field(default_factory=ItemState)
    thumbs: dict[int, Path] = field(default_factory=dict)

    def file_name(self, index: int, ext: str) -> str:
        return f"{self.post.platform}_{self.post.shortcode}_{index + 1:02d}.{ext}"

    def zip_name(self) -> str:
        return f"{self.post.platform}_{self.post.shortcode}.zip"


def _session_dir_name(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()[:24]


class JobManager:
    def __init__(self, settings: Settings, adapters: dict[str, PlatformAdapter]):
        self.s = settings
        self.adapters = adapters
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._sem: asyncio.Semaphore | None = None
        self._extract_hits: dict[str, list[float]] = {}
        self._active: dict[str, int] = {}
        self._tasks: set[asyncio.Task] = set()

    # ---------- ciclo de vida ----------
    def startup(self) -> None:
        self.s.temp_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Sem cópias permanentes: qualquer sobra de execução anterior é apagada.
        for child in self.s.temp_dir.iterdir():
            shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        self._sem = asyncio.Semaphore(self.s.max_concurrent_jobs)

    @property
    def sem(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.s.max_concurrent_jobs)
        return self._sem

    def cleanup(self) -> int:
        now = time.time()
        with self._lock:
            expired = [j for j in self.jobs.values() if j.expires_at <= now]
            for j in expired:
                del self.jobs[j.id]
        for j in expired:
            shutil.rmtree(j.dir, ignore_errors=True)
        # Remove diretórios de sessão vazios
        if self.s.temp_dir.exists():
            for d in self.s.temp_dir.iterdir():
                if d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
        return len(expired)

    def drop_session(self, session_id: str) -> None:
        with self._lock:
            mine = [j for j in self.jobs.values() if j.session_id == session_id]
            for j in mine:
                del self.jobs[j.id]
        for j in mine:
            shutil.rmtree(j.dir, ignore_errors=True)

    async def cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(self.s.cleanup_interval_seconds)
            try:
                n = self.cleanup()
                if n:
                    log.info("limpeza: %d job(s) expirado(s) removido(s)", n)
            except Exception:  # nunca derrubar o loop
                log.exception("falha na limpeza")

    # ---------- limites ----------
    def check_extract_rate(self, session_id: str) -> None:
        now = time.time()
        with self._lock:
            hits = [t for t in self._extract_hits.get(session_id, []) if now - t < 60]
            if len(hits) >= self.s.max_extractions_per_minute:
                raise ExtractionError("TOO_MANY_REQUESTS", "Muitas buscas em pouco tempo. Aguarde um minuto.")
            hits.append(now)
            self._extract_hits[session_id] = hits

    def _enter(self, session_id: str) -> None:
        with self._lock:
            if self._active.get(session_id, 0) >= self.s.max_active_jobs_per_session:
                raise ExtractionError("TOO_MANY_REQUESTS", "Aguarde terminar os arquivos em preparo.")
            self._active[session_id] = self._active.get(session_id, 0) + 1

    def _leave(self, session_id: str) -> None:
        with self._lock:
            self._active[session_id] = max(0, self._active.get(session_id, 1) - 1)

    # ---------- operações ----------
    async def extract(self, session_id: str, platform: str, url: str) -> Job:
        self.check_extract_rate(session_id)
        adapter = self.adapters.get(platform)
        if not adapter or not adapter.enabled:
            raise ExtractionError("UNSUPPORTED_PLATFORM", "Plataforma ainda não suportada.")
        self._enter(session_id)
        try:
            async with self.sem:
                post = await asyncio.wait_for(asyncio.to_thread(adapter.extract, url),
                                              timeout=self.s.extract_timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise ExtractionError("TIMEOUT", "A consulta à publicação excedeu o tempo limite.") from exc
        finally:
            self._leave(session_id)
        job_id = secrets.token_urlsafe(16)
        jdir = self.s.temp_dir / _session_dir_name(session_id) / job_id
        jdir.mkdir(parents=True, mode=0o700)
        now = time.time()
        job = Job(job_id, session_id, post, jdir, now, now + self.s.job_ttl_seconds,
                  items={it.index: ItemState() for it in post.items})
        with self._lock:
            self.jobs[job_id] = job
        log.info("extração ok: %s %s (%d itens, %s)", post.platform, post.shortcode, len(post.items), post.extractor)
        return job

    def get(self, job_id: str, session_id: str) -> Job:
        with self._lock:
            job = self.jobs.get(job_id)
        if not job or job.session_id != session_id or job.expires_at <= time.time():
            raise ExtractionError("JOB_EXPIRED", "Esta busca expirou. Busque a publicação novamente.")
        return job

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def start_prepare(self, job: Job, index: int) -> ItemState:
        st = job.items.get(index)
        if st is None:
            raise ExtractionError("NOT_FOUND", "Item inexistente nesta publicação.")
        if st.state in ("preparing", "ready"):
            return st
        self._enter(job.session_id)
        st.state, st.error = "preparing", None
        self._spawn(self._prepare(job, index, release=True))
        return st

    async def _prepare(self, job: Job, index: int, release: bool) -> None:
        st = job.items[index]
        item = next(i for i in job.post.items if i.index == index)
        adapter = self.adapters[job.post.platform]
        base = f"item_{index + 1:02d}"
        try:
            async with self.sem:
                st.path, st.info = await asyncio.wait_for(
                    asyncio.to_thread(self._download_and_probe, adapter, item, job, base),
                    timeout=self.s.download_timeout_seconds + 30)
            st.state = "ready"
        except asyncio.TimeoutError:
            st.state, st.error = "error", {"code": "TIMEOUT", "message": "O preparo do arquivo excedeu o tempo limite."}
        except ExtractionError as exc:
            log.info("preparo falhou: %s item %d: %s", job.post.shortcode, index, exc.code)
            st.state, st.error = "error", {"code": exc.code, "message": exc.message}
        except Exception:
            log.exception("erro inesperado no preparo")
            st.state, st.error = "error", {"code": "EXTRACTION_FAILED", "message": "Falha inesperada ao preparar o arquivo."}
        finally:
            if release:
                self._leave(job.session_id)

    def _download_and_probe(self, adapter: PlatformAdapter, item, job: Job, base: str) -> tuple[Path, dict]:
        kwargs = dict(max_bytes=self.s.max_file_bytes, timeout=self.s.download_timeout_seconds,
                      http_download=download_media)
        if job.post.platform == "instagram":
            kwargs["max_duration"] = self.s.max_video_seconds
        path = adapter.download(item, job.dir, base, **kwargs)
        real_ext = sniff_ext(path)
        if real_ext and path.suffix.lstrip(".") != real_ext:
            path = path.rename(path.with_suffix("." + real_ext))
        try:
            info = probe(path)
        except ValueError as exc:
            path.unlink(missing_ok=True)
            raise ExtractionError("INVALID_MEDIA", "O arquivo recebido não é uma mídia válida.") from exc
        if item.kind == "photo":
            info["duration"] = None
            info["has_audio"] = None
        elif (info.get("duration") or 0) > self.s.max_video_seconds:
            path.unlink(missing_ok=True)
            raise ExtractionError("TOO_LONG", "O vídeo excede a duração máxima configurada.")
        info["ext"] = path.suffix.lstrip(".")
        info["file_name"] = job.file_name(item.index, info["ext"])
        return path, info

    def start_zip(self, job: Job) -> ItemState:
        if len(job.post.items) < 2:
            raise ExtractionError("NOT_AVAILABLE", "O ZIP é oferecido para publicações com mais de um item.")
        if job.zip.state in ("preparing", "ready"):
            return job.zip
        self._enter(job.session_id)
        job.zip.state, job.zip.error = "preparing", None
        self._spawn(self._build_zip(job))
        return job.zip

    async def _build_zip(self, job: Job) -> None:
        try:
            for idx, st in sorted(job.items.items()):
                while st.state == "preparing":  # já em preparo por um pedido individual
                    await asyncio.sleep(0.5)
                if st.state == "ready":
                    continue
                st.state, st.error = "preparing", None
                await self._prepare(job, idx, release=False)
                if st.state != "ready":
                    raise ExtractionError(st.error["code"], f"Item {idx + 1}: {st.error['message']}")
            total = sum(st.path.stat().st_size for st in job.items.values())
            if total > self.s.max_zip_bytes:
                raise ExtractionError("TOO_LARGE", "O conjunto excede o limite de tamanho do ZIP.")
            zpath = job.dir / "conjunto.zip"
            await asyncio.to_thread(self._write_zip, job, zpath)
            job.zip.path = zpath
            job.zip.info = {"size_bytes": zpath.stat().st_size, "file_name": job.zip_name(), "count": len(job.items)}
            job.zip.state = "ready"
        except ExtractionError as exc:
            job.zip.state, job.zip.error = "error", {"code": exc.code, "message": exc.message}
        except Exception:
            log.exception("erro ao montar ZIP")
            job.zip.state, job.zip.error = "error", {"code": "EXTRACTION_FAILED", "message": "Falha ao montar o ZIP."}
        finally:
            self._leave(job.session_id)

    @staticmethod
    def _write_zip(job: Job, zpath: Path) -> None:
        # ZIP_STORED: os arquivos entram exatamente como vieram da origem, na ordem do carrossel.
        with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_STORED) as zf:
            for _idx, st in sorted(job.items.items()):
                zf.write(st.path, arcname=st.info["file_name"])

    async def thumbnail(self, job: Job, index: int) -> Path:
        if index in job.thumbs and job.thumbs[index].exists():
            return job.thumbs[index]
        item = next((i for i in job.post.items if i.index == index), None)
        if not item or not item.thumbnail_url:
            raise ExtractionError("NOT_FOUND", "Sem miniatura para este item.")
        path = await asyncio.to_thread(download_media, item.thumbnail_url, job.dir / f"thumb_{index + 1:02d}.img",
                                       max_bytes=15 * 1024 * 1024, timeout=30,
                                       referer=self.adapters[job.post.platform].referer)
        job.thumbs[index] = path
        return path


def serialize_job(job: Job) -> dict[str, Any]:
    base = f"/api/jobs/{job.id}"
    items = []
    for it in job.post.items:
        st = job.items[it.index]
        items.append({
            **it.public(),
            "state": st.state,
            "file": st.info if st.state == "ready" else None,
            "error": st.error,
            "thumb_url": f"{base}/items/{it.index}/thumb" if it.thumbnail_url else None,
            "download_url": f"{base}/items/{it.index}/file" if st.state == "ready" else None,
        })
    return {
        "job_id": job.id,
        "platform": job.post.platform,
        "source_url": job.post.source_url,
        "author": job.post.author,
        "caption": job.post.caption,
        "extractor": job.post.extractor,
        "expires_at": int(job.expires_at),
        "items": items,
        "zip": {
            "available": len(job.post.items) > 1,
            "state": job.zip.state,
            "file": job.zip.info if job.zip.state == "ready" else None,
            "error": job.zip.error,
            "download_url": f"{base}/zip" if job.zip.state == "ready" else None,
        },
    }
