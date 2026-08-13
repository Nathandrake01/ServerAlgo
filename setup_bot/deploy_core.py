"""deploy_core — the deployment logic, run LOCALLY on the client's own server.

This is the SSH-free core: the setup-bot agent runs on the same box as OpenAlgo,
so every step is a local subprocess / file write. The steps mirror the validated
deploy.py exactly (nested-config via the engine's locked_params, .env seeded from
.sample.env, crypto-key preservation on re-run, API-key provisioning, cron).

No secrets are hard-coded here; strategy parameters live compiled in the engine.
"""
from __future__ import annotations

import json
import os
import secrets
import string
import subprocess
import tempfile
import time
from pathlib import Path

ENGINE_RELEASE_URL = "https://github.com/Nathandrake01/ServerAlgo/releases/latest/download/engine.tar.gz"
OPENALGO_REPO = "https://github.com/marketcalls/openalgo.git"
OPENALGO_BRANCH = "main"

STRATEGY_NAMES = ("pr_0918", "pr_0946", "gamma", "delta")


# ------------------------------------------------------------------ runner ---
class LocalRunner:
    """Runs commands and writes files on THIS machine (the client's server)."""

    def sh(self, cmd: str, timeout: int = 1200) -> tuple[int, str]:
        p = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    def put(self, text: str, path: str, mode: int | None = None) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
        if mode is not None:
            os.chmod(path, mode)

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def read_env(self, path: str, keys: set[str]) -> dict:
        out = {}
        if not Path(path).exists():
            return out
        for ln in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.partition("=")
                if k.strip() in keys:
                    out[k.strip()] = v.strip().strip("'").strip('"')
        return out


