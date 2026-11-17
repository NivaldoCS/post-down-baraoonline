"""Inspeção do arquivo real com ffprobe: formato, resolução, duração e presença de áudio."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

MAGIC = [
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),  # confirmado abaixo
    (b"GIF8", "gif"),
]


def sniff_ext(path: Path) -> str | None:
    head = path.open("rb").read(16)
    for sig, ext in MAGIC:
        if head.startswith(sig):
            if ext == "webp" and head[8:12] != b"WEBP":
                continue
            return ext
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"mif1"):
            return "heic"
        return "mp4"
    return None


def probe(path: Path, timeout: int = 30) -> dict[str, Any]:
    """Retorna metadados do arquivo. Lança ValueError se não for mídia válida."""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "--", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, check=True).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Arquivo não é uma mídia válida") from exc
    data = json.loads(out or b"{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise ValueError("Arquivo sem imagem ou vídeo")
    fmt = data.get("format") or {}
    duration = None
    try:
        duration = float(fmt.get("duration")) if fmt.get("duration") not in (None, "N/A") else None
    except ValueError:
        pass
    return {
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "has_audio": audio is not None,
        "duration": duration,
        "size_bytes": path.stat().st_size,
        "container": fmt.get("format_name"),
    }
