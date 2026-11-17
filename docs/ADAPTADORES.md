# Adaptadores por plataforma e atualização dos extratores

Cada rede tem um adaptador em `app/adapters/` com dois métodos: `extract(url) -> PostResult` e `download(item, ...)`.

| Plataforma | Arquivo | Estratégia | Status |
|---|---|---|---|
| Instagram | `instagram.py` | yt-dlp (primário, com curl_cffi) → Instaloader (alternativa) | Implementado; **validação real pendente** |
| Threads | `threads.py` | Lê os blocos JSON `data-sjs` da página pública do post | Implementado; **validação real pendente** |
| Stories do Instagram | — | Recusados com mensagem clara (`UNSUPPORTED_CONTENT`) | Fora da v1 |
| Facebook, TikTok, X, YouTube | `__init__.py` (`PLANNED_PLATFORMS`) | Reconhecidos só para responder "ainda não suportado" | Não anunciados |

## Por que essas escolhas

- **yt-dlp**: o extrator do Instagram foi reescrito em julho/2026 e entrega o carrossel inteiro, na ordem, com
  vídeos (formatos progressivos e DASH) e fotos (variantes de imagem). Para vídeo escolhe a melhor faixa e une o
  áudio com FFmpeg sem recompressão.
- **Instaloader**: bom para fotos/carrosséis, mas o acesso anônimo dele tem histórico de erro 401; por isso é a alternativa.
- **Threads**: nem yt-dlp nem gallery-dl têm extrator (verificado: yt-dlp 2026.10.26, gallery-dl 1.32.20).

## Atualizar os extratores (faça quando aparecer `EXTRACTION_FAILED` com frequência)

```bash
# 1. veja a versão mais nova
pip index versions yt-dlp ; pip index versions instaloader
# 2. altere requirements.txt (yt-dlp, curl_cffi, instaloader)
# 3. rode os testes e a validação real
pytest -q
python scripts/validar_extracao.py LINK1 LINK2
# 4. publique
docker compose up -d --build
```

O yt-dlp lança versões com frequência; manter atualizado é a manutenção principal.

## Se o Threads quebrar

1. Abra o link do post no navegador, "Ver código-fonte", procure o código do post (ex.: `DAbc123`).
2. Confira se ele aparece dentro de `<script type="application/json" ... data-sjs>`.
3. Ajuste `find_post()` / `media_to_item()` em `threads.py` e acrescente um caso em `tests/test_extractors.py`.

## Cookies opcionais do Instagram

Se o Instagram bloquear o acesso anônimo do servidor com frequência, o yt-dlp aceita um arquivo de cookies
(formato Netscape) de uma **conta secundária** via `INSTAGRAM_COOKIES_FILE`. O Post-Down nunca pede senha.
Riscos: a conta pode ser limitada ou suspensa pelo Instagram; o arquivo é um segredo (fica fora do Git, em
`secrets/`, montado só-leitura). Isso não dá acesso a conteúdo privado de terceiros e não deve ser usado para isso.

## APIs pagas (não contratadas)

Existem APIs comerciais de terceiros que devolvem os dados de posts do Instagram/Threads cobrando por
requisição. Não foram integradas. Só considere se a extração gratuita falhar de forma persistente no seu
servidor; antes, compare preço por requisição, limites, termos de uso e onde os dados trafegam. Um novo
adaptador pode ser acrescentado sem mudar a interface.

## Adicionar uma rede

1. Crie `app/adapters/<rede>.py` herdando `PlatformAdapter`.
2. Adicione os domínios em `PLATFORM_HOSTS` (`app/security.py`) e os CDNs em `MEDIA_HOST_SUFFIXES`.
3. Registre em `build_registry()` e remova de `PLANNED_PLATFORMS`.
4. Só depois de `validar_extracao.py` passar com links reais, mostre a rede na interface (`.platforms` em `static/index.html`).
