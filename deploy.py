#!/usr/bin/env python3
"""
Trading Strategy Deployer — One-command setup for your server.
=================================================================

What this does:
    Pulls OpenAlgo from GitHub (official repo)
    Downloads compiled strategy engine (YOUR repo — .so binaries, no source)
    Sets up broker (Kotak or Zerodha), Telegram, cron
    Client picks strategies + multiplier — never sees strategy logic

Your GitHub repo only contains:
    deploy.py          ← this script
    engine/            ← compiled .so files (built by build.py)
    README.md

Usage:
    python3 deploy.py
"""

import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ===== CONFIG — update these on each engine release =====
ENGINE_RELEASE_URL = "https://github.com/Nathandrake01/ServerAlgo/releases/latest/download/engine.tar.gz"
OPENALGO_REPO = "https://github.com/marketcalls/openalgo.git"
OPENALGO_BRANCH = "main"
# License file (you maintain this — add/remove clients here)
LICENSE_URL = "https://raw.githubusercontent.com/Nathandrake01/ServerAlgo/main/clients.json"
# Your Telegram for receiving daily PnL reports from clients
OWNER_TELEGRAM_BOT_TOKEN = ""   # Your bot token for receiving reports
OWNER_TELEGRAM_CHAT_ID = ""     # Your chat ID for receiving reports
# ==========================================================

CYAN = "\033[96m"; GREEN = "\033[92m"; YELLOW = "\033[93m"
RED = "\033[91m"; BOLD = "\033[1m"; RESET = "\033[0m"

SSH_OPTS = []      # list of ssh/scp args, e.g. ["-o", "...", "-i", "C:\\path\\key"]
SSH_TARGET = ""


def ask(msg, default="", password=False):
    prompt = f"{CYAN}?{RESET} {msg}"
    if default: prompt += f" [{default}]"
    prompt += ": "
    if password:
        import getpass
        return getpass.getpass(prompt).strip() or default
    return input(prompt).strip() or default

def confirm(msg): return ask(f"{msg} (y/n)", "y").lower() in ("y", "yes")

def discover_chat_id(bot_token):
    """Ask Telegram's getUpdates for the chat that just messaged the bot.
    Returns the chat id as a string, or None."""
    import urllib.request, json
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    try:
        data = json.loads(urllib.request.urlopen(url, timeout=10).read())
    except Exception:
        return None
    if not data.get("ok"):
        return None
    for upd in reversed(data.get("result", [])):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            return str(chat["id"])
    return None

def valid_base32(secret):
    """True if secret is a usable base32 TOTP seed (A-Z, 2-7)."""
    import base64
    s = (secret or "").strip().replace(" ", "").upper()
    if not s:
        return False
    s += "=" * (-len(s) % 8)
    try:
        base64.b32decode(s, casefold=True)
        return True
    except Exception:
        return False

def ssh(cmd, capture=True, check=False):
    # No shell=True: build an argv list. This is the ONE remote command passed as
    # a single argument to ssh (ssh forwards it to the remote shell, so &&, |, etc.
    # still work). Avoids WinError 5 under Microsoft Store Python and all local
    # quoting differences between cmd.exe / PowerShell / bash.
    argv = ["ssh", *SSH_OPTS, SSH_TARGET, cmd]
    try:
        if capture:
            r = subprocess.run(argv, capture_output=True, text=True)
            if check and r.returncode != 0:
                print(f"{RED}SSH error:{RESET} {r.stderr}")
            return r
        else:
            subprocess.run(argv)
    except FileNotFoundError:
        print(f"{RED}'ssh' not found. Install the OpenSSH client and retry.{RESET}")
        sys.exit(1)

def scp_up(local, remote):
    subprocess.run(["scp", *SSH_OPTS, local, f"{SSH_TARGET}:{remote}"],
                   check=False, capture_output=True)

def remote_exists(path):
    return "yes" in ssh(f"test -e {path} && echo yes || echo no").stdout

