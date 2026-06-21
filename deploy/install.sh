#!/bin/bash
# Install gopher-proxy on a Debian/Ubuntu Linode (or similar VPS).
#
# Usage (on the server as root):
#   curl -fsSL https://raw.githubusercontent.com/wrstone/gopher-proxy/main/deploy/install.sh | sudo bash
#
# Or with a custom hostname:
#   sudo GOPHER_PROXY_HOST=gopher.wrstone.com CERTBOT_EMAIL=wrs@wrstone.com bash install.sh

set -euo pipefail

GOPHER_PROXY_HOST="${GOPHER_PROXY_HOST:-gopher.wrstone.com}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-wrs@wrstone.com}"
REPO_URL="${REPO_URL:-https://github.com/wrstone/gopher-proxy.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/gopher-proxy}"
SERVICE_USER="${SERVICE_USER:-gopher-proxy}"
BRANCH="${BRANCH:-main}"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root (or via sudo)." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 nginx certbot python3-certbot-nginx

if ! id "$SERVICE_USER" &>/dev/null; then
  useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" fetch origin
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
else
  rm -rf "$INSTALL_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

install -m 0644 "$INSTALL_DIR/deploy/gopher-proxy.service" /etc/systemd/system/gopher-proxy.service
systemctl daemon-reload
systemctl enable gopher-proxy
systemctl restart gopher-proxy

sed "s/GOPHER_PROXY_HOST/${GOPHER_PROXY_HOST}/g" \
  "$INSTALL_DIR/deploy/nginx-gopher-proxy.conf" \
  > /etc/nginx/sites-available/gopher-proxy
ln -sf /etc/nginx/sites-available/gopher-proxy /etc/nginx/sites-enabled/gopher-proxy
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

if command -v ufw &>/dev/null && ufw status | grep -q inactive; then
  :
elif command -v ufw &>/dev/null; then
  ufw allow OpenSSH
  ufw allow 'Nginx Full'
fi

if ! certbot certificates 2>/dev/null | grep -q "Domains:.*${GOPHER_PROXY_HOST}"; then
  certbot --nginx -d "$GOPHER_PROXY_HOST" --non-interactive --agree-tos -m "$CERTBOT_EMAIL" --redirect
else
  certbot renew --quiet || true
fi

echo ""
echo "Gopher proxy installed."
echo "  Host:     https://${GOPHER_PROXY_HOST}/"
echo "  Embed:    https://${GOPHER_PROXY_HOST}/embed?url=gopher%3A%2F%2Fsdf.org%2Fusers%2Fwrstone%2F"
echo "  Service:  systemctl status gopher-proxy"
echo ""
echo "Point DNS A/AAAA for ${GOPHER_PROXY_HOST} at this server's public IP before using HTTPS."