---
name: serveralgo-deploy
description: >
  Guide for deploying the ServerAlgo automated options-trading strategies onto
  your own server with one script (deploy.py). Use this when a user has a
  ServerAlgo license key and wants to install, configure, run, update, or
  troubleshoot the trading strategies on their Kotak Neo or Zerodha account.
---

# ServerAlgo — Deployment Guide

You have a **license key** and want to run the automated options strategies
(PR918, PR946, Gamma, Delta) on **your own server** and **your own broker
account**. This guide walks you through it end to end. The strategy logic is a
compiled black box — you control only the leverage and which strategies run.

An assistant using this guide: read the user's situation, check the
prerequisites first (most failures are a wrong server type), then walk them
through the steps. Never ask for or handle their broker passwords, API secrets,
or TOTP secrets yourself — those are typed directly into `deploy.py`, which
stores them only on the user's own server.

---

## 1. Prerequisites — CHECK THESE FIRST

Most deployment failures are a wrong environment, not a bug. Verify all of these
before running anything.

### The server (where strategies run)

| Requirement | Value | Why it is strict |
|---|---|---|
| **Architecture** | **ARM64 (aarch64)** | The engine is shipped as pre-compiled `aarch64` binaries. It will **not run on x86/amd64.** |
| **OS** | **Ubuntu 22.04** | Ships Python **3.10**, which the compiled engine requires. |
| **Python** | **3.10** | The engine binaries are built for CPython 3.10. Ubuntu 24.04 has 3.12 and the engine **will fail to import.** |
| RAM / disk | 4 GB / 20 GB free | OpenAlgo + Docker |
| Access | SSH in as `ubuntu` with a key | You must be able to `ssh -i <key> ubuntu@<server-ip>` |

> **The single most common mistake:** using an x86 server or Ubuntu 24.04. If the
> engine fails to import after deploy, this is almost always why. A good match is
> an **Oracle Cloud Ampere A1** instance running **Ubuntu 22.04**.

Verify on the server:
```bash
uname -m         # must print: aarch64
python3 --version # must print: Python 3.10.x
lsb_release -a   # Ubuntu 22.04
```

### Your own machine (where you run `deploy.py`)

- **Python 3** installed.
- An **SSH client** (`ssh` on PATH). Windows 10/11 has it built in; macOS/Linux
  have it. Both PowerShell and Git Bash work.
- Your server's **SSH private key file** saved locally.

### Accounts

- A **broker account** — Kotak Neo **or** Zerodha — with F&O (options) enabled,
  and API access:
  - **Kotak:** API key, API secret, registered mobile, MPIN, TOTP secret.
  - **Zerodha:** Kite Connect app (API key + secret), user ID, password, TOTP
    secret. You must **whitelist your server's IP** on the Kite developer
    dashboard.
- A **Telegram bot** (create one with **@BotFather**) → bot token + your chat ID
  (get it from **@userinfobot**). Used for trade/error alerts.
- Your **ServerAlgo license key** (from your provider). One key works on **one
  server**.

---

## 2. Get the deployer

On your own machine:
```bash
git clone https://github.com/Nathandrake01/ServerAlgo.git
cd ServerAlgo
```

---

## 3. Run the deployer

```bash
python3 deploy.py
```

It asks a short series of questions — that's all you provide:

| Prompt | What to enter |
|---|---|
| **License key** | The key from your provider. |
| **Server IP** | Public IP of your ARM64 Ubuntu 22.04 server (IP only; the script adds the user). |
| **SSH user** | Usually `ubuntu`. |
| **Path to SSH private key** | Full path to your `.key`/`.pem` file (`~` is fine). Blank = use default `~/.ssh`. |
| **Broker** | `1` = Kotak, `2` = Zerodha. |
| **Broker credentials** | API key, secret, user id, password, and **TOTP secret** — typed directly, stored only on your server. |
| **Telegram bot token** | From @BotFather. You then **send your bot any message** and the deployer finds your chat id automatically. |
| **Strategies** | For each of PR918 / PR946 / Gamma / Delta: enable? and a **lot multiplier** (e.g. `1`). |

For the **Zerodha TOTP secret**: in Kite → Profile → Settings → Password & security →
**External TOTP**. Copy the base32 secret (letters A–Z and digits 2–7). The deployer
checks it's valid before continuing.

