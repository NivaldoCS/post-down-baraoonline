"""Configuração lida exclusivamente de variáveis de ambiente.

Nenhum segredo tem valor padrão: sem APP_PASSWORD_HASH e SESSION_SECRET o
servidor não inicia (exceto em modo de teste).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "sim", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "Post-Down Baraoonline.oficial"
    # Autenticação
    password_hash: str = ""
    session_secret: str = ""
    session_ttl_seconds: int = 60 * 60 * 12
    cookie_secure: bool = True
    login_max_attempts: int = 5
    login_window_seconds: int = 15 * 60
    # Armazenamento temporário
    temp_dir: Path = Path("/tmp/post-down")
    job_ttl_seconds: int = 15 * 60
    cleanup_interval_seconds: int = 60
    # Limites
    max_concurrent_jobs: int = 2
    max_active_jobs_per_session: int = 3
    max_extractions_per_minute: int = 6
    max_items_per_post: int = 20
    max_file_bytes: int = 500 * 1024 * 1024
    max_zip_bytes: int = 1024 * 1024 * 1024
    max_video_seconds: int = 30 * 60
    extract_timeout_seconds: int = 45
    download_timeout_seconds: int = 180
    # Extratores (opcional, nunca senha): arquivo de cookies Netscape de uma
    # conta secundária, lido apenas pelo servidor.
    instagram_cookies_file: str = ""
    trusted_hosts: list[str] = field(default_factory=list)
    testing: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        mb = 1024 * 1024
        return cls(
            password_hash=os.environ.get("APP_PASSWORD_HASH", ""),
            session_secret=os.environ.get("SESSION_SECRET", ""),
            session_ttl_seconds=_int("SESSION_TTL_SECONDS", 60 * 60 * 12),
            cookie_secure=_bool("COOKIE_SECURE", True),
            login_max_attempts=_int("LOGIN_MAX_ATTEMPTS", 5),
            login_window_seconds=_int("LOGIN_WINDOW_SECONDS", 15 * 60),
            temp_dir=Path(os.environ.get("TEMP_DIR", "/tmp/post-down")),
            job_ttl_seconds=_int("JOB_TTL_SECONDS", 15 * 60),
            cleanup_interval_seconds=_int("CLEANUP_INTERVAL_SECONDS", 60),
            max_concurrent_jobs=_int("MAX_CONCURRENT_JOBS", 2),
            max_active_jobs_per_session=_int("MAX_ACTIVE_JOBS_PER_SESSION", 3),
            max_extractions_per_minute=_int("MAX_EXTRACTIONS_PER_MINUTE", 6),
            max_items_per_post=_int("MAX_ITEMS_PER_POST", 20),
            max_file_bytes=_int("MAX_FILE_MB", 500) * mb,
            max_zip_bytes=_int("MAX_ZIP_MB", 1024) * mb,
            max_video_seconds=_int("MAX_VIDEO_SECONDS", 30 * 60),
            extract_timeout_seconds=_int("EXTRACT_TIMEOUT_SECONDS", 45),
            download_timeout_seconds=_int("DOWNLOAD_TIMEOUT_SECONDS", 180),
            instagram_cookies_file=os.environ.get("INSTAGRAM_COOKIES_FILE", ""),
            trusted_hosts=[h.strip() for h in os.environ.get("TRUSTED_HOSTS", "").split(",") if h.strip()],
            testing=_bool("TESTING", False),
        )

    def validate(self) -> None:
        if self.testing:
            return
        missing = [n for n, v in (("APP_PASSWORD_HASH", self.password_hash), ("SESSION_SECRET", self.session_secret)) if not v]
        if missing:
            raise RuntimeError(
                "Configuração ausente: " + ", ".join(missing)
                + ". Gere com `python -m app.hash_password` e defina no .env do servidor."
            )
        if len(self.session_secret) < 32:
            raise RuntimeError("SESSION_SECRET deve ter pelo menos 32 caracteres.")
