"""Validação REAL dos extratores com publicações públicas que você tem autorização para usar.

Rode no servidor (ou no seu computador) — NÃO depende do site estar no ar:

    docker compose run --rm app python scripts/validar_extracao.py URL1 [URL2 ...]
    # ou, fora do Docker:
    python scripts/validar_extracao.py https://www.instagram.com/p/XXXX/ https://www.threads.com/@x/post/YYYY

Para cada link: extrai, baixa TODOS os itens, mede cada arquivo com ffprobe e
compara com o que a plataforma informou. Gera relatorio.json na pasta de saída.
Os arquivos ficam em validacao_<data>/ para você abrir e conferir; apague depois.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters import ExtractionError, build_registry  # noqa: E402
from app.config import Settings  # noqa: E402
from app.httpfetch import download_media  # noqa: E402
from app.media import probe, sniff_ext  # noqa: E402
from app.security import install_socket_guard, platform_for_url  # noqa: E402


def main(urls: list[str]) -> int:
    if not urls:
        print(__doc__)
        return 2
    install_socket_guard()
    settings = Settings.from_env()
    adapters = build_registry(settings)
    out = Path(os.environ.get("VALIDACAO_DIR", ".")) / (f"validacao_{time.strftime('%Y%m%d_%H%M%S')}")
    out.mkdir(parents=True)
    report, failures = [], 0
    for url in urls:
        entry: dict = {"url": url}
        t0 = time.time()
        try:
            platform, normalized = platform_for_url(url)
            post = adapters[platform].extract(normalized)
            entry.update(platform=platform, extractor=post.extractor, author=post.author,
                         caption_chars=len(post.caption or ""), items=[])
            print(f"\n✔ {url}\n  {platform} via {post.extractor}: {len(post.items)} item(ns) "
                  f"em {time.time() - t0:.1f}s")
            pdir = out / f"{platform}_{post.shortcode}"
            pdir.mkdir()
            for item in post.items:
                rec = {"index": item.index + 1, "kind": item.kind,
                       "reported": {"width": item.width, "height": item.height}}
                try:
                    kwargs = {"max_duration": settings.max_video_seconds} if platform == "instagram" else {}
                    path = adapters[platform].download(item, pdir, f"{item.index + 1:02d}", max_bytes=settings.max_file_bytes,
                                                       timeout=settings.download_timeout_seconds,
                                                       http_download=download_media, **kwargs)
                    ext = sniff_ext(path)
                    info = probe(path)
                    rec.update(ok=True, file=str(path), sniffed=ext, measured=info)
                    audio = "" if item.kind == "photo" else (" com áudio" if info["has_audio"] else " SEM áudio")
                    print(f"  [{item.index + 1}] {item.kind:5} {info['width']}x{info['height']} "
                          f"{ext} {info['size_bytes'] / 1024 / 1024:.2f} MB{audio}")
                except ExtractionError as exc:
                    failures += 1
                    rec.update(ok=False, error=exc.code, message=exc.message, detail=exc.detail)
                    print(f"  [{item.index + 1}] FALHOU: {exc.code} — {exc.message}")
                entry["items"].append(rec)
        except ExtractionError as exc:
            failures += 1
            entry.update(ok=False, error=exc.code, message=exc.message, detail=exc.detail)
            print(f"\n✘ {url}\n  {exc.code}: {exc.message}\n  detalhe: {(exc.detail or '')[:400]}")
        except Exception as exc:  # registrar qualquer falha inesperada
            failures += 1
            entry.update(ok=False, error="UNEXPECTED", message=repr(exc))
            print(f"\n✘ {url}\n  erro inesperado: {exc!r}")
        report.append(entry)
    (out / "relatorio.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nRelatório: {out / 'relatorio.json'}  —  falhas: {failures}")
    print("Abra os arquivos e confira: reproduzem? têm áudio? a ordem bate com o carrossel?")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
