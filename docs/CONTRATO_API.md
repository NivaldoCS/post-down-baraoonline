# Contrato da API — Post-Down Baraoonline.oficial

Versão 1.0. Este documento descreve a API **como implementada** em `app/main.py`.
A interface (`static/app.js`) usa apenas estes endpoints.

## Regras gerais

- Mesma origem: a interface e a API são servidas pelo mesmo servidor.
- Autenticação por cookie de sessão (`__Host-postdown` em produção; `HttpOnly`, `Secure`, `SameSite=Strict`).
- Toda requisição `POST` para `/api/*` deve enviar o cabeçalho `X-Post-Down: 1`; se houver `Origin`, ele precisa coincidir com o host. Sem isso: `403 FORBIDDEN`.
- Respostas da API têm `Cache-Control: no-store`.
- Não existe endpoint que receba uma URL de mídia: o cliente só envia o **link da publicação**. As URLs de CDN ficam no servidor, presas ao job.

### Formato de erro

```json
{ "error": { "code": "RATE_LIMITED", "message": "Texto em português para exibir ao usuário." } }
```

| code | HTTP | Quando |
|---|---|---|
| `EMPTY_URL` | 400 | Campo vazio |
| `INVALID_URL` | 400 | Não é HTTPS, domínio não permitido, não aponta para uma publicação |
| `UNSUPPORTED_PLATFORM` | 400 | Facebook, TikTok, X, YouTube (planejados, não ativos) |
| `UNSUPPORTED_CONTENT` | 400 | Stories (fora da v1) |
| `NOT_FOUND` | 404 | Publicação removida ou inexistente |
| `RESTRICTED` | 403 | Conta privada, conteúdo restrito ou que exige login |
| `RATE_LIMITED` | 429 | A plataforma limitou o servidor |
| `TOO_MANY_REQUESTS` | 429 | Limite do próprio Post-Down (buscas/minuto, preparos simultâneos, login) |
| `EXTRACTION_FAILED` | 502 | Falha do extrator (mudança na plataforma, erro de rede) |
| `NO_MEDIA` | 422 | Publicação sem foto/vídeo (ex.: só texto) |
| `TIMEOUT` | 504 | Tempo excedido |
| `TOO_LARGE` / `TOO_LONG` / `TOO_MANY_ITEMS` | 413 | Limites de tamanho, duração ou quantidade |
| `EXPIRED` | 410 | Link temporário do CDN expirou |
| `JOB_EXPIRED` | 410 | Busca expirou, foi apagada ou pertence a outra sessão |
| `NOT_READY` | 409 | Arquivo ainda não preparado |
| `INVALID_MEDIA` | 502 | O arquivo recebido não é mídia válida (ffprobe) |
| `UNAUTHORIZED` | 401 | Sem sessão / senha incorreta |

## Autenticação

- `GET /api/session` → `{ "authenticated": bool, "app": "...", "version": "1.0.0" }`
- `POST /api/login` body `{ "password": "..." }` → `200 { "authenticated": true }` + cookie. 5 falhas em 15 min por IP → `429`.
- `POST /api/logout` → apaga a sessão **e os arquivos temporários dela**.

## Extração

`POST /api/extract` body `{ "url": "https://www.instagram.com/reel/XXXX/" }`

Síncrono (até `EXTRACT_TIMEOUT_SECONDS`). Resposta — objeto **Job**:

```json
{
  "job_id": "kq3...",
  "platform": "instagram",
  "source_url": "https://www.instagram.com/p/XXXX/",
  "author": "baraoonline.oficial",
  "caption": "Legenda ou null",
  "extractor": "yt-dlp",
  "expires_at": 1795430000,
  "items": [
    {
      "index": 0,
      "kind": "video",
      "reported": { "width": 1080, "height": 1920, "duration": 31.2, "ext": "mp4" },
      "state": "listed",
      "file": null,
      "error": null,
      "thumb_url": "/api/jobs/kq3.../items/0/thumb",
      "download_url": null
    }
  ],
  "zip": { "available": true, "state": "listed", "file": null, "error": null, "download_url": null }
}
```

- `reported` = o que a plataforma declarou (pode faltar). **Não** é exibido como garantia.
- `state`: `listed` → `preparing` → `ready` | `error`.
- `file` (só quando `ready`) = valores **medidos no arquivo real** com ffprobe:
  `{ "width", "height", "video_codec", "audio_codec", "has_audio", "duration", "size_bytes", "container", "ext", "file_name" }`.
  Para fotos, `has_audio` e `duration` são `null`.

## Preparo e download

- `GET /api/jobs/{job_id}` → Job atualizado (a interface consulta a cada ~1,2 s enquanto algo está em `preparing`).
- `POST /api/jobs/{job_id}/items/{index}/prepare` → inicia o download do item no servidor; devolve o Job.
- `GET /api/jobs/{job_id}/items/{index}/thumb` → miniatura (imagem do CDN, servida pelo servidor).
- `GET /api/jobs/{job_id}/items/{index}/file` → arquivo (`Content-Disposition: attachment`). `409` se não estiver pronto.
- `POST /api/jobs/{job_id}/zip` → prepara todos os itens e monta o ZIP (só se houver 2+ itens).
- `GET /api/jobs/{job_id}/zip` → ZIP sem compressão (`ZIP_STORED`), itens na ordem do carrossel.

Nomes dos arquivos: `{plataforma}_{codigo}_{NN}.{ext}` (ex.: `instagram_C1a2b3_02.mp4`) e `{plataforma}_{codigo}.zip`.

## Expiração

Cada job expira em `JOB_TTL_SECONDS` (padrão 15 min). Uma rotina a cada `CLEANUP_INTERVAL_SECONDS` apaga a pasta do job.
Ao iniciar, o servidor apaga qualquer sobra em `TEMP_DIR`. Logout apaga os jobs da sessão.

## Diferenças em relação à proposta original

O `CONTRATO_API.md` do pacote do layout não foi recebido nesta entrega; este contrato foi escrito a partir do
protótipo `PREVIA_POST_DOWN.html` e das exigências do prompt. Se o original tiver algo diferente que você queira manter, envie-o.
