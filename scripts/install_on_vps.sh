#!/usr/bin/env bash
#
# HPB Blog Auto-Post: One-shot Linux VPS installer
#
# Tested on Ubuntu 22.04 / 24.04 (Japan-region VPS).
# Run this in the VPS web console as a user with sudo privileges:
#
#   curl -fsSL https://raw.githubusercontent.com/tanukichiyamaguchi/HPB-Blog/main/scripts/install_on_vps.sh | bash
#
# (Or git clone the repo first, then bash scripts/install_on_vps.sh)
#
# Sets up:
#   - System dependencies (python3.11, git, Chromium libs, locales)
#   - Repo cloned at /opt/hpb-blog (or pulled if already present)
#   - venv + Python deps + Playwright Chromium
#   - .env template (user must fill in API keys + credentials)
#   - Daily cron entry at JST 22:15 (UTC 13:15) — RUN_SALON_BOARD_POST=schedule

set -euo pipefail

INSTALL_DIR="${HPB_BLOG_INSTALL_DIR:-/opt/hpb-blog}"
REPO_URL="${HPB_BLOG_REPO_URL:-https://github.com/tanukichiyamaguchi/HPB-Blog.git}"
REPO_BRANCH="${HPB_BLOG_BRANCH:-main}"
RUN_USER="${HPB_BLOG_USER:-$(id -un)}"

echo "==================================================="
echo " HPB Blog Auto-Post: VPS Installer"
echo "==================================================="
echo "  INSTALL_DIR = $INSTALL_DIR"
echo "  REPO_URL    = $REPO_URL"
echo "  REPO_BRANCH = $REPO_BRANCH"
echo "  RUN_USER    = $RUN_USER"
echo ""

# ---- 0. Pre-flight salonboard.com reachability check ----
echo "[0/6] Pre-flight: verifying this VPS can reach salonboard.com ..."
PUBLIC_IP="$(curl -fsS --max-time 10 https://api.ipify.org || echo unknown)"
echo "  This VPS's public IP: $PUBLIC_IP"

# Quick geo info (informational)
curl -fsS --max-time 10 "https://ipinfo.io/${PUBLIC_IP}" 2>/dev/null \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print(f'  Geo: {d.get(\"city\")}, {d.get(\"region\")}, {d.get(\"country\")} ({d.get(\"org\")})')" \
    2>/dev/null || true

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
SB_STATUS="$(curl -I -s -o /dev/null -w '%{http_code}' --max-time 20 -A "$UA" https://salonboard.com/login/ || echo "000")"
echo "  HTTP status from this VPS to https://salonboard.com/login/ : $SB_STATUS"

if [ "$SB_STATUS" = "000" ]; then
    echo ""
    echo "  ❌ STOP: This VPS cannot reach salonboard.com (timeout / no response)."
    echo "     Akamai's bot manager is likely blocking this IP range."
    echo "     Options:"
    echo "       1) Try a different VPS provider / region (Japan residential-style IP)"
    echo "       2) If on Oracle Cloud, terminate this instance and retry in a"
    echo "          different Availability Domain"
    echo "       3) Use a paid Japan VPS (Sakura, Conoha, Onamae VPS — typically"
    echo "          have cleaner IP reputation)"
    echo ""
    echo "     This VPS is NOT viable for the salon-board automation. Aborting setup."
    exit 2
fi

if [ "$SB_STATUS" = "403" ] || [ "$SB_STATUS" = "429" ]; then
    echo ""
    echo "  ⚠️  WARNING: salonboard.com returned HTTP $SB_STATUS — application-layer"
    echo "     block. The site responded but rejected the request. Real-browser"
    echo "     (Playwright) MAY still work since it has correct TLS fingerprint."
    echo "     Continuing setup, but be prepared for login failures."
    sleep 3
fi

echo "  ✅ salonboard.com is reachable from this VPS. Proceeding."
echo ""

# ---- 1. OS deps ----
echo "[1/6] Installing OS dependencies..."
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo apt-get install -y \
        python3.11 python3.11-venv python3.11-dev python3-pip \
        git curl ca-certificates locales tzdata \
        libnss3 libnspr4 libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libpango-1.0-0 libcairo2 fonts-noto-cjk libcups2t64 \
        || sudo apt-get install -y \
            python3 python3-venv python3-dev python3-pip \
            git curl ca-certificates locales tzdata \
            libnss3 libnspr4 libasound2 libatk-bridge2.0-0 libatk1.0-0 \
            libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
            libgbm1 libpango-1.0-0 libcairo2 fonts-noto-cjk libcups2
    # Set JST locale (some installers prefer this for cron clarity)
    sudo locale-gen ja_JP.UTF-8 || true
    sudo timedatectl set-timezone Asia/Tokyo 2>/dev/null || true