# ----------------------------------------------------------- pure helpers ---
def _scalar(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"


def _emit_yaml(obj, indent=0):
    pad = "  " * indent
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.append(_emit_yaml(v, indent + 1))
            else:
                lines.append(f"{pad}{k}: {_scalar(v)}")
    elif isinstance(obj, list):
        for item in obj:
            lines.append(f"{pad}- {_scalar(item)}")
    return "\n".join(lines)


def generate_config(strategy: str, lots: int, port: str) -> str:
    """Minimal CLIENT config — the engine's locked_params supplies everything else."""
    if strategy not in STRATEGY_NAMES:
        raise ValueError(f"unknown strategy {strategy}")
    cfg = {
        "strategy": {"name": strategy, "mode": "live", "quantity_lots": lots},
        "openalgo": {"host": f"http://127.0.0.1:{port}", "api_key": "auto"},
    }
    return _emit_yaml(cfg) + "\n"


_ENGINE_CLS = {"gamma": ("gamma_engine", "LongGammaRescue"),
               "delta": ("delta_engine", "DeltaShortStrangle")}


def entry_script(strategy: str) -> str:
    if strategy in _ENGINE_CLS:
        mod, cls = _ENGINE_CLS[strategy]
        imp = f"from engine.{mod} import {cls}\n"
        call = f'run("{strategy}.live.yaml", engine_cls={cls})'
    else:
        imp = ""
        call = f'run("{strategy}.live.yaml")'
    return (f'"""{strategy} - auto-generated entry point (do not edit)."""\n'
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "from engine.pr_runner import run\n" + imp +
            "\nif __name__ == \"__main__\":\n    " + call + "\n")


def get_public_ip() -> str:
    """Fetch the server's public IP address for UI dashboard and OAuth callback URLs."""
    import requests
    for url in ("https://api.ipify.org", "https://ifconfig.me", "https://icanhazip.com"):
        try:
            res = requests.get(url, timeout=3)
            if res.ok and res.text.strip():
                return res.text.strip()
        except Exception:
            continue
    return "127.0.0.1"


def env_overrides(broker, creds, port, ws_port, preserve=None, public_ip=None):
    """Managed .env keys. Crypto keys + admin account are PRESERVED across re-runs."""
    preserve = preserve or {}
    alp = string.ascii_lowercase + string.digits
    pub_ip = public_ip or "127.0.0.1"

    def keep(key, gen):
        return preserve[key] if preserve.get(key) else gen()

    admin_user = preserve.get("OPENALGO_USER") or "admin"
    admin_pass = preserve.get("OPENALGO_PASS") or ''.join(secrets.choice(alp) for _ in range(12))
    ov = {
        "BROKER_API_KEY": creds["BROKER_API_KEY"],
        "BROKER_API_SECRET": creds["BROKER_API_SECRET"],
        "REDIRECT_URL": f"http://{pub_ip}:{port}/{broker}/callback",
        "VALID_BROKERS": "zerodha" if broker == "zerodha" else "kotak",
        "APP_KEY": keep("APP_KEY", lambda: secrets.token_hex(32)),
        "API_KEY_PEPPER": keep("API_KEY_PEPPER", lambda: secrets.token_hex(32)),
        "FERNET_SALT": keep("FERNET_SALT", lambda: secrets.token_hex(32)),
        "OPENALGO_USER": admin_user,
        "OPENALGO_PASS": admin_pass,
        "OPENALGO_API_KEY": preserve.get("OPENALGO_API_KEY", ""),
        "FLASK_HOST_IP": "0.0.0.0", "FLASK_PORT": port,
        "FLASK_ENV": "production", "FLASK_DEBUG": "0",
        "WEBSOCKET_PORT": ws_port, "WEBSOCKET_HOST": "127.0.0.1",
        "WEBSOCKET_URL": f"ws://127.0.0.1:{ws_port}",
        "TELEGRAM_BOT_TOKEN": creds.get("TELEGRAM_BOT_TOKEN", ""),
        "TELEGRAM_CHAT_ID": creds.get("TELEGRAM_CHAT_ID", ""),
        "TZ": "Asia/Kolkata", "HOST_SERVER": f"http://127.0.0.1:{port}",
        "NGROK_ALLOW": "FALSE",
    }
    if broker == "kotak":
        ov.update({"KOTAK_MOBILE": creds.get("KOTAK_MOBILE", ""),
                   "KOTAK_MPIN": creds.get("KOTAK_MPIN", ""),
                   "KOTAK_TOTP_SECRET": creds.get("KOTAK_TOTP_SECRET", "")})
    else:
        ov.update({"ZERODHA_USER_ID": creds.get("ZERODHA_USER_ID", ""),
                   "ZERODHA_PASSWORD": creds.get("ZERODHA_PASSWORD", ""),
                   "ZERODHA_TOTP_SECRET": creds.get("ZERODHA_TOTP_SECRET", "")})
    return ov, admin_user, admin_pass



def apply_env_overrides(env_path: str, overrides: dict) -> None:
    """Replace managed keys in place; append any the sample didn't have."""
    seen, out = set(), []
    for ln in Path(env_path).read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in overrides:
                out.append(f"{k}='{overrides[k]}'"); seen.add(k); continue
        out.append(ln)
    for k, v in overrides.items():
        if k not in seen:
            out.append(f"{k}='{v}'")
    Path(env_path).write_text("\n".join(out) + "\n", encoding="utf-8")


_PROVISION_PY = '''
import sys, secrets
sys.path.insert(0, "/app")
user, pw = sys.argv[1], sys.argv[2]
try:
    from app import app
except Exception as e:
    print("PROVISION_ERR import app:", e); sys.exit(2)
with app.app_context():
    from database.user_db import add_user, find_user_by_username
    from database.auth_db import upsert_api_key
    existing = None
    try:
        existing = find_user_by_username()
    except Exception:
        existing = None
    if existing is None:
        existing = add_user(user, user + "@local", pw, is_admin=True)
    uid = getattr(existing, "username", None) or user
    key = secrets.token_hex(32)
    upsert_api_key(uid, key)
    print("APIKEY=" + key)
    print("OAUSER=" + str(uid))
    try:
        totp = existing.get_totp_secret()
        if totp:
            print("OATOTP=" + str(totp))
    except Exception as e:
        print("OATOTP_ERR=" + str(e))
'''


# ----------------------------------------------------------------- deploy ---
def run_deploy(answers: dict, log=print, runner: LocalRunner | None = None) -> dict:
    """Execute the full deployment locally. `answers` keys:
        broker, creds{...}, strategies{name:lots}, user, [port, ws_port]
    `log(msg)` streams progress (the bot forwards these to Telegram).
    Returns {ok, admin_user, admin_pass, url}.
    """
    r = runner or LocalRunner()
    broker = answers["broker"]
    creds = answers["creds"]
    strategies = answers["strategies"]
    import getpass
    user = answers.get("user") or os.environ.get("USER") or getpass.getuser()
    port = answers.get("port", "5000")
    ws_port = answers.get("ws_port", "8765")
    inst = f"/home/{user}/openalgo"
    cname = "openalgo-web"
    venv = f"{inst}/venv"
    vpy = f"{venv}/bin/python"

    # 1. OpenAlgo present
    log("Setting up OpenAlgo...")
    if not r.exists(inst):
        rc, out = r.sh(f"git clone --depth 1 --branch {OPENALGO_BRANCH} {OPENALGO_REPO} {inst}")
        if rc != 0:
            return {"ok": False, "error": f"git clone failed: {out[-500:]}"}
    else:
        log("Existing install found — preserving keys & account.")

    # 2. .env (seed from sample only when fresh; preserve crypto keys on re-run)
    log("Configuring credentials...")
    env_path = f"{inst}/.env"
    preserve_keys = {"APP_KEY", "API_KEY_PEPPER", "FERNET_SALT",
                     "OPENALGO_USER", "OPENALGO_PASS", "OPENALGO_API_KEY"}
    env_exists = r.exists(env_path)
    preserve = r.read_env(env_path, preserve_keys) if env_exists else {}
    account_exists = env_exists and bool(preserve.get("OPENALGO_API_KEY"))
    if not env_exists:
        if not r.exists(f"{inst}/.sample.env"):
            return {"ok": False, "error": ".sample.env missing in OpenAlgo clone"}
        r.sh(f"cp {inst}/.sample.env {env_path}")
    public_ip = get_public_ip()
    overrides, admin_user, admin_pass = env_overrides(broker, creds, port, ws_port, preserve, public_ip=public_ip)
    apply_env_overrides(env_path, overrides)
    r.sh(f"chmod 644 {env_path}")

    # 3. docker-compose (with build: so a fresh box can build the image)
    log("Writing docker-compose...")
    compose = f"""services:
  openalgo:
    image: openalgo:latest
    build:
      context: .
      dockerfile: Dockerfile
    container_name: {cname}
    ports:
      - "0.0.0.0:{port}:{port}"
      - "127.0.0.1:{ws_port}:{ws_port}"
    volumes:
      - openalgo_db:/app/db
      - ./.env:/app/.env:ro
      - ./strategies:/app/strategies
    environment:
      - PORT={port}
      - FLASK_ENV=production
      - TZ=Asia/Kolkata
    shm_size: 256m
    mem_limit: 2g
    restart: unless-stopped
volumes:
  openalgo_db: {{driver: local}}
"""
    r.put(compose, f"{inst}/docker-compose.yaml")

    # 4. build + start
    log("Building & starting OpenAlgo (first build can take several minutes)...")
    r.sh(f"cd {inst} && docker compose up -d", timeout=1800)
    up = False
    for _ in range(24):
        rc, out = r.sh(f"curl -s --max-time 4 -o /dev/null -w '%{{http_code}}' "
                       f"http://127.0.0.1:{port}/auth/check-setup")
        if out.strip().endswith("200"):
            up = True
            break
        time.sleep(5)
    if not up:
        return {"ok": False, "error": f"OpenAlgo did not come up on :{port} — check `docker logs {cname}`"}
    log("OpenAlgo is up.")

    # 5. engine + dirs + configs + entry scripts
    log("Downloading strategy engine & writing configs...")
    r.sh(f"mkdir -p {inst}/strategies/configs {inst}/strategies/logs {inst}/strategies/status "
         f"{inst}/strategies/commands {inst}/strategies/checkpoints")
    rc, out = r.sh(f"curl -fsSL {ENGINE_RELEASE_URL} -o /tmp/engine.tar.gz && "
                   f"tar -xzf /tmp/engine.tar.gz -C {inst}/strategies/ && rm /tmp/engine.tar.gz")
    if rc != 0:
        return {"ok": False, "error": f"engine download failed: {out[-300:]}"}
    for s, lots in strategies.items():
        r.put(generate_config(s, lots, port), f"{inst}/strategies/configs/{s}.live.yaml")
        r.put(entry_script(s), f"{inst}/strategies/{s}.py")

    # 6. venv + deps
    log("Installing strategy runtime...")
    deps = "requests pyyaml pytz" + (" playwright pyotp" if broker == "zerodha" else "")
    r.sh(f"python3 -m venv {venv} && {vpy} -m pip install -q --upgrade pip && "
         f"{vpy} -m pip install -q {deps}", timeout=600)
    if broker == "zerodha":
        log("Installing headless browser for Zerodha login...")
        r.sh(f"{vpy} -m playwright install chromium && "
             f"sudo {vpy} -m playwright install-deps chromium 2>/dev/null || true", timeout=900)

    # 7. run_with_env.py + auto-login script (bundled next to this file)
    r.put(
        '"""Load .env then exec the given script under this venv."""\n'
        "import os, sys\n"
        f'for line in open("{env_path}"):\n'
        "    line = line.strip()\n"
        "    if not line or line.startswith('#') or '=' not in line: continue\n"
        "    k, _, v = line.partition('='); k = k.strip(); v = v.strip().strip(\"'\").strip('\"')\n"
        "    if k and ' ' not in k: os.environ[k] = v\n"
        f'os.chdir("{inst}")\n'
        "os.execvp(sys.executable, [sys.executable] + sys.argv[1:])\n",
        f"{inst}/run_with_env.py")
    login_script = "auto_login_zerodha.py" if broker == "zerodha" else "auto_login.py"
    local_login = Path(__file__).resolve().parent.parent / login_script
    if local_login.is_file():
        r.put(local_login.read_text(encoding="utf-8"), f"{inst}/{login_script}")

    # 8. provision API key (+ login TOTP) unless the account already exists
    log("Provisioning OpenAlgo API key...")
    if account_exists:
        api_key = preserve.get("OPENALGO_API_KEY")
        oa_totp = None
    else:
        r.put(_PROVISION_PY, f"{inst}/strategies/_provision.py")
        rc, out = r.sh(f"docker exec -w /app {cname} python /app/strategies/_provision.py "
                       f"{admin_user!r} {admin_pass!r} 2>&1")
        r.sh(f"rm -f {inst}/strategies/_provision.py")
        api_key = oa_totp = None
        for ln in out.splitlines():
            if ln.startswith("APIKEY="): api_key = ln.split("=", 1)[1].strip()
            elif ln.startswith("OATOTP="): oa_totp = ln.split("=", 1)[1].strip()

    def set_env(k, v):
        if v is None:
            return
        r.sh(f"sed -i '/^{k}=/d' {env_path} && echo \"{k}='{v}'\" >> {env_path}")

    if api_key:
        set_env("OPENALGO_API_KEY", api_key)
        set_env("OPENALGO_USER", admin_user)
        set_env("OPENALGO_PASS", admin_pass)
        set_env("OPENALGO_TOTP_SECRET", oa_totp)

    # 9. run_live.sh + cron
    log("Wiring the daily launcher & cron...")
    launches = "\n".join(
        f'nohup {vpy} run_with_env.py strategies/{s}.py '
        f'--config strategies/configs/{s}.live.yaml '
        f'>> strategies/logs/{s}_$TODAY.log 2>&1 &' for s in strategies)
    r.put(f"""#!/bin/bash
cd {inst}
TODAY=$(date +%Y-%m-%d)
mkdir -p strategies/logs strategies/status strategies/commands strategies/checkpoints
pkill -f "run_with_env.py strategies/" 2>/dev/null; sleep 1
{launches}
""", f"{inst}/run_live.sh", mode=0o755)
    login_cmd = f"{vpy} {login_script}"
    login_min = "28 3" if broker == "zerodha" else "25 3"
    cron = (f"{login_min} * * 1-5 cd {inst} && {login_cmd} >> {inst}/auto_login.log 2>&1\n"
            f"40 3 * * 1-5 {inst}/run_live.sh >> {inst}/run_live.log 2>&1")
    r.sh(f"( crontab -l 2>/dev/null | grep -v '{inst}' ; printf '%s\\n' \"{cron}\" ) | crontab -")

    return {"ok": True, "admin_user": admin_user, "admin_pass": admin_pass,
            "url": f"http://{public_ip}:{port}", "public_ip": public_ip, "account_existed": account_exists}

