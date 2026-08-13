# ServerAlgo — Client Onboarding & Setup Guide

Welcome to **ServerAlgo**! This guide will take you step-by-step through setting up your automated options trading system on your own server using our conversational Telegram setup bot.

> [!NOTE]
> **Privacy & Security Guarantee:** Your broker API keys, passwords, and TOTP secrets stay 100% on **your own server** inside an encrypted `.env` file. They are never sent to ServerAlgo or any third-party servers. All secret messages sent during Telegram setup are automatically deleted from the chat immediately after being saved locally.

---

## 📋 Prerequisites

Before starting, ensure you have:
1. **An Ubuntu 22.04 LTS Server** (ARM64 recommended, e.g. Oracle Cloud, AWS EC2, Hetzner, DigitalOcean, or any VPS).
2. **A Telegram Account** on your phone or desktop.
3. **Your ServerAlgo License Key** (provided by Sumit).
4. **Broker Credentials:**
   - **Zerodha Kite:** API Key, API Secret, User ID, Password, External TOTP Secret key.
   - *OR* **Kotak Neo:** 10-digit Mobile Number, 6-digit MPIN, API Key, API Secret, TOTP Secret key.

---

## 🤖 Step 1: Create Your Telegram Bot (2 minutes)

1. Open **Telegram** and search for `@BotFather`.
2. Send the message `/newbot`.
3. Enter a friendly name for your bot (e.g. `My Trading Bot`).
4. Enter a unique username ending in `bot` (e.g. `my_algo_trading_bot`).
5. `@BotFather` will reply with an **HTTP API Token** (e.g. `8636830081:AAEcNjTEB9VW1uh3E8K6...`).
6. **Copy this Bot Token** — you will need it in Step 2.

---

## ⚡ Step 2: One-Line Server Setup

1. Open your terminal (or PowerShell / Putty) and **SSH into your Ubuntu server**:
   ```bash
   ssh ubuntu@<YOUR_SERVER_IP>
   ```

2. Run the following single setup command (replace `<YOUR_BOT_TOKEN>` with the token from Step 1):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/Nathandrake01/ServerAlgo/main/bootstrap.sh | SETUP_BOT_TOKEN="<YOUR_BOT_TOKEN>" bash
   ```

*(This automatically installs Docker, configures the setup service, and links it to your Telegram bot in under 60 seconds).*

---

## 💬 Step 3: Complete Onboarding in Telegram Chat

1. Open Telegram and search for your newly created bot username (e.g., `@my_algo_trading_bot`).
2. Tap **START** or type `/start`.
3. Follow the interactive chat prompts:
   - **License Key:** Paste your ServerAlgo license key.
   - **Broker Selection:** Tap `[ Zerodha (Kite) ]` or `[ Kotak Neo ]`.
   - **Credentials:** Send each requested credential one at a time (passwords and secrets are auto-deleted from chat).
   - **Telegram Token:** Tap `[ ⏩ Reuse Current Bot Token ]`.
   - **Strategy Allocation:** Type your desired strategy & lot counts (e.g. `pr_0918:1 gamma:1`).
   - **Confirmation:** Tap `[ 🚀 Deploy Strategies ]`.

The bot will stream real-time progress as it builds Docker containers and prepares your trading runtime.

---

## 🛠️ Step 4: Daily Operations & Control Commands

Once deployment completes, your setup bot automatically becomes your **Operations Control Bot**.

### 1. One-Time Broker Portal Setup
- **Zerodha:** Log into Kite Developer Portal and set:
  - **Redirect URL:** `http://<YOUR_SERVER_IP>:5000/zerodha/callback`
  - **IP Whitelist:** `<YOUR_SERVER_IP>`
- **Kotak:** Set:
  - **Redirect URL:** `http://<YOUR_SERVER_IP>:5000/kotak/callback`
  - **IP Whitelist:** `<YOUR_SERVER_IP>`


### 2. Daily Connection Nudge
Each morning before **09:10 AM IST** (Mon–Fri), open your OpenAlgo dashboard at `http://<YOUR_SERVER_IP>:5000` and click **Connect Broker** to authorize the day's session.

### 3. Telegram Control Commands
You can send these commands to your bot anytime:
- `/status` — View Docker container health, active strategies, and recent log outputs.
- `/positions` — View live open positions from OpenAlgo.
- `/exit_all` — Terminate all running strategy instances immediately.
- `/killall` — Emergency stop for OpenAlgo container and strategy runners.

---

## 🔄 Resetting / Restarting Setup from Scratch

If you ever need to completely wipe your server and re-install:

```bash
sudo systemctl stop serveralgo-setup.service 2>/dev/null
sudo systemctl disable serveralgo-setup.service 2>/dev/null
sudo rm -f /etc/systemd/system/serveralgo-setup.service
pkill -f "run_with_env.py" 2>/dev/null
pkill -f "setup_agent.py" 2>/dev/null
docker stop openalgo-web 2>/dev/null && docker rm openalgo-web 2>/dev/null
docker system prune -af --volumes 2>/dev/null
rm -rf ~/openalgo ~/serveralgo ~/.serveralgo /tmp/engine.tar.gz
```

After running the wipe command, repeat **Step 2** to start fresh!
