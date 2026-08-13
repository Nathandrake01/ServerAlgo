# Trading Strategy Engine

Automated options trading for Indian markets (NIFTY / SENSEX).  
Deploys on your server in **one command**. You control the leverage.  
The strategy logic is compiled — source code is not included.

---

## Quick start

```bash
# Prerequisites: Python 3 on your machine, SSH access to your server
python3 deploy.py
```

Answer the questions. The script handles Docker, OpenAlgo, broker setup, Telegram alerts, and cron. Takes ~5 minutes.

---

## What you get

| Component | Source |
|---|---|
| OpenAlgo (trading platform) | Pulled from [marketcalls/openalgo](https://github.com/marketcalls/openalgo) |
| Broker connectivity | Kotak Neo or Zerodha |
| Telegram alerts | Trade entries, errors, EOD summary |
| Auto-login | TOTP-based (Playwright for Zerodha) |
| Daily cron | Auto-login at 08:55 IST, strategies at 09:11 IST |

---

## Available strategies

| Strategy | Type | Entry window | Exit |
|---|---|---|---|
| **PR918** | Premium Reentry | 09:18 – 10:15 IST | 14:30 |
| **PR946** | Premium Reentry | 09:46 – 10:15 IST | 14:30 |
| **Gamma** | Long insurance | 12:00 – 14:30 | 15:05 |

You choose which strategies to run and at what multiplier (1x, 5x, 12x, etc.).

---

## Requirements

| | Minimum |
|---|---|
| **Server OS** | Ubuntu 22.04 (Python 3.10 — required by the engine) |
| **Architecture** | ARM64 (aarch64) only |
| **RAM** | 4 GB |
| **Disk** | 20 GB free |
| **Broker** | Kotak Neo or Zerodha (F&O enabled) |
| **Your machine** | Python 3 with SSH access to the server |

---

## After deployment

```
Your server
├── /home/ubuntu/openalgo/
│   ├── .env                    ← Broker credentials (never shared)
│   ├── docker-compose.yaml     ← Container config
│   │
│   └── strategies/
│       ├── engine/             ← Compiled strategy (.so — not readable)
│       ├── configs/            ← Your strategy configs
│       │   ├── pr_0918.live.yaml
│       │   ├── pr_0946.live.yaml
│       │   └── gamma.live.yaml
│       ├── logs/               ← Daily logs (JSONL)
│       └── status/             ← Current positions & PnL
│
Web UI:    http://<your-ip>:5000
Admin:     admin / <generated password>
```

---

## Changing leverage

Edit the config files on your server:
```bash
ssh ubuntu@<your-server>
nano /home/ubuntu/openalgo/strategies/configs/pr_0918.live.yaml
# Change: quantity_lots: 12 → quantity_lots: 24
# Restart: docker restart openalgo-web
```

Changes take effect at the next market open.

---

## Updating the engine

When a new version is released:
```bash
python3 deploy.py
```
Select "Use existing installation" when prompted. Configs and credentials are preserved.

---

## Security

- Strategy logic is **compiled to native binaries** (.so files) — not readable
- Broker credentials are stored **only on your server** in `.env`
- Telegram tokens never leave your server
- SSH key never leaves your machine
