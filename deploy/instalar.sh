#!/usr/bin/env bash
# Instalador do Post-Down Baraoonline.oficial para um servidor Ubuntu 22.04/24.04 novo.
#
# Uso (no terminal do servidor, como root ou com sudo):
#   curl -fsSL https://raw.githubusercontent.com/NivaldoCS/post-down-baraoonline/main/deploy/instalar.sh | sudo bash
#
# Opcional: usar um domínio próprio já apontado para o IP do servidor:
#   curl -fsSL .../instalar.sh | sudo DOMAIN=postdown.seudominio.com.br bash
#
# O que faz: instala Docker, baixa o Post-Down, gera a senha de acesso, cria o .env,
# liga o site com HTTPS automático e mostra o endereço e a senha no final.
# Pode ser executado de novo com segurança: mantém a senha e o .env existentes.
set -Eeuo pipefail

REPO_URL="https://github.com/NivaldoCS/post-down-baraoonline.git"
DIR="/opt/post-down"
SENHA_ARQ="/root/post-down-senha.txt"

verde() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
aviso() { printf '\n\033[1;33m[atenção] %s\033[0m\n' "$*"; }
erro()  { printf '\n\033[1;31m[erro] %s\033[0m\n' "$*" >&2; exit 1; }
trap 'erro "Falhou na linha $LINENO. Tire um print desta tela e envie para análise."' ERR

[ "$(id -u)" -eq 0 ] || erro "Execute como root: coloque 'sudo' antes do comando."
. /etc/os-release 2>/dev/null || true
[ "${ID:-}" = "ubuntu" ] || aviso "Sistema não é Ubuntu (${PRETTY_NAME:-desconhecido}). Seguindo mesmo assim."

export DEBIAN_FRONTEND=noninteractive

verde "1/7 Atualizando a lista de pacotes e instalando ferramentas básicas"
apt-get update -qq
apt-get install -y -qq ca-certificates curl git python3 >/dev/null

verde "2/7 Instalando o Docker"
if ! command -v docker >/dev/null 2>&1; then
  if ! apt-get install -y -qq docker.io docker-compose-v2 >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
  fi
fi
if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y -qq docker-compose-v2 >/dev/null 2>&1 || apt-get install -y -qq docker-compose-plugin >/dev/null
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker compose version

verde "3/7 Conferindo memória (servidores de 1 GB recebem 2 GB de swap)"
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 2000 ] && ! swapon --show | grep -q .; then
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
  chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "Swap de 2 GB criado."
else
  echo "Memória: ${MEM_MB} MB. Swap: $(swapon --show --noheadings | wc -l) área(s)."
fi

verde "4/7 Baixando o Post-Down do GitHub"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  git clone --depth 1 "$REPO_URL" "$DIR"
fi
cd "$DIR"

verde "5/7 Configurando endereço, senha e limites"
IP=$(curl -4 -fsS --max-time 10 https://api.ipify.org || curl -4 -fsS --max-time 10 https://ifconfig.me || true)
[ -n "$IP" ] || erro "Não consegui descobrir o IP público do servidor. Confira se a máquina tem IP público."
if [ -z "${DOMAIN:-}" ]; then
  # Endereço gratuito que aponta para o próprio IP (ex.: 200-1-2-3.sslip.io); o Caddy emite HTTPS para ele.
  DOMAIN="${IP//./-}.sslip.io"
fi
CONCURRENT=2
[ "$MEM_MB" -lt 2000 ] && CONCURRENT=1

if [ -f .env ]; then
  echo ".env já existe: mantendo a senha e a configuração atuais."
  if [ -f "$SENHA_ARQ" ]; then SENHA=$(sed -n 's/^Senha: //p' "$SENHA_ARQ"); else SENHA="(a mesma de antes)"; fi
  DOMAIN=$(sed -n "s/^DOMAIN=//p" .env | tr -d "'\"")
else
  SENHA=$(python3 -c "import secrets; a='abcdefghjkmnpqrstuvwxyz23456789'; print('-'.join(''.join(secrets.choice(a) for _ in range(5)) for _ in range(4)))")
  HASH=$(PYTHONPATH="$DIR" python3 -c "import sys; from app.auth import hash_password; print(hash_password(sys.argv[1]))" "$SENHA")
  SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
  umask 077
  cat > .env <<EOF
APP_PASSWORD_HASH='${HASH}'
SESSION_SECRET='${SECRET}'
DOMAIN=${DOMAIN}
TRUSTED_HOSTS=${DOMAIN}
COOKIE_SECURE=true
MAX_CONCURRENT_JOBS=${CONCURRENT}
MAX_ACTIVE_JOBS_PER_SESSION=3
MAX_EXTRACTIONS_PER_MINUTE=6
MAX_ITEMS_PER_POST=20
MAX_FILE_MB=500
MAX_ZIP_MB=1024
MAX_VIDEO_SECONDS=1800
EXTRACT_TIMEOUT_SECONDS=45
DOWNLOAD_TIMEOUT_SECONDS=180
JOB_TTL_SECONDS=900
INSTAGRAM_COOKIES_FILE=
EOF
  printf 'Endereço: https://%s\nSenha: %s\n' "$DOMAIN" "$SENHA" > "$SENHA_ARQ"
  chmod 600 "$SENHA_ARQ" .env
fi

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  echo "Firewall do Ubuntu: portas 80 e 443 liberadas."
fi

verde "6/7 Construindo e ligando o site (pode levar de 3 a 10 minutos)"
docker compose up -d --build

verde "7/7 Testando"
ok_local=0
for _ in $(seq 1 30); do
  if docker compose exec -T app python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=4).status==200 else 1)" 2>/dev/null; then
    ok_local=1; break
  fi
  sleep 3
done
[ "$ok_local" -eq 1 ] || { docker compose logs --tail 40 app; erro "O app não respondeu dentro do servidor."; }
echo "App respondendo dentro do servidor."

ok_https=0
for _ in $(seq 1 20); do
  if curl -fsS --max-time 10 "https://${DOMAIN}/healthz" >/dev/null 2>&1; then ok_https=1; break; fi
  sleep 6
done

echo
echo "=============================================================="
echo "  Post-Down Baraoonline.oficial"
echo "  Endereço: https://${DOMAIN}"
echo "  Senha:    ${SENHA}"
echo "  (também salvos em ${SENHA_ARQ}, visível só para o root)"
echo "=============================================================="
if [ "$ok_https" -eq 1 ]; then
  echo "HTTPS funcionando. Abra o endereço no celular e entre com a senha."
else
  aviso "O app está ligado, mas o endereço HTTPS ainda não respondeu de fora.
Causa mais comum: as portas 80 e 443 estão bloqueadas no painel do provedor
(grupo de segurança / firewall da máquina). Libere entrada TCP 80 e 443 e rode:
  cd ${DIR} && docker compose restart caddy
Diagnóstico: docker compose logs --tail 30 caddy"
fi
echo
echo "Atualizar no futuro:  cd ${DIR} && git pull && docker compose up -d --build"
