"""Auto-login to OpenAlgo + Kotak NEO broker.

Step 0: GET  /auth/csrf-token          (session cookie + CSRF token)
Step 1: POST /auth/login               (platform auth)
Step 2: POST /kotak/callback            (broker TOTP auth, CSRF-exempt)
Step 3: Verify broker via positionbook API
Step 4: Trigger master contract download

All credentials read from .env. Cron: Mon-Fri 08:55 IST (03:25 UTC).
"""
import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("\"").strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val


def generate_totp(secret_b32, period=30, digits=6):
    s = secret_b32.upper().replace(" ", "")
    s += "=" * (-len(s) % 8)  # pad to multiple of 8
    key = base64.b32decode(s, casefold=True)
    counter = struct.pack(">Q", int(time.time()) // period)
    mac = hmac.new(key, counter, hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    code = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def trigger_master_contract_download():
    script = (
        'import os, sys\n'
        'from broker.kotak.database.master_contract_db import (\n'
        '    download_csv_kotak_data, delete_symtoken_table, init_db,\n'
        '    process_kotak_nse_csv, process_kotak_nfo_csv, process_kotak_bse_csv,\n'
        '    process_kotak_bfo_csv, copy_from_dataframe,\n'
        ')\n'
        'init_db()\n'
        'output_path = "tmp"\n'
        'downloaded = download_csv_kotak_data(output_path)\n'
        'if not downloaded:\n'
        '    print("ERROR: No CSV files downloaded")\n'
        '    sys.exit(1)\n'
        'delete_symtoken_table()\n'
        'processors = [\n'
        '    ("NSE_CM.csv", process_kotak_nse_csv, "NSE"),\n'
        '    ("NSE_FO.csv", process_kotak_nfo_csv, "NFO"),\n'
        '    ("BSE_CM.csv", process_kotak_bse_csv, "BSE"),\n'
        '    ("BSE_FO.csv", process_kotak_bfo_csv, "BFO"),\n'
        ']\n'
        'total = 0\n'
        'for fname, proc, name in processors:\n'
        '    fp = f"{output_path}/{fname}"\n'
        '    if os.path.exists(fp):\n'
        '        df = proc(output_path)\n'
        '        if not df.empty:\n'
        '            copy_from_dataframe(df)\n'
        '            total += len(df)\n'
        '            print(f"{name}: {len(df)} records")\n'
        'print(f"TOTAL: {total}")\n'
        'if total == 0:\n'
        '    sys.exit(1)\n'
    )
    result = subprocess.run(
        ["docker", "exec", "openalgo-web", "python3", "-c", script],
        capture_output=True, text=True, timeout=120,
    )
    output = result.stdout.strip()
    if result.returncode != 0:
        log(f"Master contract download FAILED: {result.stderr[-300:]}")
        return False
    for line in output.splitlines():
        if not line.startswith("["):
            log(f"  {line}")
    return "TOTAL:" in output and "TOTAL: 0" not in output


def main():
    env_path = Path(__file__).resolve().parent / ".env"
    load_env(env_path)

    host = os.environ.get("HOST_SERVER", "http://127.0.0.1:5000").strip().strip("'\"")
    oa_user = os.environ.get("OPENALGO_USER", "").strip()
    oa_pass = os.environ.get("OPENALGO_PASS", "").strip()
    mobile = os.environ.get("KOTAK_MOBILE", "").strip()
    mpin = os.environ.get("KOTAK_MPIN", "").strip()
    totp_secret = os.environ.get("KOTAK_TOTP_SECRET", "").strip()

    missing = []
    if not oa_user:
        missing.append("OPENALGO_USER")
    if not oa_pass:
        missing.append("OPENALGO_PASS")
    if not mobile:
        missing.append("KOTAK_MOBILE")
    if not mpin:
        missing.append("KOTAK_MPIN")
    if not totp_secret:
        missing.append("KOTAK_TOTP_SECRET")
    if missing:
        log(f"ERROR: Missing in .env: {', '.join(missing)}")
        sys.exit(1)

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPRedirectHandler(),
    )

    # Step 0: Get CSRF token + session cookie
    log("Step 0: Fetching CSRF token...")
    req = urllib.request.Request(f"{host}/auth/csrf-token", method="GET")
    try:
        resp = opener.open(req, timeout=30)
        body = json.loads(resp.read().decode())
        csrf_token = body["csrf_token"]
        log(f"CSRF token obtained: {csrf_token[:20]}...")
    except Exception as e:
        log(f"CSRF fetch FAILED: {e}")
        sys.exit(1)

    # Step 1: OpenAlgo platform login
    log("Step 1: OpenAlgo platform login...")
    data = urllib.parse.urlencode({
        "username": oa_user,
        "password": oa_pass,
    }).encode()
    req = urllib.request.Request(
        f"{host}/auth/login",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-CSRFToken": csrf_token,
        },
    )
    try:
        resp = opener.open(req, timeout=30)
        body = resp.read().decode()
        log(f"Platform login OK (HTTP {resp.getcode()}): {body[:200]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        log(f"Platform login FAILED (HTTP {e.code}): {body}")
        sys.exit(1)

    # Step 2: Kotak broker TOTP login (CSRF-exempt)
    log("Step 2: Kotak broker TOTP login...")
    totp = generate_totp(totp_secret)
    data = urllib.parse.urlencode({
        "mobile": mobile,
        "mpin": mpin,
        "totp": totp,
    }).encode()
    req = urllib.request.Request(
        f"{host}/kotak/callback",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        resp = opener.open(req, timeout=30)
        body = resp.read().decode()
        status = resp.getcode()
        log(f"Broker login response (HTTP {status}): {body[:300]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        log(f"Broker login FAILED (HTTP {e.code}): {body}")
        sys.exit(1)

    # Step 3: Verify broker is actually connected via API
    log("Step 3: Verifying broker connection...")
    api_key = os.environ.get("OPENALGO_API_KEY", "").strip()
    if api_key:
        verify_body = json.dumps({"apikey": api_key}).encode()
        vreq = urllib.request.Request(
            f"{host}/api/v1/positionbook",
            data=verify_body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            vresp = urllib.request.urlopen(vreq, timeout=15)
            vdata = json.loads(vresp.read().decode())
            if vdata.get("status") == "success":
                log("Broker VERIFIED — API responding")
            else:
                log(f"WARNING: Broker may not be connected: {vdata}")
        except Exception as ve:
            log(f"WARNING: Broker verification FAILED: {ve}")

    # Step 4: Download master contract
    log("Step 4: Downloading master contract...")
    if trigger_master_contract_download():
        log("Master contract downloaded successfully")
    else:
        log("WARNING: Master contract download failed — orders may not work")

    log("All done — OpenAlgo + Kotak broker authenticated.")


if __name__ == "__main__":
    main()