def remote_file(path):
    return "yes" in ssh(f"test -f {path} && echo yes || echo no").stdout

def remote_env_values(env_path, keys):
    """Read the given keys from an existing remote .env. Returns {key: value} for
    those present. Used to preserve crypto keys / admin creds across re-runs."""
    r = ssh(f"cat {env_path} 2>/dev/null")
    out = {}
    for ln in (r.stdout or "").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, _, v = ln.partition("=")
            k = k.strip()
            if k in keys:
                out[k] = v.strip().strip("'").strip('"')
    return out

# Import shared pure helpers from setup_bot/deploy_core.py
sys.path.insert(0, str(Path(__file__).resolve().parent / "setup_bot"))
from deploy_core import (
    env_overrides, _emit_yaml, _scalar, generate_config,
    STRATEGY_NAMES, _PROVISION_PY, entry_script as _entry_script_core
)


def entry_script(strategy, config_filename=None):
    return _entry_script_core(strategy)

def step(n, msg): print(f"\n{BOLD}{GREEN}[{n}]{RESET} {msg}")
def ok(msg=""): print(f"  {GREEN}✓{RESET} {msg}")
def warn(msg): print(f"  {YELLOW}⚠{RESET} {msg}")


def provision_api_key(cname, admin_user, admin_pass, inst_dir):
    """Create admin (if needed) + API key inside the container. Returns a dict with
    keys api_key / oa_user / oa_totp (values may be None)."""
    remote_dir = "/app/strategies"          # mounted from {inst_dir}/strategies
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(_PROVISION_PY); tmp = f.name
    try:
        scp_up(tmp, f"{inst_dir}/strategies/_provision_key.py")
    finally:
        os.unlink(tmp)
    # -w /app so `import app` resolves; the container's `python` is its own venv.
    r = ssh(f"docker exec -w /app {cname} python {remote_dir}/_provision_key.py "
            f"{shlex.quote(admin_user)} {shlex.quote(admin_pass)} 2>&1")
    ssh(f"rm -f {inst_dir}/strategies/_provision_key.py")
    out = {"api_key": None, "oa_user": None, "oa_totp": None}
    for line in (r.stdout or "").splitlines():
        if line.startswith("APIKEY="):
            out["api_key"] = line.split("=", 1)[1].strip()
        elif line.startswith("OAUSER="):
            out["oa_user"] = line.split("=", 1)[1].strip()
        elif line.startswith("OATOTP="):
            out["oa_totp"] = line.split("=", 1)[1].strip()
    return out


