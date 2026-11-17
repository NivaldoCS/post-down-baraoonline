"""Testes SIMULADOS dos adaptadores (sem acesso à rede das plataformas).

Os dados abaixo são sintéticos, montados a partir da estrutura documentada dos
objetos de mídia da Meta (image_versions2, video_versions, carousel_media). Eles
validam a lógica de parsing, ordem e escolha de qualidade — não provam que a
plataforma continua entregando esse formato. Para isso: scripts/validar_extracao.py.
"""
import functools
import http.server
import json
import subprocess
import threading

import pytest

from app.adapters import instagram, threads
from app.adapters.base import ExtractionError

CDN = "https://scontent-gru2-1.cdninstagram.com/v/t51/"


def _img(w, h, name):
    return {"url": f"{CDN}{name}_{w}.jpg?oe=1", "width": w, "height": h}


def _threads_html(post_obj, code):
    payload = {"require": [["ScheduledServerJS", "handle", None, [{"__bbox": {"result": {"data": {
        "data": {"edges": [{"node": {"thread_items": [{"post": post_obj}]}}]}}}}}]]]}
    return (f'<html><script type="application/json" data-content-len="1" data-sjs>{json.dumps(payload)}</script>'
            f'<script type="application/json" data-sjs>{{"other":"{code}x"}}</script></html>')


def test_threads_parse_code():
    assert threads.parse_code("https://www.threads.com/@baraoonline.oficial/post/DAbc123_-X") == "DAbc123_-X"
    assert threads.parse_code("https://www.threads.net/t/DAbc123XYZ/") == "DAbc123XYZ"
    with pytest.raises(ExtractionError):
        threads.parse_code("https://www.threads.com/@baraoonline.oficial")


def test_threads_mixed_carousel_order_and_quality():
    code = "DQmixed123"
    post = {
        "code": code, "user": {"username": "baraoonline.oficial"}, "caption": {"text": "Legenda Threads"},
        "carousel_media": [
            {"image_versions2": {"candidates": [_img(640, 800, "a"), _img(1440, 1800, "a"), _img(320, 400, "a")]},
             "video_versions": None},
            {"image_versions2": {"candidates": [_img(1080, 1920, "b")]},
             "video_versions": [{"type": 101, "url": CDN + "b_hd.mp4"}, {"type": 102, "url": CDN + "b_sd.mp4"}]},
            {"image_versions2": {"candidates": [_img(1080, 1080, "c")]}, "video_versions": []},
        ],
    }
    found = threads.find_post(_threads_html(post, code), code)
    res = threads.post_to_result(found, code, "https://www.threads.com/@b/post/" + code, 20)
    assert [i.kind for i in res.items] == ["photo", "video", "photo"]
    assert res.items[0].direct_url.endswith("a_1440.jpg?oe=1")
    assert res.items[1].direct_url.endswith("b_hd.mp4")
    assert res.caption == "Legenda Threads" and res.author == "baraoonline.oficial"


def test_threads_rejects_non_cdn_and_text_only():
    code = "DQtext1234"
    post = {"code": code, "image_versions2": {"candidates": [{"url": "https://evil.example/x.jpg", "width": 9, "height": 9}]}}
    found = threads.find_post(_threads_html(post, code), code)
    with pytest.raises(ExtractionError) as e:
        threads.post_to_result(found, code, "u", 20)
    assert e.value.code == "NO_MEDIA"


def test_threads_find_post_missing():
    assert threads.find_post("<html>nada</html>", "DQnone1234") is None


@pytest.mark.parametrize("url,code", [
    ("https://www.instagram.com/p/C1a2b3c4d5/", "C1a2b3c4d5"),
    ("https://www.instagram.com/reel/C1a2b3c4d5/?igsh=xyz", "C1a2b3c4d5"),
    ("https://www.instagram.com/reels/C1a2b3c4d5", "C1a2b3c4d5"),
    ("https://www.instagram.com/tv/C1a2b3c4d5/", "C1a2b3c4d5"),
    ("https://www.instagram.com/baraoonline.oficial/p/C1a2b3c4d5/", "C1a2b3c4d5"),
    ("https://www.instagram.com/baraoonline.oficial/reel/C1a2b3c4d5/", "C1a2b3c4d5"),
])
def test_instagram_shortcodes(url, code):
    assert instagram.parse_shortcode(url) == code


def test_instagram_stories_and_profile():
    with pytest.raises(ExtractionError) as e:
        instagram.parse_shortcode("https://www.instagram.com/stories/baraoonline.oficial/123/")
    assert e.value.code == "UNSUPPORTED_CONTENT"
    with pytest.raises(ExtractionError) as e:
        instagram.parse_shortcode("https://www.instagram.com/baraoonline.oficial/")
    assert e.value.code == "INVALID_URL"