Everything else is automatic — you are **not** asked about ports, reinstalling,
API keys, or instance numbers. The deployer:
1. Verifies the server is ARM64 + Python 3.10, installs Docker if missing.
2. Pulls **OpenAlgo**, writes a complete `.env` (seeded from OpenAlgo's own sample), builds and starts it.
3. Downloads the compiled engine, generates your configs + entry scripts.
4. Creates a Python **venv** + dependencies (plus a headless browser for Zerodha).
5. **Auto-creates your OpenAlgo admin account and API key** — you never see or paste one.
6. Writes `run_live.sh` + a **daily cron** (auto-login ~08:55 IST, strategies 09:10 IST, Mon–Fri).
7. Creates **`open_ui.bat` / `open_ui.command`** next to `deploy.py` — your one-click dashboard opener.

First run takes **5–10 minutes** (longer if OpenAlgo's Docker image has to build, or
for Zerodha's browser download). At the end it prints your admin login and how to open
the dashboard — **save these**.

---

## 4. After deployment

**Open the dashboard** by double-clicking the launcher the deployer created next
to `deploy.py`:
- Windows: **`open_ui.bat`**
- Mac: **`open_ui.command`**

This opens a secure SSH tunnel and your browser to `http://127.0.0.1:5000`.
**Keep that window open while you use the dashboard**; close it to disconnect.
The dashboard is deliberately **not** exposed to the public internet — this tunnel
is the way in, and it means you never have to open any firewall/Oracle ports.

> If the browser shows "can't provide a secure connection", you typed (or the
> browser forced) `https://`. Use **`http://127.0.0.1:5000`** — OpenAlgo has no TLS.

Then, one time:
1. Log in with the printed admin credentials and **change the admin password**.
2. **Zerodha only:** in the Kite developer console set the app's **Redirect URL**
   to `http://127.0.0.1:5000/zerodha/callback` and **whitelist your server IP**.
   Without the IP whitelist, orders get rejected.

**Each trading morning:** open the dashboard and click **Connect** for your broker
so the day's session is live before 09:10 IST (auto-login is best-effort — always
confirm the UI shows "connected"). Then cron launches the strategies automatically
and alerts come to your Telegram.

---

## 5. Everyday operations

**Change leverage (lot multiplier):**
```bash
ssh -i <key> ubuntu@<server-ip>
nano /home/ubuntu/openalgo/strategies/configs/pr_0918.live.yaml
# change:  quantity_lots: 1   ->   quantity_lots: 2
```
Changes take effect at the next market open (running processes do not reload
mid-session).

**See logs:**
```bash
ls /home/ubuntu/openalgo/strategies/logs/
tail -f /home/ubuntu/openalgo/strategies/logs/pr_0918_*.log
```

**Update the engine** (when a new version is released): re-run `python3 deploy.py`
and choose to keep the existing installation. Your configs and credentials are
preserved.

---

## 6. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| **"Cannot run SSH from this terminal" / Access denied** | Old issue on Microsoft Store Python. Update to the latest `deploy.py`. If SSH itself fails, verify `ssh -i <key> ubuntu@<ip>` works manually first. |
| **"Invalid license key"** | Typo, or the key is already used on another server (one key = one server). |
| **`Permission denied (publickey)`** | Your SSH key isn't authorized on the server, or you gave the wrong key path. Test with `ssh -i <key> ubuntu@<ip>` first. |
| **Engine fails to import / strategies crash instantly** | Wrong server: must be **ARM64 + Ubuntu 22.04 (Python 3.10)**. Check `uname -m` (aarch64) and `python3 --version` (3.10). This is the #1 cause. |
| **OpenAlgo not answering on :5000** | First image build can take several minutes. Check `docker logs openalgo-web` and `docker ps`. |
| **No trades placed** | Broker not logged in (whitelist your IP; check `auto_login.log`), or market closed / not a trading day for that strategy's index routing. |
| **Zerodha login fails** | Kite IP whitelist missing, wrong TOTP secret, or the browser dependencies didn't install. Check `auto_login.log`. |

---

## 7. What each strategy does (high level)

| Strategy | Style | Entry window (IST) | Exit | Trading days |
|---|---|---|---|---|
| **PR918** | Premium-reentry, sells the richer ATM side, hedged | 09:18–10:15 | 14:30 | Mon–Fri |
| **PR946** | Same engine, later scan | 09:46–10:15 | 14:30 | Mon–Fri |
| **Gamma** | Long single ATM option (insurance) | 12:00–14:30 | 15:05 | Mon–Thu |
| **Delta** | Short strangle | from 09:20 | 15:05 | Mon–Thu |

Risk parameters (stops, hedging, sizing rules) are fixed inside the engine. You
control only which strategies run and at what lot multiplier.

---

## 8. Security notes

- Your broker credentials and Telegram tokens are stored **only in `.env` on your
  own server** — never transmitted elsewhere, never in this repo.
- Strategy logic is compiled to native binaries; source is not included.
- Your SSH private key never leaves your machine.
- Never paste your broker password, API secret, or TOTP secret into a chat, an
  LLM, or any web form. They go **only** into `deploy.py`'s prompts.
