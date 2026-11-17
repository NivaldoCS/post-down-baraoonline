# Registro de validação

Data: 17/11/2026. Este registro separa o que foi **testado de verdade** do que foi **simulado** e do que **não pôde ser testado**.

## Testado de verdade (ambiente de desenvolvimento)

| O quê | Como | Resultado |
|---|---|---|
| União de vídeo + áudio DASH via yt-dlp + FFmpeg | `test_ytdlp_merges_dash_video_and_audio_without_reencode`: servidor HTTP local com faixa de vídeo 1080×1920 sem áudio e faixa AAC separadas | MP4 final 1080×1920 com áudio; MD5 do fluxo de vídeo e do de áudio **idênticos** à origem → sem recompressão |
| Medição real dos arquivos (ffprobe) | arquivos gerados pelo FFmpeg | resolução, duração, áudio e tamanho corretos |
| ZIP | 3 itens (foto, vídeo, foto) | ordem preservada, `ZIP_STORED` |
| Autenticação | login errado, limite de tentativas (bloqueia na 6ª), cookie HttpOnly/SameSite, CSRF | ok |
| Isolamento | job de uma sessão inacessível por outra | ok (`JOB_EXPIRED`) |
| Limpeza | job expirado e logout | pasta apagada do disco |
| Proteção de URLs | 30+ casos: HTTP, portas, credenciais, IP direto, localhost, metadados de nuvem, domínios parecidos, IPv6 mapeado | recusados |
| Guarda de DNS no processo | `socket.getaddrinfo` instalado de verdade | `localhost` e `169.254.169.254` bloqueados; bind do servidor permitido |
| Interface | Chrome headless (Puppeteer), viewport iPhone 390×844 com user-agent do Safari iOS e desktop 1440×900, com dados simulados | fluxo completo: login, campo vazio, link inválido, busca, preparo, download, ZIP; link preservado após erro; nenhum erro de CSP no console |
| Servidor em modo produção | `uvicorn app.main:get_app --factory` com guarda de rede ativo | inicia, `/healthz` ok, login ok |

Total: 73 testes automatizados (`pytest`) passando.

### No GitHub Actions

| O quê | Como | Resultado |
|---|---|---|
| Build da imagem Docker | GitHub Actions (PR #1): build, contêiner somente leitura, `/healthz`, `/api/session`, FFmpeg presente | ok |
| Testes no CI | GitHub Actions, Ubuntu + Python 3.13 + FFmpeg | 73 passando, lint ok |

## Simulado

- Estrutura dos dados do Instagram e do Threads: os testes de parsing usam JSON **sintético**, montado a partir da
  estrutura documentada (`image_versions2`, `video_versions`, `carousel_media`). Provam a lógica, não o formato atual da Meta.
- Safari: **somente emulação** (Chrome com tamanho e user-agent de iPhone). Não houve teste em Safari real.

## NÃO testado (pendente — precisa ser feito por você)

1. **Extração real do Instagram e do Threads.** O ambiente de nuvem usado no desenvolvimento bloqueia
   `instagram.com`, `threads.com` e os CDNs (erro 403 do proxy de rede da sessão — não é resposta da Meta).
   Execute `scripts/validar_extracao.py` com um Reel, uma foto, um carrossel misto do Instagram e um post do Threads.
2. **Teste na hospedagem** (o IP do servidor pode ser limitado pelo Instagram).
3. **Safari em iPhone real**: baixar, abrir em Arquivos e o botão Compartilhar.

Ao concluir, anote aqui a data, os links (ou só o tipo de publicação) e o `relatorio.json` gerado.
