"""Post-Down Baraoonline.oficial — servidor FastAPI (API + interface)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.adapters import PLANNED_PLATFORMS, ExtractionError, build_registry
from app.auth import LoginLimiter, Session, SessionStore, verify_password
from app.config import Settings
from app.jobs import JobManager, serialize_job
from app.media import sniff_ext
from app.security import UnsafeURLError, install_socket_guard, platform_for_url

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
log = logging.getLogger("postdown")

HTTP_STATUS = {
    "EMPTY_URL": 400, "INVALID_URL": 400, "UNSUPPORTED_PLATFORM": 400, "UNSUPPORTED_CONTENT": 400,
    "NOT_FOUND": 404, "RESTRICTED": 403, "RATE_LIMITED": 429, "TOO_MANY_REQUESTS": 429,
    "EXTRACTION_FAILED": 502, "NO_MEDIA": 422, "TIMEOUT": 504, "TOO_LARGE": 413, "TOO_LONG": 413,
    "TOO_MANY_ITEMS": 413, "INVALID_MEDIA": 502, "EXPIRED": 410, "JOB_EXPIRED": 410,
    "NOT_READY": 409, "NOT_AVAILABLE": 400, "UNAUTHORIZED": 401, "FORBIDDEN": 403,
}


def error_response(code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=HTTP_STATUS.get(code, 400))


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class ExtractBody(BaseModel):
    url: str = Field(default="", max_length=2048)


def create_app(settings: Settings | None = None, adapters=None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    sessions = SessionStore(settings.session_secret, settings.session_ttl_seconds)
    limiter = LoginLimiter(settings.login_max_attempts, settings.login_window_seconds)
    jobs = JobManager(settings, adapters if adapters is not None else build_registry(settings))
    cookie_name = "__Host-postdown" if settings.cookie_secure else "postdown"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not settings.testing:
            install_socket_guard()
        jobs.startup()
        task = asyncio.create_task(jobs.cleanup_loop())
        try:
            yield
        finally:
            task.cancel()
            for j in list(jobs.jobs.values()):
                jobs.drop_session(j.session_id)

    app = FastAPI(title=settings.app_name, version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.settings, app.state.jobs, app.state.sessions = settings, jobs, sessions
    if settings.trusted_hosts:
        # 127.0.0.1/localhost: verificação de saúde feita de dentro do contêiner.
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[*settings.trusted_hosts, "127.0.0.1", "localhost"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        # Proteção CSRF: operações que alteram estado exigem cabeçalho próprio e mesma origem.
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if request.headers.get("x-post-down") != "1" or (origin and origin.split("://", 1)[-1] != host):
                return error_response("FORBIDDEN", "Requisição recusada.")
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), interest-cohort=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ExtractionError)
    async def on_extraction_error(request: Request, exc: ExtractionError):
        if exc.detail:
            log.info("erro %s: %s", exc.code, exc.detail[:300])
        return error_response(exc.code, exc.message)

    def current_session(request: Request) -> Session:
        sess = sessions.get(request.cookies.get(cookie_name))
        if not sess:
            raise ExtractionError("UNAUTHORIZED", "Sessão expirada. Entre novamente.")
        return sess

    # ---------- autenticação ----------
    @app.get("/api/session")
    async def session_info(request: Request):
        return {"authenticated": sessions.get(request.cookies.get(cookie_name)) is not None,
                "app": settings.app_name, "version": __version__}

    @app.post("/api/login")
    async def login(body: LoginBody, request: Request):
        key = request.client.host if request.client else "?"
        if limiter.blocked(key):
            return error_response("TOO_MANY_REQUESTS", "Muitas tentativas. Aguarde alguns minutos.")
        ok = await asyncio.to_thread(verify_password, body.password, settings.password_hash)
        if not ok:
            limiter.fail(key)
            await asyncio.sleep(0.5)
            return error_response("UNAUTHORIZED", "Senha incorreta.")
        limiter.reset(key)
        _, token = sessions.create()
        resp = JSONResponse({"authenticated": True})
        resp.set_cookie(cookie_name, token, max_age=settings.session_ttl_seconds, httponly=True,
                        secure=settings.cookie_secure, samesite="strict", path="/")
        return resp

    @app.post("/api/logout")
    async def logout(request: Request):
        sid = sessions.destroy(request.cookies.get(cookie_name))
        if sid:
            jobs.drop_session(sid)
        resp = JSONResponse({"authenticated": False})
        resp.delete_cookie(cookie_name, path="/", secure=settings.cookie_secure, httponly=True, samesite="strict")
        return resp

    # ---------- extração ----------
    @app.post("/api/extract")
    async def extract(body: ExtractBody, sess: Session = Depends(current_session)):
        url = body.url.strip()
        if not url:
            raise ExtractionError("EMPTY_URL", "Cole o link da publicação para continuar.")
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        if url.lower().startswith("http://"):
            url = "https://" + url[7:]
        try:
            platform, normalized = platform_for_url(url)
        except UnsafeURLError as exc:
            host = url.split("://", 1)[-1].split("/", 1)[0].lower()
            if any(host in hosts for hosts in PLANNED_PLATFORMS.values()):
                raise ExtractionError("UNSUPPORTED_PLATFORM",
                                      "Esta rede ainda não é suportada. Nesta versão: Instagram e Threads.") from exc
            raise ExtractionError("INVALID_URL", "Use um link HTTPS de uma publicação do Instagram ou Threads.", str(exc)) from exc
        job = await jobs.extract(sess.sid, platform, normalized)
        return serialize_job(job)

    @app.get("/api/jobs/{job_id}")
    async def job_status(job_id: str, sess: Session = Depends(current_session)):
        return serialize_job(jobs.get(job_id, sess.sid))

    @app.post("/api/jobs/{job_id}/items/{index}/prepare")
    async def prepare_item(job_id: str, index: int, sess: Session = Depends(current_session)):
        job = jobs.get(job_id, sess.sid)
        jobs.start_prepare(job, index)
        return serialize_job(job)

    @app.post("/api/jobs/{job_id}/zip")
    async def prepare_zip(job_id: str, sess: Session = Depends(current_session)):
        job = jobs.get(job_id, sess.sid)
        jobs.start_zip(job)
        return serialize_job(job)

    @app.get("/api/jobs/{job_id}/items/{index}/thumb")
    async def thumb(job_id: str, index: int, sess: Session = Depends(current_session)):
        job = jobs.get(job_id, sess.sid)
        path = await jobs.thumbnail(job, index)
        kind = sniff_ext(path)
        if kind not in ("jpg", "png", "webp", "gif"):
            raise ExtractionError("INVALID_MEDIA", "Miniatura inválida.")
        return FileResponse(path, media_type="image/jpeg" if kind == "jpg" else f"image/{kind}",
                            headers={"Cache-Control": "private, max-age=600"})

    @app.get("/api/jobs/{job_id}/items/{index}/file")
    async def item_file(job_id: str, index: int, sess: Session = Depends(current_session)):
        job = jobs.get(job_id, sess.sid)
        st = job.items.get(index)
        if not st or st.state != "ready" or not st.path or not st.path.exists():
            raise ExtractionError("NOT_READY", "O arquivo ainda não está pronto.")
        media_type = {"mp4": "video/mp4", "jpg": "image/jpeg", "png": "image/png", "webp": "image/webp",
                      "heic": "image/heic", "gif": "image/gif"}.get(st.info["ext"], "application/octet-stream")
        return FileResponse(st.path, media_type=media_type, filename=st.info["file_name"])

    @app.get("/api/jobs/{job_id}/zip")
    async def zip_file(job_id: str, sess: Session = Depends(current_session)):
        job = jobs.get(job_id, sess.sid)
        if job.zip.state != "ready" or not job.zip.path:
            raise ExtractionError("NOT_READY", "O ZIP ainda não está pronto.")
        return FileResponse(job.zip.path, media_type="application/zip", filename=job.zip.info["file_name"])

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def get_app() -> FastAPI:  # usado pelo uvicorn: `uvicorn app.main:get_app --factory`
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app()
