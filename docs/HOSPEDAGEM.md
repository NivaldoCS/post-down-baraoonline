# Hospedagem

Consultado em 17/11/2026. Preços mudam — confira na página de cada provedor antes de contratar.

## O que o Post-Down precisa

- Contêiner Docker com **FFmpeg** e Python (o GitHub Pages só serve arquivos estáticos e **não** executa o backend;
  GitHub Actions não deve ser usado como servidor).
- ~1 GB de RAM, 1–2 vCPU, alguns GB de disco temporário.
- **Tráfego de saída**: cada arquivo passa duas vezes pelo servidor (CDN → servidor → você). Um Reel de 30 s em
  1080p costuma ter de 5 a 20 MB. 300 downloads/mês × 15 MB ≈ 4,5 GB.
- HTTPS (o cookie de sessão exige).

## Ponto crítico: o IP do servidor

Instagram e Threads limitam e às vezes bloqueiam acessos anônimos vindos de **IPs de datacenter**. Isso é
amplamente relatado (ex.: o erro 401 "Please wait a few minutes" do Instaloader). Nenhum provedor garante que o
Instagram aceitará o IP dele. Por isso a recomendação inclui uma opção sem custo de servidor para comparar.

## Opções

| Opção | Custo aproximado | Tráfego | Observações |
|---|---|---|---|
| **A. VPS Hetzner** (CX23/CPX22, 2 vCPU / 4 GB) | a partir de ~€4–8/mês + IPv4 (preços subiram em 2026) | 20 TB/mês incluídos na UE/EUA | Docker Compose deste repositório + Caddy (HTTPS automático). Melhor custo por tráfego. Datacenter na Europa/EUA. |
| **B. Fly.io** | shared-cpu-1x 1 GB ≈ US$ 6,70/mês | US$ 0,02/GB (América do Norte/Europa), US$ 0,04/GB (América do Sul) | Sem plano grátis; cobra por segundo. Região em São Paulo (`gru`) disponível. Usa o Dockerfile direto. |
| **C. Railway** | plano Hobby US$ 5/mês de mínimo; RAM ≈ US$ 10/GB/mês | US$ 0,05/GB | 1 GB de RAM 24 h já passa do mínimo: estimativa US$ 10–15/mês. |
| **D. Seu computador / mini-PC em casa + Cloudflare Tunnel** | R$ 0 de servidor (energia/internet já pagas) | da sua conexão | Usa IP residencial, que o Instagram tende a tolerar mais. Precisa da máquina ligada. Cloudflare Tunnel tem plano gratuito, mas confirme os termos de uso atuais para tráfego de mídia. |

**Recomendação:** comece pela **D** (ou rode o `scripts/validar_extracao.py` no seu computador) para confirmar que
os extratores funcionam com seus links. Se funcionar e você quiser o site sempre no ar, contrate a **A** e rode o
mesmo script **no servidor** antes de usar no dia a dia — sucesso local não prova que o IP do servidor será aceito.

Não existe hospedagem gratuita e ilimitada adequada a este caso.

## Publicar na VPS (opção A)

```bash
# no servidor Ubuntu, com Docker instalado e o DNS do domínio apontando para o IP do servidor
git clone https://github.com/NivaldoCS/post-down-baraoonline.git && cd post-down-baraoonline
cp .env.example .env
docker compose run --rm --no-deps app python -m app.hash_password   # cole o resultado no .env
nano .env                                                            # DOMAIN, TRUSTED_HOSTS
docker compose up -d --build
docker compose logs -f app
```

Teste real no servidor:

```bash
mkdir -p validacao && sudo chown 10001 validacao
docker compose run --rm --no-deps -v "$PWD/validacao:/srv/validacao" -e VALIDACAO_DIR=/srv/validacao \
  app python scripts/validar_extracao.py "LINK_INSTAGRAM" "LINK_THREADS"
```

Firewall: libere só 22, 80 e 443.

## Fly.io (opção B), resumo

```bash
fly launch --no-deploy            # usa o Dockerfile; escolha a região gru
fly secrets set APP_PASSWORD_HASH='...' SESSION_SECRET='...'
fly scale memory 1024
fly deploy
```
Ajuste `TEMP_DIR=/tmp/post-down` (disco efêmero da máquina é suficiente; nada precisa persistir).

## Fontes
- Hetzner: 20 TB de tráfego incluído e aumentos de preço em 2026 — https://www.bitdoze.com/hetzner-cloud-review/ , https://kimmo.suominen.com/stuff/cpc-2026-08.txt
- Fly.io (preços a partir de 01/10/2026): https://fly.io/pricing-update/
- Railway: https://temps.sh/blog/railway-pricing-2026
- Instaloader 401: https://issues.ecosyste.ms/hosts/GitHub/repositories/instaloader%2Finstaloader/issues
