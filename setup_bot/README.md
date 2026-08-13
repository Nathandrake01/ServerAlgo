# ServerAlgo Telegram Onboarding & Setup Agent

A conversational Telegram onboarding agent for ServerAlgo clients. Instead of running a deployment script on a laptop, the client runs a one-line bootstrap on **their own server**, then completes setup by **chatting with their own Telegram bot**.

Secrets stay 100% local on the client's machine — the provider (Sumit) never sees or touches them.

---

## Architecture & Principles
- **`bootstrap.sh`** — One-line shell script run on the client's server (or cloud-init). Installs Docker and registers `serveralgo-setup.service` bound to the client's Telegram bot token.
- **`setup_bot/setup_agent.py`** — Telegram bot backend with interactive inline buttons (`Zerodha` / `Kotak`), secret auto-deletion, license verification, strategy allocation, and ops control commands (`/status`, `/positions`, `/exit_all`, `/killall`).
- **`setup_bot/deploy_core.py`** — Local deployment runner (no SSH). Handles `.env` seeding, crypto key preservation on re-runs, and OpenAlgo API key provisioning.

---

## Client Quickstart Guide
For full step-by-step instructions to send to clients or friends, see:
👉 **[`SERVERALGO_CLIENT_SETUP.md`](../SERVERALGO_CLIENT_SETUP.md)**

```bash
# One-line server installer command
curl -fsSL https://raw.githubusercontent.com/Nathandrake01/ServerAlgo/main/bootstrap.sh \
  | SETUP_BOT_TOKEN="<YOUR_BOT_TOKEN>" bash
```

---

## Verification & Tests
Unit tests for `deploy_core` and `setup_agent`:
```bash
python -m unittest setup_bot/test_setup_bot.py
```
