"""Fixtures de teste. TODOS os testes desta pasta são simulados/locais:
nenhum acessa Instagram ou Threads. Os testes reais ficam em scripts/validar_extracao.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.base import MediaItem, PlatformAdapter, PostResult
from app.auth import hash_password
from app.config import Settings
from app.main import create_app

PASSWORD = "senha-de-teste-123"
HEADERS = {"X-Post-Down": "1"}


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("media")
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=720x1280:rate=30", "-f", "lavfi", "-i", "sine=frequency=440",
            "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "av.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30", "-t", "2", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-an", str(d / "video_only.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=330", "-t", "2", "-c:a", "aac", "-vn", str(d / "audio_only.m4a"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=1080x1350", "-frames:v", "1", str(d / "photo.jpg"))
    return d


class FakeAdapter(PlatformAdapter):
    """Adaptador simulado: devolve arquivos locais gerados pelo FFmpeg."""

    name = "instagram"

    def __init__(self, media_dir: Path, kinds=("photo", "video", "photo")):
        self.media_dir = media_dir
        self.kinds = kinds
        self.calls = 0

    def extract(self, url: str) -> PostResult:
        self.calls += 1
        items = [MediaItem(i, k, width=1080, height=1350 if k == "photo" else 1920, ext="jpg" if k == "photo" else "mp4",
                           thumbnail_url=None, direct_url="https://scontent.cdninstagram.com/x")
                 for i, k in enumerate(self.kinds)]
        return PostResult("instagram", "https://www.instagram.com/p/ABCDEF123/", "ABCDEF123", "baraoonline.oficial",
                          "Legenda de teste", items, "fake")

    def download(self, item, dest_dir, base_name, *, max_bytes, timeout, http_download, max_duration=None):
        src = self.media_dir / ("photo.jpg" if item.kind == "photo" else "av.mp4")
        dest = dest_dir / f"{base_name}.{src.suffix.lstrip('.')}"
        dest.write_bytes(src.read_bytes())
        return dest


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(password_hash=hash_password(PASSWORD), session_secret="x" * 40, cookie_secure=False,
                    temp_dir=tmp_path / "tmp", testing=True, cleanup_interval_seconds=3600)


@pytest.fixture
def fake_adapter(media_dir):
    return FakeAdapter(media_dir)


@pytest.fixture
def client(settings, fake_adapter):
    app = create_app(settings, adapters={"instagram": fake_adapter})
    with TestClient(app, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def auth_client(client):
    r = client.post("/api/login", json={"password": PASSWORD}, headers=HEADERS)
    assert r.status_code == 200, r.text
    return client