# ===========================================================================
# MAIN
# ===========================================================================
def main():

    global SSH_OPTS, SSH_TARGET

    print(f"\n{BOLD}{'='*55}{RESET}")
    print(f"{BOLD}  Trading Strategy Deployer{RESET}")
    print(f"{BOLD}{'='*55}{RESET}\n")


    # ── 0. License check ──
    step(0, 'License verification')
    try:
        import urllib.request, json
        req = urllib.request.Request(LICENSE_URL, headers={'User-Agent': 'deploy.py'})
        clients = json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception as e:
        print(f'{RED}Cannot reach license server. Try again later.{RESET}')
        sys.exit(1)

    key = ask('License key (from your provider)')
    import hashlib
    key_hash = hashlib.sha256(key.encode()).hexdigest()[:16]
    # Handle both list [hash,...] and dict {hash: server} formats
    if isinstance(clients, list):
        clients = {h: None for h in clients}
    if key_hash not in clients:
        print(f'{RED}Invalid license key.{RESET}')
        sys.exit(1)
    existing_server = clients[key_hash]  # single-use check deferred to step 1 (needs host)
    print(f'{GREEN}  License valid.{RESET}')

    # ── 1. Server connection ──
    step(1, "Server connection")
    host = ask("Server IP")
    # Tolerate common paste mistakes: "ubuntu@1.2.3.4", "http://1.2.3.4", ":5000/…"
    host = host.strip().strip("/")
    if "://" in host:
        host = host.split("://", 1)[1]
    if "@" in host:
        host = host.rsplit("@", 1)[1]     # drop any user@ prefix
    host = host.split("/")[0].split(":")[0].strip()
    user = ask("SSH user", "ubuntu").strip().split("@")[0] or "ubuntu"
    key_path = ask("Path to SSH private key (blank = use default ~/.ssh keys)")
    SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]
    if key_path:
        key_path = os.path.expanduser(key_path.strip().strip('"').strip("'"))
        if not os.path.isfile(key_path):
            print(f"{RED}Key file not found: {key_path}{RESET}")
            sys.exit(1)
        SSH_OPTS += ["-i", key_path]
    SSH_TARGET = f"{user}@{host}"
    print(f"  Connecting to {SSH_TARGET}")
    # Check SSH is available
    import shutil as _shutil
    if not _shutil.which("ssh"):
        print(f"{RED}SSH not found. Run this from a terminal with SSH installed (Git Bash / Mac / Linux).{RESET}")
        sys.exit(1)
    # Single-use enforcement (host is known now)
    if existing_server and existing_server != host:
        print(f"{RED}This license is already deployed on {existing_server}.{RESET}")
        print(f"{RED}Each license key can only be used on one server.{RESET}")
        sys.exit(1)
    r = ssh("echo OK")
    if "OK" not in r.stdout:
        print(f"{RED}Cannot connect to {SSH_TARGET}. Check IP and that your SSH key is set up.{RESET}")
        sys.exit(1)
    ok("Connected")

    # ── 2. Server check ──
    step(2, "Server check")
    r = ssh("uname -m && . /etc/os-release 2>/dev/null && echo $ID $VERSION_ID && "
            "python3 -c 'import sys;print(\"%d.%d\"%sys.version_info[:2])' && "
            "free -h | grep Mem && df -h / | tail -1 && "
            "which docker >/dev/null && echo DOCKER_OK || echo NO_DOCKER")
    lines = r.stdout.strip().split("\n")
    arch, os_ver, pyver, mem, disk = lines[0], lines[1], lines[2], lines[3], lines[4]
    has_docker = "NO_DOCKER" not in r.stdout
    print(f"  Arch: {arch} | OS: {os_ver} | Python: {pyver}")
    print(f"  {mem} | {disk}")
    # The engine ships as aarch64 CPython-3.10 binaries — both are hard requirements.
    if arch != "aarch64":
        print(f"{RED}This engine requires an ARM64 (aarch64) server. Yours is '{arch}'.{RESET}")
        print(f"{RED}Use an ARM64 Ubuntu 22.04 box (e.g. Oracle Ampere A1).{RESET}")
        sys.exit(1)
    if pyver != "3.10":
        print(f"{YELLOW}  Warning: server Python is {pyver}; the engine needs 3.10 "
              f"(Ubuntu 22.04). Strategies may fail to import on {pyver}.{RESET}")
    ok()

    # ── 3. Docker ──
    step(3, "Docker")
    if not has_docker:
        print("  Installing Docker (~2 min)...")
        ssh(f"curl -fsSL https://get.docker.com | sudo bash && sudo usermod -aG docker {user}", capture=False)
        ok("Docker installed")
    else:
        ok("Already installed")

    # ── 4. Broker ──
    step(4, "Broker")
    print("  [1] Kotak Neo\n  [2] Zerodha")
    broker = "kotak" if ask("Choose", "1") == "1" else "zerodha"

    # ── 5. Instance (single broker per server — always the standard instance) ──
    port, ws_port = "5000", "8765"
    inst_dir = f"/home/{user}/openalgo"
    cname = "openalgo-web"

    # ── 6. Credentials ──
    step(6, f"{broker.upper()} credentials")
    print(f"{YELLOW}These are stored ONLY on your server.{RESET}\n")
    creds = {}
    if broker == "zerodha":
        print("Kite Connect app: https://developers.kite.trade (CONNECT tier)")
        print(f"{YELLOW}IMPORTANT: set the app's Redirect URL to  http://127.0.0.1:{port}/zerodha/callback")
        print(f"and whitelist this server IP ({host}) on the Kite dashboard.{RESET}\n")
        creds["BROKER_API_KEY"] = ask("Kite API key")
        creds["BROKER_API_SECRET"] = ask("Kite API secret", password=True)
        creds["ZERODHA_USER_ID"] = ask("Zerodha user ID")
        creds["ZERODHA_PASSWORD"] = ask("Zerodha password", password=True)
        print(f"\n{YELLOW}Zerodha TOTP secret: Kite → Profile → Settings → Password & security")
        print(f"→ External TOTP. Copy the base32 secret (letters A-Z and digits 2-7).{RESET}")
        while True:
            tsec = ask("Zerodha TOTP secret", password=True).replace(" ", "").upper()
            if valid_base32(tsec):
                break
            print(f"{RED}That doesn't look like a valid base32 TOTP secret. "
                  f"Copy the exact 'External TOTP' key from Kite (A-Z, 2-7).{RESET}")
        creds["ZERODHA_TOTP_SECRET"] = tsec
    else:
        creds["BROKER_API_KEY"] = ask("Kotak API key (e.g., ABC12)")
        creds["BROKER_API_SECRET"] = ask("Kotak API secret", password=True)
        creds["KOTAK_MOBILE"] = ask("Kotak mobile")
        creds["KOTAK_MPIN"] = ask("Kotak MPIN", password=True)
        creds["KOTAK_TOTP_SECRET"] = ask("Kotak TOTP secret", password=True)

    print("\nTelegram bot (for trade/error alerts):")
    creds["TELEGRAM_BOT_TOKEN"] = ask("Bot token (from @BotFather)", password=True)
    # Auto-discover the chat id so the user never has to hunt for it.
    print("  Open Telegram, find your bot, and send it any message (e.g. 'hi').")
    chat_id = None
    for _ in range(3):
        ask("  Press Enter after you've messaged the bot")
        chat_id = discover_chat_id(creds["TELEGRAM_BOT_TOKEN"])
        if chat_id:
            print(f"{GREEN}  Found your chat id: {chat_id}{RESET}")
            break
        print(f"{YELLOW}  Couldn't see a message yet — send one to the bot and retry.{RESET}")
    creds["TELEGRAM_CHAT_ID"] = chat_id or ask("  Enter Telegram chat id manually")

    # ── 7. Strategies ──
    step(7, "Strategies")
    strategies = {}
    for key, label in [
        ("pr_0918", "PR918 (09:18 entry)"),
        ("pr_0946", "PR946 (09:46 entry)"),
        ("gamma", "Gamma (long insurance, Mon-Thu)"),
        ("delta", "Delta (short strangle, Mon-Thu)"),
    ]:
        if confirm(f"Enable {label}?"):
            strategies[key] = int(ask("  Lot multiplier", "1"))
    if not strategies:
        print(f"{RED}No strategies selected.{RESET}"); sys.exit(1)

    # ── 8. Summary ──
    print(f"\n{BOLD}{'='*55}{RESET}")
    print(f"  Server:  {SSH_TARGET}")
    print(f"  Broker:  {broker.upper()} on :{port}")
    print(f"  Strategies:")
    for s, m in strategies.items(): print(f"    - {s}: {m}x")
    print(f"{BOLD}{'='*55}{RESET}")
    if not confirm("Deploy now?"):
        print("Aborted."); sys.exit(0)

    # ═══════════ DEPLOY ═══════════
    print(f"\n{GREEN}Deploying to {SSH_TARGET}...{RESET}\n")

    # --- 8a. Set up OpenAlgo ---
    step("8a", "Installing OpenAlgo")
    # Reuse an existing install automatically (preserves .env + configs on a re-run,
    # which is the upgrade path). Only clone when the directory is absent.
    if remote_exists(inst_dir):
        ok("Using existing installation (credentials & configs preserved)")
    else:
        ssh(f"git clone --depth 1 --branch {OPENALGO_BRANCH} {OPENALGO_REPO} {inst_dir}", capture=False)
        ok("Cloned from GitHub")

    # --- 8b. Generate & upload .env ---
    step("8b", "Configuring broker")
    # CRITICAL re-run safety: if a .env already exists, read its encryption keys +
    # admin account and PRESERVE them. Regenerating APP_KEY/FERNET_SALT/API_KEY_PEPPER
    # would orphan everything encrypted in the DB (TOTP, broker token) and silently
    # break login. Only seed a fresh .env from .sample.env on a first install.
    env_exists = remote_file(f"{inst_dir}/.env")
    preserve_keys = {"APP_KEY", "API_KEY_PEPPER", "FERNET_SALT",
                     "OPENALGO_USER", "OPENALGO_PASS", "OPENALGO_API_KEY"}
    preserve = remote_env_values(f"{inst_dir}/.env", preserve_keys) if env_exists else {}
    account_exists = env_exists and bool(preserve.get("OPENALGO_API_KEY"))
    if env_exists:
        ok("Existing .env found — preserving encryption keys & admin account")
    else:
        r = ssh(f"test -f {inst_dir}/.sample.env && echo yes || echo no")
        if "yes" not in r.stdout:
            print(f"{RED}.sample.env not found in the OpenAlgo clone — cannot build .env.{RESET}")
            sys.exit(1)
        ssh(f"cp {inst_dir}/.sample.env {inst_dir}/.env")
    overrides, admin_user, admin_pass = env_overrides(broker, creds, port, ws_port, preserve)
    import json as _json
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        _json.dump(overrides, f); ov_tmp = f.name
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(_ENV_PATCH_PY); patch_tmp = f.name
    scp_up(ov_tmp, f"{inst_dir}/.env_overrides.json")
    scp_up(patch_tmp, f"{inst_dir}/_env_patch.py")
    os.unlink(ov_tmp); os.unlink(patch_tmp)
    # 644 (not 600): the .env is bind-mounted into the container, which runs as a
    # different UID — 600 makes it unreadable inside and the app boots with an
    # empty config. Single-tenant server, so world-readable on the host is fine.
    ssh(f"cd {inst_dir} && python3 _env_patch.py .env .env_overrides.json && "
        f"rm -f _env_patch.py .env_overrides.json && chmod 644 .env", capture=False)
    ok(f"Admin: {admin_user} / {admin_pass}")

    # --- 8c. Generate docker-compose ---
    step("8c", "Docker compose")
    compose = f"""services:
  openalgo:
    image: openalgo:latest
    build:
      context: .
      dockerfile: Dockerfile
    container_name: {cname}
    ports:
      - "127.0.0.1:${{FLASK_PORT:-{port}}}:{port}"
      - "127.0.0.1:${{WEBSOCKET_PORT:-{ws_port}}}:{ws_port}"
    volumes:
      - openalgo_db:/app/db
      - openalgo_log:/app/log
      - openalgo_strategies:/app/strategies
      - openalgo_keys:/app/keys
      - openalgo_tmp:/app/tmp
      - ./.env:/app/.env:ro
      - ./strategies:/app/strategies
    environment:
      - PORT={port}
      - FLASK_ENV=production
      - TZ=Asia/Kolkata
      - OPENBLAS_NUM_THREADS=2
      - SHM_SIZE=256m
    shm_size: 256m
    mem_limit: 2g
    cpus: 0.75
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:{port}/auth/check-setup"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    restart: unless-stopped
volumes:
  openalgo_db: {{driver: local}}
  openalgo_log: {{driver: local}}
  openalgo_strategies: {{driver: local}}
  openalgo_keys: {{driver: local}}
  openalgo_tmp: {{driver: local}}
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(compose)
        compose_tmp = f.name
    scp_up(compose_tmp, f"{inst_dir}/docker-compose.yaml")
    os.unlink(compose_tmp)
    ok()

    # --- 8d. Download engine ---
    step("8d", "Downloading strategy engine")
    ssh(f"mkdir -p {inst_dir}/strategies/engine")
    ssh(f"curl -fsSL {ENGINE_RELEASE_URL} -o /tmp/engine.tar.gz && tar -xzf /tmp/engine.tar.gz -C {inst_dir}/strategies/ && rm /tmp/engine.tar.gz", capture=False)
    ok()

    # --- 8e. Generate strategy configs + entry scripts ---
    step("8e", "Generating strategy configs")
    ssh(f"mkdir -p {inst_dir}/strategies/configs {inst_dir}/strategies/logs "
        f"{inst_dir}/strategies/status {inst_dir}/strategies/commands "
        f"{inst_dir}/strategies/checkpoints")
    for sname, mult in strategies.items():
        yaml = generate_config(sname, mult, port)
        entry = entry_script(sname, f"{sname}.live.yaml")   # bare filename; run() resolves vs configs/
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml); yaml_tmp = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(entry); py_tmp = f.name
        scp_up(yaml_tmp, f"{inst_dir}/strategies/configs/{sname}.live.yaml")
        scp_up(py_tmp, f"{inst_dir}/strategies/{sname}.py")
        os.unlink(yaml_tmp); os.unlink(py_tmp)
        ok(f"{sname}: {mult}x")

    # --- 8f. Start container (build image if the tag doesn't exist yet) ---
    step("8f", "Starting OpenAlgo")
    ssh(f"cd {inst_dir} && docker compose up -d 2>&1", capture=False)
    time.sleep(5)
    r = ssh(f"docker ps --filter name={cname} --format '{{{{.Status}}}}'")
    if "Up" not in r.stdout:
        warn("Container not running — building image first (~5-10 min)...")
        ssh(f"cd {inst_dir} && docker compose build && docker compose up -d", capture=False)
        time.sleep(30)
    # Wait for the web app to answer (up to ~2 min)
    for _ in range(24):
        r = ssh(f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}/auth/check-setup")
        if r.stdout.strip() == "200":
            ok(f"OpenAlgo running on :{port}"); break
        time.sleep(5)
    else:
        warn(f"OpenAlgo not answering on :{port} yet — check `docker logs {cname}`")

    # --- 8g. Host Python venv + engine dependencies ---
    step("8g", "Installing strategy runtime (venv + deps)")
    venv = f"{inst_dir}/venv"
    vpy = f"{venv}/bin/python"
    deps = "requests pyyaml pytz"
    if broker == "zerodha":
        deps += " playwright pyotp"        # Zerodha auto-login drives a headless browser
    ssh(f"python3 -m venv {venv} && {vpy} -m pip install -q --upgrade pip && "
        f"{vpy} -m pip install -q {deps}", capture=False)
    if broker == "zerodha":
        warn("Installing Playwright Chromium (Zerodha login) — may take a few minutes...")
        ssh(f"{vpy} -m playwright install chromium && "
            f"sudo {vpy} -m playwright install-deps chromium 2>/dev/null || true", capture=False)
    ok("venv ready")

    # --- 8h. Upload launcher + auto-login script ---
    step("8h", "Uploading launcher & auto-login")
    # run_with_env.py: load .env into the environment, then exec the target with THIS
    # interpreter (the venv), cwd = inst_dir. Parameterised per instance.
    run_with_env = f'''"""Load .env then exec the given script under this venv (cwd={inst_dir})."""
import os, sys
for line in open("{inst_dir}/.env"):
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    k = k.strip(); v = v.strip().strip("'").strip('"')
    if k and " " not in k:
        os.environ[k] = v
os.chdir("{inst_dir}")
os.execvp(sys.executable, [sys.executable] + sys.argv[1:])
'''
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(run_with_env); rwe_tmp = f.name
    scp_up(rwe_tmp, f"{inst_dir}/run_with_env.py")
    os.unlink(rwe_tmp)
    # auto-login script — bundled next to this deploy.py, uploaded as-is (reads .env).
    login_script = "auto_login_zerodha.py" if broker == "zerodha" else "auto_login.py"
    local_login = str(Path(__file__).resolve().parent / login_script)
    if os.path.isfile(local_login):
        scp_up(local_login, f"{inst_dir}/{login_script}")
        ok(f"Uploaded {login_script} + run_with_env.py")
    else:
        warn(f"{login_script} not found next to deploy.py — auto-login NOT installed")

    # --- 8i. Provision OpenAlgo API key (+ login TOTP) into .env ---
    step("8i", "Provisioning OpenAlgo API key")
    if account_exists:
        # Re-run: the account + API key already exist and are still encrypted with
        # the preserved keys. Re-provisioning would rotate the TOTP/key needlessly.
        ok("Existing OpenAlgo account & API key preserved (no re-provision)")
        api_key = preserve.get("OPENALGO_API_KEY")
        prov = {"api_key": api_key, "oa_user": preserve.get("OPENALGO_USER"), "oa_totp": None}
    else:
        prov = provision_api_key(cname, admin_user, admin_pass, inst_dir)
    api_key = prov["api_key"]
    if not account_exists and not api_key:
        warn("Could not auto-provision an API key.")
        print(f"  Open the UI (run open_ui after this finishes), complete first-time")
        print(f"  setup, generate an API key (Settings → API Key), and paste it here.")
        api_key = ask("OPENALGO_API_KEY (paste, or leave blank to wire up later)")

    def _set_env(key, val):
        if val is None:
            return
        ssh(f"sed -i \"/^{key}=/d\" {inst_dir}/.env && "
            f"echo \"{key}='{val}'\" >> {inst_dir}/.env")

    if api_key:
        _set_env("OPENALGO_API_KEY", api_key)
        # Keep OPENALGO_USER/PASS/TOTP aligned with the account we just created so
        # the morning auto-login can log into OpenAlgo without a human.
        _set_env("OPENALGO_USER", prov["oa_user"] or admin_user)
        _set_env("OPENALGO_PASS", admin_pass)
        _set_env("OPENALGO_TOTP_SECRET", prov["oa_totp"])
        ok("API key + login TOTP written to .env")
    else:
        warn("No API key set — strategies will fail to authenticate until you add "
             f"OPENALGO_API_KEY to {inst_dir}/.env")

    # --- 8j. run_live.sh launcher ---
    step("8j", "Writing run_live.sh")
    launches = "\n".join(
        f'nohup {vpy} run_with_env.py strategies/{s}.py '
        f'--config strategies/configs/{s}.live.yaml '
        f'>> strategies/logs/{s}_$TODAY.log 2>&1 &\necho "  {s} PID=$!"'
        for s in strategies
    )
    run_live = f'''#!/bin/bash
# Auto-generated launcher. Cron starts this after auto-login.
cd {inst_dir}
TODAY=$(date +%Y-%m-%d)
mkdir -p strategies/logs strategies/status strategies/commands strategies/checkpoints
pkill -f "run_with_env.py strategies/" 2>/dev/null; sleep 1
echo "[$(date)] launching strategies for $TODAY"
{launches}
echo "[$(date)] launched: {' '.join(strategies)}"
'''
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(run_live); rl_tmp = f.name
    scp_up(rl_tmp, f"{inst_dir}/run_live.sh")
    os.unlink(rl_tmp)
    ssh(f"chmod +x {inst_dir}/run_live.sh")
    ok()

    # --- 8k. Cron (times in UTC; IST = UTC+5:30) ---
    step("8k", "Setting up daily cron")
    login_cmd = (f"{vpy} auto_login_zerodha.py" if broker == "zerodha"
                 else f"{vpy} auto_login.py")
    login_min = "28 3" if broker == "zerodha" else "25 3"   # 08:58 / 08:55 IST
    cron_entries = [
        f"{login_min} * * 1-5 cd {inst_dir} && {login_cmd} >> {inst_dir}/auto_login.log 2>&1",
        f"40 3 * * 1-5 {inst_dir}/run_live.sh >> {inst_dir}/run_live.log 2>&1",   # 09:10 IST
    ]
    cron_block = "\n".join(cron_entries)
    # Replace any prior block for THIS inst_dir, then re-add (idempotent).
    ssh(f"( crontab -l 2>/dev/null | grep -v '{inst_dir}' ; printf '%s\\n' \"{cron_block}\" ) | crontab -",
        capture=False)
    ok("Cron set: auto-login ~08:55 IST, strategies 09:10 IST (Mon-Fri)")

    # --- 8l. One-click UI launchers (SSH tunnel; UI stays loopback-only) ---
    step("8l", "Creating UI launcher (open_ui)")
    here = Path(__file__).resolve().parent
    ident = f'-i "{key_path}" ' if key_path else ""
    ident_sh = f"-i '{key_path}' " if key_path else ""
    url = f"http://127.0.0.1:{port}"
    bat = (
        "@echo off\r\n"
        f"echo Opening a secure tunnel to your trading server ({host})...\r\n"
        f"start \"\" {url}\r\n"
        f"echo Keep THIS window open while you use the dashboard. Close it to disconnect.\r\n"
        f"ssh {ident}-L {port}:127.0.0.1:{port} -N {user}@{host}\r\n"
    )
    command = (
        "#!/bin/bash\n"
        f"echo 'Opening a secure tunnel to your trading server ({host})...'\n"
        f"( sleep 2; open {url} ) &\n"
        f"echo 'Keep this window open while you use the dashboard. Close it to disconnect.'\n"
        f"ssh {ident_sh}-L {port}:127.0.0.1:{port} -N {user}@{host}\n"
    )
    try:
        (here / "open_ui.bat").write_text(bat, encoding="utf-8")
        cmd_path = here / "open_ui.command"
        cmd_path.write_text(command, encoding="utf-8")
        try:
            os.chmod(cmd_path, 0o755)
        except Exception:
            pass
        ok("open_ui.bat (Windows) and open_ui.command (Mac) created next to deploy.py")
    except Exception as e:
        warn(f"Could not write launcher ({e}); use the manual tunnel command below.")

    # Single-use license marker (best effort reminder for the operator).
    print(f"\n{YELLOW}  Operator note: mark this key used in clients.json -> {host}{RESET}")

    # ═══════════ COMPLETE ═══════════
    print(f"\n{BOLD}{GREEN}{'='*55}{RESET}")
    print(f"{BOLD}{GREEN}  Deployment Complete!{RESET}")
    print(f"{BOLD}{GREEN}{'='*55}{RESET}\n")
    print(f"  To open the dashboard:")
    print(f"    Windows: double-click  open_ui.bat")
    print(f"    Mac:     double-click  open_ui.command")
    print(f"    (or run this and browse to {url}:)")
    print(f"      ssh {ident_sh}-L {port}:127.0.0.1:{port} -N {user}@{host}\n")
    print(f"  Dashboard:    {url}  (via the tunnel above)")
    print(f"  Admin login:  {admin_user} / {admin_pass}")
    print(f"  Strategies launch: 09:10 IST, Mon-Fri (first entry 09:18)")
    print(f"  Logs:         {inst_dir}/strategies/logs/\n")
    if broker == "zerodha":
        print(f"{YELLOW}  ONE-TIME on Kite: set Redirect URL to {url}/zerodha/callback")
        print(f"  and whitelist this server IP ({host}) in the Kite developer console.{RESET}")
    print(f"{YELLOW}  Each morning: open the dashboard and connect your broker before 09:10")
    print(f"  (auto-login is best-effort; confirm 'connected' in the UI).{RESET}\n")


if __name__ == "__main__":
    main()
