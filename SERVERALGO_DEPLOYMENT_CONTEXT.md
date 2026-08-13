# ServerAlgo & OpenAlgo — Comprehensive Deployment, Architecture & Runbook Context

> **System Status:** Fully Operational & Deployed  
> **Server IP:** `130.210.54.238`  
> **SSH Access:** `ssh -i /Users/apple/Desktop/Trading/a1_instance.key ubuntu@130.210.54.238`  
> **Dashboard:** [http://130.210.54.238:5000](http://130.210.54.238:5000)  
> **Last Verified:** 2026-08-13 (All tests, OAuth token capture, and master contracts passing)

---

## 1. System Topology & Architecture

```
                                +--------------------------------------------+
                                |             Client Devices                 |
                                |  - Telegram App (Mobile/Desktop)           |
                                |  - Web Browser (http://130.210.54.238:5000)|
                                +---------------------+----------------------+
                                                      |
                                                      | Public Internet (TCP)
                                                      v
+---------------------------------------------------------------------------------------------------------+
| Ubuntu 22.04 LTS (ARM64 / aarch64) Host Server: 130.210.54.238                                          |
|                                                                                                         |
|  +-------------------------------------+        +----------------------------------------------------+  |
|  | systemd: serveralgo-setup.service   |        | Docker Container: openalgo-web (Restart=always)    |  |
|  | Python 3.10 (.venv)                 |        |                                                    |  |
|  | Long-polling Telegram Ops Agent     |        |  - Flask / Gunicorn Web App (Port 5000)            |  |
|  | Handles /status, /positions, etc.   |        |  - WebSocket Proxy (Port 8765)                     |  |
|  +-------------------------------------+        |  - SQLite DB (/app/db/openalgo.db)                 |  |
|                                                 |  - SymToken Cache (114,000+ F&O symbols)           |  |
|  +-------------------------------------+        +----------------------------------------------------+  |
|  | Cron Daemon Automation (User: ubuntu)|                                   ^                            |
|  |                                     |                                   |                            |
|  | 08:58 IST: auto_login_zerodha.py    | ----------------------------------+                            |
|  |   (Playwright Headless Chrome)      |                                   |                            |
|  |                                     |                                   | (REST API via 127.0.0.1)   |
|  | 09:10 IST: run_live.sh              | ----------------------------------+                            |
|  |   (delta, gamma, pr_0918, pr_0946)  |                                                                |
|  +-------------------------------------+                                                                |
+---------------------------------------------------------------------------------------------------------+
                                                      |
                                                      | Outbound HTTPS
                                                      v
                                        +----------------------------+
                                        | Zerodha Kite Connect API   |
                                        | https://kite.zerodha.com   |
                                        +----------------------------+
```

---

## 2. Directory Layout & Key Files

### 📂 Directory Structure on Server (`ubuntu@130.210.54.238`)

| Path | Purpose | Key Files |
| :--- | :--- | :--- |
| `/home/ubuntu/openalgo/` | Core OpenAlgo platform & strategy execution runtime | `docker-compose.yaml`, `.env`, `run_with_env.py`, `run_live.sh`, `auto_login_zerodha.py` |
| `/home/ubuntu/openalgo/strategies/` | Python strategy entry points & engine binaries | `delta.py`, `gamma.py`, `pr_0918.py`, `pr_0946.py`, `engine/` |
| `/home/ubuntu/openalgo/strategies/configs/` | YAML configurations per strategy (lots, mode) | `delta.live.yaml`, `gamma.live.yaml`, `pr_0918.live.yaml`, `pr_0946.live.yaml` |
| `/home/ubuntu/openalgo/strategies/logs/` | Daily live strategy logs | `delta_YYYY-MM-DD.log`, `gamma_YYYY-MM-DD.log`, etc. |
| `/home/ubuntu/serveralgo/` | Source repository for bootstrap & setup agent | `bootstrap.sh`, `clients.json`, `setup_bot/` |
| `/home/ubuntu/serveralgo/setup_bot/` | Telegram interactive onboarding and ops bot | `setup_agent.py`, `deploy_core.py`, `.venv/` |
| `/home/ubuntu/.serveralgo/` | Persistent local state for Telegram bot | `agent_state.json` (Stores paired Chat ID & deployment flag) |
| `/etc/systemd/system/` | System service definition | `serveralgo-setup.service` |

---

## 3. Credentials, Secrets & Authentication

> [!IMPORTANT]
> All secrets stay 100% local on the client's Ubuntu host inside `/home/ubuntu/openalgo/.env`. They are never sent to external servers.

### 🔑 Authentication Matrix

| Key / Account | Current Value / Setting | Storage Location | Notes |
| :--- | :--- | :--- | :--- |
| **OpenAlgo Admin User** | `admin` | `/app/db/openalgo.db` & `.env` | Primary dashboard administrator |
| **OpenAlgo Admin Password** | `xkhr2sk2h7au` | `/app/db/openalgo.db` (Argon2 hashed) | Synced with `puneet.chotia@gmail.com` |
| **OpenAlgo API Key** | `509bf55b5f6d8f6e...` | `/app/db/openalgo.db` & `.env` | Bound to `admin`, used by strategies (`api_key: auto`) |
| **Telegram Setup Bot Token** | `8686644609:AAHbyei-...` | `/etc/systemd/system/serveralgo-setup.service` | Token from `@BotFather` |
| **Zerodha User ID** | `XG2933` | `/home/ubuntu/openalgo/.env` | Zerodha Kite trading ID |
| **Zerodha Password** | Stored in `.env` | `/home/ubuntu/openalgo/.env` | Used by Playwright for morning headless login |
| **Zerodha TOTP Secret** | Stored in `.env` | `/home/ubuntu/openalgo/.env` | Base32 TOTP secret key for automatic 2FA |
| **Zerodha Kite API Key** | `brwt2qz1fo6m5b33` | `/home/ubuntu/openalgo/.env` | Developer App API Key |
| **Zerodha Kite API Secret** | Stored in `.env` | `/home/ubuntu/openalgo/.env` | Developer App API Secret |

---

## 4. Network, Ports & Broker Whitelisting

### 🌐 Ports & Bindings

* **Port `5000` (TCP):** OpenAlgo Web UI & REST API (`0.0.0.0:5000->5000/tcp`). Accessible at `http://130.210.54.238:5000`.
* **Port `8765` (TCP):** WebSocket Proxy (`127.0.0.1:8765->8765/tcp`).
* **Port `22` (TCP):** SSH Administration (`ubuntu@130.210.54.238`).

### ⚙️ Zerodha Kite Developer Portal Configuration

Log in to [developers.kite.trade](https://developers.kite.trade/) and configure your app:

1. **Redirect URL:**
   ```text
   http://130.210.54.238:5000/zerodha/callback
   ```
   *(Must match this public URL so that OAuth redirects land on your server rather than `127.0.0.1`).*
2. **IP Whitelist:**
   ```text
   130.210.54.238
   ```

---

## 5. Daily Automated Lifecycle (Cron Schedule)

The crontab on the server (`crontab -l`) controls the daily execution:

```cron
# 08:58 AM IST (03:28 UTC) - Headless Zerodha OAuth Authentication
28 3 * * 1-5 cd /home/ubuntu/openalgo && /home/ubuntu/openalgo/venv/bin/python auto_login_zerodha.py >> /home/ubuntu/openalgo/auto_login.log 2>&1

# 09:10 AM IST (03:40 UTC) - Launch All Deployed Strategies
40 3 * * 1-5 /home/ubuntu/openalgo/run_live.sh >> /home/ubuntu/openalgo/run_live.log 2>&1
```

### 🔁 Sequence of Operations Each Trading Day:

1. **At 08:58 AM IST (`auto_login_zerodha.py`):**
   * Launches headless Chromium via Playwright.
   * Logs into OpenAlgo local session (`http://127.0.0.1:5000/login`).
   * Opens Kite Connect login portal with API key.
   * Fills `ZERODHA_USER_ID` and `ZERODHA_PASSWORD`.
   * Automatically calculates current TOTP using `ZERODHA_TOTP_SECRET` and submits 2FA.
   * Follows redirect to `/zerodha/callback` with authenticated OpenAlgo session.
   * Exchanges `request_token` for persistent `access_token`.
   * Verifies `/api/v1/positionbook` connection.
   * Verifies master contract table (`NFO`, `BFO` symbols).
   * Dispatches a Telegram confirmation alert: *"Zerodha auto-login COMPLETE"*.

2. **At 09:10 AM IST (`run_live.sh`):**
   * Kills any stale previous runner processes.
   * Initializes log directories with date-stamped log files.
   * Spawns 4 background processes via `nohup` for each strategy:
     * `delta.py` (`--config strategies/configs/delta.live.yaml`)
     * `gamma.py` (`--config strategies/configs/gamma.live.yaml`)
     * `pr_0918.py` (`--config strategies/configs/pr_0918.live.yaml`)
     * `pr_0946.py` (`--config strategies/configs/pr_0946.live.yaml`)

---

## 6. Telegram Bot Control Center

Your Telegram Bot acts as your mobile remote control 24/7.

| Command | Action Performed |
| :--- | :--- |
| `/status` | Returns Docker container health, active strategy PID list, and tails the latest 3 log files. |
| `/positions` | Calls OpenAlgo API to fetch and render all open trading positions as JSON. |
| `/exit_all` | Sends a graceful termination signal (`pkill`) to all running strategy processes. |
| `/killall` | Emergency stop: kills all strategy runners and immediately stops the `openalgo-web` container. |
| `/start` | Starts or re-pairs the onboarding flow. |

---

## 7. Edge Cases, Troubleshooting & Failure Recovery

### 🚨 Edge Case 1: Browser says "This site can’t provide a secure connection" / SSL Error
* **Cause:** Modern browsers auto-upgrade URLs to `https://`. Port 5000 is running plaintext `http://`.
* **Fix:** Ensure you type `http://130.210.54.238:5000` explicitly in the URL bar (or use an incognito window).

### 🚨 Edge Case 2: Browser says "Access Denied" or fails to load on `127.0.0.1:5000/zerodha/callback`
* **Cause:** The Redirect URL on the Zerodha Developer Portal is set to `127.0.0.1` instead of your server IP.
* **Fix:** Update your Redirect URL on [developers.kite.trade](https://developers.kite.trade/) to:
  `http://130.210.54.238:5000/zerodha/callback`

### 🚨 Edge Case 3: Morning Auto-Login Fails (TOTP or Password Error)
* **Symptom:** Telegram alert says *"Zerodha auto-login FAILED"*.
* **Emergency Manual Fix (Takes 30 seconds):**
  1. Open: `https://kite.zerodha.com/connect/login?api_key=brwt2qz1fo6m5b33&v=3` in your browser.
  2. Log in manually.
  3. When redirected, copy the `request_token=XXXXX` parameter from the address bar.
  4. SSH into the server and run:
     ```bash
     cd /home/ubuntu/openalgo
     venv/bin/python run_with_env.py auto_login_zerodha.py --request-token "<PASTED_TOKEN>"
     ```

### 🚨 Edge Case 4: Bot says `telegram.error.Conflict: terminated by other getUpdates request`
* **Cause:** Multiple processes or another machine is running with the same Telegram Bot token.
* **Fix:**
  ```bash
  sudo systemctl stop serveralgo-setup
  pkill -9 -f setup_agent.py || true
  sudo systemctl start serveralgo-setup
  ```

### 🚨 Edge Case 5: Need to change strategy lot allocations
* **File:** `/home/ubuntu/openalgo/strategies/configs/<strategy_name>.live.yaml`
* **Edit:** Change `quantity_lots: 1` to desired lots.
* **Apply:** Restart the strategy process or run:
  ```bash
  /home/ubuntu/openalgo/run_live.sh
  ```

### 🚨 Edge Case 6: Server Reboot / Power Failure
* `serveralgo-setup.service` is enabled with `systemd` and starts on boot automatically.
* Docker container `openalgo-web` has `restart: unless-stopped` and starts on boot automatically.
* Crontab is persistent and triggers at scheduled times.
* To check after a server reboot:
  ```bash
  docker ps
  sudo systemctl status serveralgo-setup
  ```

---

## 8. Essential Diagnostic Commands Cheat Sheet

| Task | Command |
| :--- | :--- |
| **View Live Telegram Bot Logs** | `journalctl -u serveralgo-setup -f` |
| **View OpenAlgo Container Logs** | `docker logs openalgo-web -f` |
| **View Daily Auto-Login Log** | `cat /home/ubuntu/openalgo/auto_login.log` |
| **View Strategy Execution Logs** | `tail -f /home/ubuntu/openalgo/strategies/logs/*.log` |
| **Check Active Strategy PIDs** | `pgrep -a -f 'run_with_env.py strategies/'` |
| **Restart Docker Container** | `cd /home/ubuntu/openalgo && docker compose restart` |
| **Restart Telegram Setup Bot** | `sudo systemctl restart serveralgo-setup` |
| **Test Broker Connection Manually** | `curl -s http://127.0.0.1:5000/api/v1/positionbook -H "Content-Type: application/json" -d "{\"apikey\":\"$(grep OPENALGO_API_KEY /home/ubuntu/openalgo/.env \| cut -d\' -f2)\"}"` |