else
    echo "[ERROR] Only Debian/Ubuntu (apt) is supported by this installer."
    echo "Manual setup required for $(uname -a)."
    exit 1
fi

# ---- 2. Clone / pull repo ----
echo "[2/6] Setting up repository at $INSTALL_DIR..."
if [ ! -d "$INSTALL_DIR/.git" ]; then
    sudo mkdir -p "$(dirname "$INSTALL_DIR")"
    sudo git clone --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
    sudo chown -R "$RUN_USER:$RUN_USER" "$INSTALL_DIR"
else
    echo "  (already cloned, pulling latest)"
    cd "$INSTALL_DIR"
    sudo -u "$RUN_USER" git fetch origin
    sudo -u "$RUN_USER" git checkout "$REPO_BRANCH"
    sudo -u "$RUN_USER" git pull --ff-only origin "$REPO_BRANCH"
fi

cd "$INSTALL_DIR"

# ---- 3. venv + Python deps ----
echo "[3/6] Setting up Python virtualenv..."
PYTHON_BIN="$(command -v python3.11 || command -v python3)"
if [ ! -d "$INSTALL_DIR/.venv" ]; then
    sudo -u "$RUN_USER" "$PYTHON_BIN" -m venv "$INSTALL_DIR/.venv"
fi
sudo -u "$RUN_USER" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip --quiet
sudo -u "$RUN_USER" "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet

# ---- 4. Playwright Chromium ----
echo "[4/6] Installing Playwright Chromium (this can take a few minutes)..."
sudo -u "$RUN_USER" "$INSTALL_DIR/.venv/bin/python" -m playwright install chromium

# ---- 5. .env template ----
echo "[5/6] Preparing .env..."
if [ ! -f "$INSTALL_DIR/.env" ]; then
    if [ -f "$INSTALL_DIR/.env.example" ]; then
        sudo -u "$RUN_USER" cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
        echo "  .env created from .env.example — YOU MUST EDIT IT below."
    fi
else
    echo "  .env already present (kept)."
fi
# Ensure logs and screenshots dirs exist
sudo -u "$RUN_USER" mkdir -p "$INSTALL_DIR/logs" "$INSTALL_DIR/screenshots" "$INSTALL_DIR/output"

# ---- 6. Cron entry ----
echo "[6/6] Installing daily cron entry (JST 22:15 = UTC 13:15)..."
CRON_LINE="15 13 * * * cd $INSTALL_DIR && RUN_SALON_BOARD_POST=schedule UPDATE_THEME_HISTORY=true $INSTALL_DIR/.venv/bin/python -m src.main >> $INSTALL_DIR/logs/cron-\$(date +\%Y\%m).log 2>&1"
( sudo -u "$RUN_USER" crontab -l 2>/dev/null | grep -v "$INSTALL_DIR" || true ; echo "$CRON_LINE" ) | sudo -u "$RUN_USER" crontab -
echo "  Cron entries:"
sudo -u "$RUN_USER" crontab -l | sed 's/^/    /'

echo ""
echo "==================================================="
echo " [SUCCESS] Installation complete."
echo "==================================================="
echo ""
echo "Next steps:"
echo ""
echo "  1. Edit secrets in $INSTALL_DIR/.env"
echo "       sudo -u $RUN_USER nano $INSTALL_DIR/.env"
echo ""
echo "     Required values:"
echo "       ANTHROPIC_API_KEY=sk-ant-..."
echo "       GEMINI_API_KEY=..."
echo "       SALON_BOARD_ID=..."
echo "       SALON_BOARD_PASSWORD=..."
echo "       SLACK_WEBHOOK_URL=...    (optional)"
echo ""
echo "  2. Test run (AI generation only, no Salon Board post):"
echo "       cd $INSTALL_DIR && RUN_SALON_BOARD_POST=skip .venv/bin/python -m src.main"
echo ""
echo "  3. Then test a draft post to Salon Board:"
echo "       cd $INSTALL_DIR && RUN_SALON_BOARD_POST=draft .venv/bin/python -m src.main"
echo ""
echo "  4. The daily cron at JST 22:15 will then run automatically."
echo "     Logs go to $INSTALL_DIR/logs/cron-YYYYMM.log"
echo ""
