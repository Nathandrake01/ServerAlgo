# ServerAlgo — Automated Trading Strategy Engine

Automated options trading strategies for Indian markets (NIFTY / SENSEX).  
Deploys on your server via an interactive **Telegram Setup Bot**. You control the lot sizes and leverage — the strategy execution logic is compiled and secure.

---

## ⚡ Quickstart (Zero-Friction Deployment)

You only need **1 command** from your laptop terminal to kick off the setup. Everything else happens inside **Telegram**.

### Step 1: Get Your Telegram Bot Token (1 minute)
1. Open Telegram on your phone or desktop and search for **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot`.
3. Give your bot a name (e.g. `My Trading Bot`) and a username (e.g. `my_algo_trader_bot`).
4. Copy the **HTTP API Bot Token** provided by BotFather (e.g., `8636830081:AAEcNjTE...`).

---

### Step 2: Run the 1-Line Installer from Your Laptop Terminal

Run this single command from your Mac / Windows PowerShell / Linux terminal without needing to SSH first:

```bash
# If using an SSH Key:
ssh -i /path/to/your_key.key ubuntu@<YOUR_SERVER_IP> 'curl -fsSL https://raw.githubusercontent.com/Nathandrake01/ServerAlgo/main/bootstrap.sh | SETUP_BOT_TOKEN="<YOUR_BOT_TOKEN>" bash'

# Or if logging in with password:
ssh ubuntu@<YOUR_SERVER_IP> 'curl -fsSL https://raw.githubusercontent.com/Nathandrake01/ServerAlgo/main/bootstrap.sh | SETUP_BOT_TOKEN="<YOUR_BOT_TOKEN>" bash'
```

---

### Step 3: Complete Onboarding in Telegram

1. Open your new bot in Telegram and send `/start`.
2. Enter your **License Key**.
3. Tap **[ Zerodha (Kite) ]** or **[ Kotak Neo ]**.
4. Enter credentials (passwords, MPIN, and TOTP secrets are auto-deleted from chat after receipt).
5. Choose your strategies & lots (e.g. `pr_0918:1 gamma:1`).
6. Tap **[ 🚀 Deploy Strategies ]**.

---

## 🛡️ Security & Zero-Custody Guarantee

- **Zero Provider Custody:** All broker credentials, passwords, and TOTP secrets stay 100% on **your own server** in an encrypted `.env` file.
- **Auto-Deleting Secrets:** Credential messages sent during Telegram onboarding are immediately deleted from chat history.
- **Compiled Black-Box Logic:** Strategy engine parameters and execution models are compiled into high-performance native binaries (`.so`).
- **24/7 Mobile Control:** After setup, the bot becomes your personal ops controller (`/status`, `/positions`, `/exit_all`, `/killall`).

---

## 📋 System Requirements

| Specification | Minimum Requirement |
| :--- | :--- |
| **Server OS** | Ubuntu 22.04 LTS (Python 3.10) |
| **Architecture** | ARM64 (aarch64) recommended (Oracle Cloud, AWS Graviton, etc.) |
| **RAM** | 2 GB minimum (4 GB recommended) |
| **Disk Space** | 20 GB SSD |
| **Broker** | Zerodha Kite or Kotak Neo (F&O segment enabled) |

---

## 📖 Full Documentation
For complete step-by-step instructions and runbook details, see:
👉 **[SERVERALGO_CLIENT_SETUP.md](SERVERALGO_CLIENT_SETUP.md)**
