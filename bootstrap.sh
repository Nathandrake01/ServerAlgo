#!/usr/bin/env bash
# ServerAlgo bootstrap — run ONCE on your own Ubuntu 22.04 (ARM64) server.
#
#   curl -fsSL https://raw.githubusercontent.com/Nathandrake01/ServerAlgo/main/bootstrap.sh | SETUP_BOT_TOKEN=<your-bot-token> bash
#
# or paste this whole thing as the VM's cloud-init "user data" at creation time.
#
# It installs Docker + the ServerAlgo setup agent (a Telegram bot) as a systemd
# service, bound to YOUR bot. You then finish setup by chatting with your bot.
# No credentials are entered here — the bot asks for them later, on this machine.
set -euo pipefail

REPO_URL="https://github.com/Nathandrake01/ServerAlgo.git"
USER_NAME="$(whoami)"
HOME_DIR="$(eval echo "~${USER_NAME}")"
APP_DIR="${HOME_DIR}/serveralgo"
AGENT_DIR="${APP_DIR}/setup_bot"

say() { echo -e "\033[92m[serveralgo]\033[0m $*"; }
die() { echo -e "\033[91m[serveralgo] ERROR:\033[0m $*" >&2; exit 1; }

[ -n "${SETUP_BOT_TOKEN:-}" ] || die "Set SETUP_BOT_TOKEN=<your @BotFather token> and re-run."

# --- 0. permissions & environment ---
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

# --- 0. sanity: ARM64 + Python 3.10 (engine requirement) ---
ARCH="$(uname -m)"
[ "$ARCH" = "aarch64" ] || say "WARNING: arch is $ARCH; the engine needs ARM64 (aarch64)."
PYV="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo none)"
[ "$PYV" = "3.10" ] || say "WARNING: python3 is $PYV; the engine needs 3.10 (Ubuntu 22.04)."

# --- 1. base packages ---
say "Installing base packages..."
$SUDO apt-get update -y -qq
$SUDO apt-get install -y -qq git curl python3-venv python3-pip

# --- 2. docker ---
if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker..."
  curl -fsSL https://get.docker.com | $SUDO bash
  $SUDO usermod -aG docker "$USER_NAME" || true
fi

# --- 3. fetch the agent ---
say "Fetching the setup agent..."
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull --ff-only || true
else
  rm -rf "$APP_DIR"
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

# --- 4. agent venv ---
say "Preparing the agent runtime..."
python3 -m venv "${AGENT_DIR}/.venv"
"${AGENT_DIR}/.venv/bin/pip" install -q --upgrade pip
"${AGENT_DIR}/.venv/bin/pip" install -q "python-telegram-bot>=20,<22" requests

# --- 5. systemd service (runs the bot 24/7, survives reboots) ---
say "Installing the systemd service..."
SERVICE=/etc/systemd/system/serveralgo-setup.service
$SUDO tee "$SERVICE" >/dev/null <<UNIT
[Unit]
Description=ServerAlgo setup + ops bot
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${AGENT_DIR}
Environment=SETUP_BOT_TOKEN=${SETUP_BOT_TOKEN}
Environment=STATE_DIR=${HOME_DIR}/.serveralgo
ExecStart=${AGENT_DIR}/.venv/bin/python ${AGENT_DIR}/setup_agent.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

$SUDO systemctl daemon-reload
$SUDO systemctl enable --now serveralgo-setup.service

say "Done. Open your Telegram bot and send /start to finish setup."
say "  logs:  journalctl -u serveralgo-setup -f"