@pytest.mark.parametrize("msg,code", [
    ("ERROR: [Instagram] X: The webpage request was redirected to the login page. You have exceeded the rate-limit", "RATE_LIMITED"),
    ("ERROR: This content is only available for registered users who follow this account", "RESTRICTED"),
    ("Restricted Video: You must be 18", "RESTRICTED"),
    ("HTTP Error 404: Not Found", "NOT_FOUND"),
    ("Read timed out", "TIMEOUT"),
    ("Something else", "EXTRACTION_FAILED"),
])
def test_instagram_error_classification(msg, code):
    assert instagram.classify_error(msg).code == code


def test_instagram_entry_mapping():
    video_entry = {"formats": [
        {"url": CDN + "v720.mp4", "width": 720, "height": 1280, "format_id": "101"},
        {"url": CDN + "dash_v1080.mp4", "width": 1080, "height": 1920, "format_id": "dash-v", "acodec": "none"},
        {"url": CDN + "dash_a.mp4", "format_id": "dash-a", "vcodec": "none"},
    ], "thumbnails": [_img(320, 568, "t"), _img(1080, 1920, "t")], "duration": 12.5}
    item = instagram._entry_to_item(0, video_entry)
    assert item.kind == "video" and (item.width, item.height) == (1080, 1920)
    assert item.direct_url.endswith("v720.mp4")  # progressivo com áudio como alternativa
    photo = instagram._entry_to_item(1, {"formats": [], "thumbnails": [_img(1440, 1800, "p"), _img(640, 800, "p")]})
    assert photo.kind == "photo" and photo.direct_url.endswith("p_1440.jpg?oe=1")
    with pytest.raises(ExtractionError):
        instagram._entry_to_item(2, {"formats": [{"url": "https://evil.example/v.mp4"}]})


@pytest.fixture
def local_media_server(media_dir):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(media_dir))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_ytdlp_merges_dash_video_and_audio_without_reencode(local_media_server, media_dir, tmp_path, monkeypatch):
    """Simula um Reel com faixas DASH separadas (vídeo 1080x1920 sem áudio + áudio AAC).
    Verifica que o yt-dlp escolhe a melhor faixa de vídeo, une com o áudio via FFmpeg
    e preserva codec/resolução (cópia de stream, sem recompressão)."""
    monkeypatch.setattr(instagram, "check_media_url", lambda u: u)  # servidor local só neste teste
    info = {
        "id": "TESTE1", "title": "TESTE1", "extractor": "instagram", "extractor_key": "Instagram",
        "webpage_url": "https://www.instagram.com/p/TESTE1/", "duration": 2,
        "formats": [
            {"format_id": "dash-video", "url": f"{local_media_server}/video_only.mp4", "ext": "mp4",
             "width": 1080, "height": 1920, "vcodec": "avc1", "acodec": "none", "protocol": "http"},
            {"format_id": "dash-audio", "url": f"{local_media_server}/audio_only.m4a", "ext": "m4a",
             "vcodec": "none", "acodec": "mp4a.40.2", "protocol": "http"},
            {"format_id": "prog-360", "url": f"{local_media_server}/av.mp4", "ext": "mp4",
             "width": 720, "height": 1280, "protocol": "http"},
        ],
    }
    out = instagram.ytdlp_download(info, tmp_path, "item_01", max_bytes=50 * 1024 * 1024, timeout=60)
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(out)],
                                      capture_output=True, check=True).stdout)
    kinds = {s["codec_type"]: s for s in probe["streams"]}
    assert kinds["video"]["width"] == 1080 and kinds["video"]["height"] == 1920
    assert kinds["video"]["codec_name"] == "h264" and kinds["audio"]["codec_name"] == "aac"
    assert out.suffix == ".mp4"

    def stream_md5(path, sel):
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{sel}", "-c", "copy",
                               "-f", "md5", "-"], capture_output=True, check=True).stdout

    # Mesmo bitstream de vídeo e de áudio da origem => nenhuma recompressão.
    assert stream_md5(out, "v") == stream_md5(media_dir / "video_only.mp4", "v")
    assert stream_md5(out, "a") == stream_md5(media_dir / "audio_only.m4a", "a")


def test_ytdlp_respects_max_duration(tmp_path):
    with pytest.raises(ExtractionError) as e:
        instagram.ytdlp_download({"formats": [], "duration": 5000}, tmp_path, "x", max_bytes=1, timeout=5, max_duration=60)
    assert e.value.code == "TOO_LONG"
