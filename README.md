# Post-Down Baraoonline.oficial

Ferramenta interna da redação do portal **@baraoonline.oficial** para baixar fotos e vídeos de publicações
públicas do **Instagram** e do **Threads** que você tem direito ou autorização para usar — sem anúncios,
pop-ups, redirecionamentos ou espera artificial.

1. Copie o link da publicação.
2. Cole no campo e toque em **Buscar mídia**.
3. Veja as fotos e vídeos encontrados.
4. Baixe cada item ou o carrossel inteiro em ZIP.

> **Situação:** código completo e testado com dados simulados e testes locais. A **extração real no
> Instagram/Threads ainda não foi comprovada** — o ambiente de desenvolvimento bloqueava esses sites.
> Antes de usar no dia a dia, rode `scripts/validar_extracao.py` (veja abaixo) e registre o resultado em
> [`docs/VALIDACAO.md`](docs/VALIDACAO.md).

## O que faz

- Instagram: Reels, vídeos, fotos e carrosséis, inclusive mistos. Links `/p/`, `/reel/`, `/reels/`, `/tv/`,
  com nome de usuário, e links de compartilhamento `/share/...` e `instagr.am`.
- Threads: fotos, vídeos e publicações com várias mídias (`threads.com` e `threads.net`).
- Melhor qualidade disponibilizada pela publicação; vídeo e áudio unidos com FFmpeg **por cópia** (sem recompressão).
- Mostra resolução, formato, tamanho, duração e presença de áudio **medidos no arquivo real** (ffprobe), não estimados.
- Nada é acrescentado aos arquivos (sem marca, nome ou moldura). Não remove marcas que já estejam na imagem.
- Copiar legenda e abrir a publicação original.
- No iPhone: download pelo Safari (vai para **Arquivos › Downloads**) e botão **Compartilhar** quando o navegador permite.
- Stories: fora desta versão. Facebook, TikTok, X e YouTube: preparados, não ativos.

## Segurança

- Acesso protegido por senha no servidor (hash scrypt, sessão com cookie `HttpOnly`/`Secure`/`SameSite=Strict`,
  limite de tentativas). **Nunca** pede senha do Instagram.
- Não é proxy aberto: o navegador só envia o link da publicação; as URLs de mídia ficam no servidor e precisam
  pertencer aos CDNs da Meta (`cdninstagram.com`, `fbcdn.net`).
- Esquema, domínio, porta, DNS e **cada redirecionamento** são validados. Endereços locais, privados e de metadados
  de nuvem são bloqueados também nas conexões feitas pelas bibliotecas (guarda em `socket.getaddrinfo`).
- Limites de buscas por minuto, preparos simultâneos, tamanho, duração, itens por post e tempo.
- Arquivos isolados por sessão, nomes gerados pelo servidor, apagados após 15 min, no logout e ao reiniciar.
- Nenhum comando do usuário é executado. Segredos só no `.env` do servidor (fora do Git).
- Não contorna contas privadas, DRM ou restrições de acesso.

## Estrutura

```
app/            FastAPI: autenticação, jobs, segurança, adaptadores
  adapters/     instagram.py (yt-dlp + Instaloader), threads.py, registro
static/         interface (layout original da prévia) + logo
scripts/        validar_extracao.py — teste real com links autorizados
tests/          73 testes automatizados (simulados/locais)
deploy/         Caddyfile (HTTPS automático)
docs/           contrato da API, hospedagem, adaptadores, validação
```

Escolhi FastAPI servindo a própria interface (HTML/CSS/JS sem etapa de build) em vez de React: o layout recebido
já era HTML/CSS puro, assim ele foi preservado sem reescrita e a manutenção fica mais simples.

## Rodar no seu computador (teste)

Requisitos: Python 3.13 e FFmpeg (ou Docker).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m app.hash_password          # gera APP_PASSWORD_HASH e SESSION_SECRET
cp .env.example .env                 # cole os valores; COOKIE_SECURE=false para http://localhost
set -a; source .env; set +a
TEMP_DIR=./data/tmp uvicorn app.main:get_app --factory --port 8000
```

Abra http://localhost:8000. Com Docker: `docker compose -f docker-compose.local.yml up --build`.

## Validar a extração real (obrigatório antes de confiar)

```bash
python scripts/validar_extracao.py "https://www.instagram.com/reel/..." "https://www.threads.com/@.../post/..."
```

Use um Reel, uma foto, um carrossel misto e um post do Threads, todos públicos e autorizados. O script baixa
cada item, mede com ffprobe, grava `validacao_*/relatorio.json` e mostra resolução, tamanho e áudio.
Abra os arquivos e confira se reproduzem e se a ordem bate com o carrossel. Apague a pasta depois.

## Publicar

Veja [`docs/HOSPEDAGEM.md`](docs/HOSPEDAGEM.md) — opções, custos (consultados em nov/2026) e passo a passo.
Resumo para VPS com Docker:

```bash
cp .env.example .env && docker compose run --rm --no-deps app python -m app.hash_password
# preencha .env (APP_PASSWORD_HASH, SESSION_SECRET, DOMAIN, TRUSTED_HOSTS)
docker compose up -d --build
```

GitHub Pages não executa o backend, e o GitHub Actions aqui só roda as verificações (testes + build da imagem).

## Configuração

Todas as variáveis estão em [`.env.example`](.env.example), com comentários. Principais: `APP_PASSWORD_HASH`,
`SESSION_SECRET`, `COOKIE_SECURE`, `MAX_FILE_MB`, `MAX_VIDEO_SECONDS`, `JOB_TTL_SECONDS`, `MAX_EXTRACTIONS_PER_MINUTE`.

## Atualizar os extratores

Instagram e Threads mudam com frequência. Quando surgirem falhas, atualize `yt-dlp` (e `curl_cffi`, `instaloader`)
em `requirements.txt`, rode `pytest` e `scripts/validar_extracao.py`, e reconstrua. Detalhes e o que fazer se o
Threads mudar: [`docs/ADAPTADORES.md`](docs/ADAPTADORES.md).

## Limitações conhecidas

- O Instagram limita acessos anônimos, principalmente de IPs de datacenter (erros `RATE_LIMITED`/`RESTRICTED`).
  Pode ser preciso usar a hospedagem caseira ou o arquivo de cookies opcional (veja `docs/ADAPTADORES.md`).
- O adaptador do Threads depende do formato da página pública; mudanças da Meta podem quebrá-lo.
- Os links de mídia dos CDNs expiram; se demorar, busque a publicação de novo.
- Sessões ficam em memória: reiniciar o servidor exige novo login.
- Arquivos acima de ~150 MB não ganham botão Compartilhar (só Baixar), para não pesar a memória do celular.
- A logo recebida (`static/assets/logo-original.jpeg`, 1536×1024) é quase branca, com contraste muito baixo e
  textura de compressão JPEG. O filtro CSS da prévia foi mantido para exibi-la no fundo escuro, mas a nitidez é
  limitada pelo arquivo. Envie o original (SVG ou PNG com transparência) para substituir.

## Uso responsável

Baixe apenas conteúdo próprio ou com autorização. O Post-Down não guarda cópias permanentes e não acessa conteúdo privado.
