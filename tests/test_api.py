import time
import zipfile
from io import BytesIO

from app.auth import hash_password, verify_password
from tests.conftest import HEADERS, PASSWORD

IG = "https://www.instagram.com/p/ABCDEF123/"


def test_password_hash_roundtrip():
    h = hash_password("abc-123-xyz-999")
    assert verify_password("abc-123-xyz-999", h)
    assert not verify_password("errada", h)
    assert not verify_password("x", "lixo")


def test_requires_auth(client):
    r = client.post("/api/extract", json={"url": IG}, headers=HEADERS)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"
    assert client.get("/api/session").json()["authenticated"] is False


def test_csrf_header_required(client):
    r = client.post("/api/login", json={"password": PASSWORD})
    assert r.status_code == 403
    r = client.post("/api/login", json={"password": PASSWORD}, headers={**HEADERS, "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_login_wrong_and_rate_limit(client):
    for _ in range(5):
        assert client.post("/api/login", json={"password": "errada"}, headers=HEADERS).status_code == 401
    r = client.post("/api/login", json={"password": PASSWORD}, headers=HEADERS)
    assert r.status_code == 429


def test_cookie_flags(client):
    r = client.post("/api/login", json={"password": PASSWORD}, headers=HEADERS)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "Post-Down Baraoonline.oficial" in r.text
    assert "PRÉVIA DO LAYOUT" not in r.text and "Explorar o layout" not in r.text


def test_empty_and_invalid_urls(auth_client):
    assert auth_client.post("/api/extract", json={"url": "  "}, headers=HEADERS).json()["error"]["code"] == "EMPTY_URL"
    r = auth_client.post("/api/extract", json={"url": "https://example.com/x"}, headers=HEADERS)
    assert r.json()["error"]["code"] == "INVALID_URL"
    r = auth_client.post("/api/extract", json={"url": "https://www.tiktok.com/@a/video/1"}, headers=HEADERS)
    assert r.json()["error"]["code"] == "UNSUPPORTED_PLATFORM"
    r = auth_client.post("/api/extract", json={"url": "https://10.0.0.1/p/abc"}, headers=HEADERS)
    assert r.json()["error"]["code"] == "INVALID_URL"


def _wait(client, job_id, cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        data = client.get(f"/api/jobs/{job_id}").json()
        if cond(data):
            return data
        time.sleep(0.2)
    raise AssertionError("timeout esperando job")


def test_full_flow_items_and_zip(auth_client):
    r = auth_client.post("/api/extract", json={"url": "instagram.com/p/ABCDEF123/"}, headers=HEADERS)
    assert r.status_code == 200, r.text
    job = r.json()
    assert [i["kind"] for i in job["items"]] == ["photo", "video", "photo"]
    assert job["caption"] == "Legenda de teste" and job["zip"]["available"]
    assert all(i["file"] is None and i["download_url"] is None for i in job["items"])
    jid = job["job_id"]

    # arquivo não pronto ainda
    assert auth_client.get(f"/api/jobs/{jid}/items/1/file").status_code == 409

    auth_client.post(f"/api/jobs/{jid}/items/1/prepare", headers=HEADERS)
    data = _wait(auth_client, jid, lambda d: d["items"][1]["state"] in ("ready", "error"))
    item = data["items"][1]
    assert item["state"] == "ready", item
    f = item["file"]
    assert (f["width"], f["height"]) == (720, 1280) and f["has_audio"] is True and f["ext"] == "mp4"
    assert f["file_name"] == "instagram_ABCDEF123_02.mp4"
    resp = auth_client.get(item["download_url"])
    assert resp.status_code == 200 and resp.headers["content-type"] == "video/mp4"
    assert 'filename="instagram_ABCDEF123_02.mp4"' in resp.headers["content-disposition"]
    assert resp.content[4:8] == b"ftyp"

    auth_client.post(f"/api/jobs/{jid}/zip", headers=HEADERS)
    data = _wait(auth_client, jid, lambda d: d["zip"]["state"] in ("ready", "error"))
    assert data["zip"]["state"] == "ready", data["zip"]
    z = zipfile.ZipFile(BytesIO(auth_client.get(data["zip"]["download_url"]).content))
    assert z.namelist() == ["instagram_ABCDEF123_01.jpg", "instagram_ABCDEF123_02.mp4", "instagram_ABCDEF123_03.jpg"]
    assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())
    photo = next(i for i in data["items"] if i["index"] == 0)["file"]
    assert (photo["width"], photo["height"]) == (1080, 1350) and photo["has_audio"] is None


def test_jobs_isolated_between_sessions(auth_client, settings, fake_adapter):
    job = auth_client.post("/api/extract", json={"url": IG}, headers=HEADERS).json()
    from fastapi.testclient import TestClient

    other = TestClient(auth_client.app, base_url="http://testserver")
    other.post("/api/login", json={"password": PASSWORD}, headers=HEADERS)
    r = other.get(f"/api/jobs/{job['job_id']}")
    assert r.status_code == 410 and r.json()["error"]["code"] == "JOB_EXPIRED"


def test_expiry_cleanup_removes_files(auth_client, settings):
    job = auth_client.post("/api/extract", json={"url": IG}, headers=HEADERS).json()
    jid = job["job_id"]
    auth_client.post(f"/api/jobs/{jid}/items/0/prepare", headers=HEADERS)
    _wait(auth_client, jid, lambda d: d["items"][0]["state"] == "ready")
    manager = auth_client.app.state.jobs
    jdir = manager.jobs[jid].dir
    assert any(jdir.iterdir())
    manager.jobs[jid].expires_at = time.time() - 1
    assert manager.cleanup() == 1
    assert not jdir.exists()
    assert auth_client.get(f"/api/jobs/{jid}").json()["error"]["code"] == "JOB_EXPIRED"


def test_logout_drops_files(auth_client):
    job = auth_client.post("/api/extract", json={"url": IG}, headers=HEADERS).json()
    manager = auth_client.app.state.jobs
    jdir = manager.jobs[job["job_id"]].dir
    auth_client.post("/api/logout", headers=HEADERS)
    assert not jdir.exists()
    assert auth_client.get(f"/api/jobs/{job['job_id']}").status_code == 401


def test_extract_rate_limit(auth_client):
    codes = [auth_client.post("/api/extract", json={"url": IG}, headers=HEADERS).status_code for _ in range(7)]
    assert codes[:6] == [200] * 6 and codes[6] == 429


def test_download_media_rejects_non_cdn(tmp_path):
    import pytest

    from app.adapters.base import ExtractionError
    from app.httpfetch import download_media

    with pytest.raises(ExtractionError):
        download_media("https://example.com/a.jpg", tmp_path / "a.jpg", max_bytes=10, timeout=5)


def test_trusted_hosts_allow_healthcheck(settings, fake_adapter):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(replace(settings, trusted_hosts=["postdown.exemplo.com"]), adapters={"instagram": fake_adapter})
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        assert c.get("/healthz").status_code == 200
    with TestClient(app, base_url="http://evil.example") as c:
        assert c.get("/healthz").status_code == 400
