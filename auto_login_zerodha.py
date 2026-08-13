"""Auto-login to OpenAlgo + Zerodha (Kite Connect).

Zerodha has no headless auth API: the access token can only be minted from a
`request_token`, and that token is only ever handed out via the browser OAuth
redirect. Kite access tokens die daily (~06:00 IST), so this must run every
trading morning before the strategies start.

Flow
----
  Step 0  OpenAlgo CSRF + platform login          (urllib, cookie jar)
  Step 1  Playwright drives the Kite login form   (user-id, password, TOTP)
  Step 2  Intercept the redirect, scrape `request_token` -- and ABORT the
          navigation so the single-use token is not burned by the browser
  Step 3  GET /zerodha/callback?request_token=... on the authenticated session
  Step 4  Verify via /api/v1/positionbook
  Step 5  Download the Zerodha master contract

If ANY step fails, a Telegram alert is sent with the manual login URL so the
owner can mint a token by hand before 09:10. Exit code is non-zero so cron
surfaces the failure too.

Deliberately mirrors `auto_login.py` (the Kotak one) in structure and logging so
the two are diffable.

    python3 auto_login_zerodha.py
    python3 auto_login_zerodha.py --headed     # watch it, for debugging selectors

Required in .env (server only, never in git):
    HOST_SERVER, OPENALGO_USER, OPENALGO_PASS, OPENALGO_API_KEY,
    BROKER_API_KEY, BROKER_API_SECRET, REDIRECT_URL,
    ZERODHA_USER_ID, ZERODHA_PASSWORD, ZERODHA_TOTP_SECRET,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# Shared Telegram bot with the Kotak book, so every message is tagged.
TAG = "[ZERODHA]"
KITE_LOGIN = "https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_env(path: Path) -> None:
    if not path.exists():
        log(f"ERROR: .env not found at {path}")
        sys.exit(1)
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def telegram(msg: str) -> None:
    """Best-effort alert. Never raises -- a dead bot must not mask the real error."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        log("WARNING: no Telegram credentials; alert not sent")
        return
    try:
        data = urllib.parse.urlencode({
            "chat_id": chat,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15
        )
        log("Telegram alert sent")
    except Exception as e:  # noqa: BLE001 - alerting must never be fatal
        log(f"WARNING: Telegram alert failed: {e}")


def fail(reason: str, api_key: str) -> None:
    """Alert with everything needed to do it by hand, then exit non-zero."""
    log(f"FATAL: {reason}")
    telegram(
        f"<b>{TAG} auto-login FAILED</b>\n\n"
        f"{reason}\n\n"
        f"<b>Manual token needed before 09:10.</b>\n"
        f"1. Open: {KITE_LOGIN.format(api_key=api_key)}\n"
        f"2. Log in, copy <code>request_token</code> from the redirect URL\n"
        f"3. On the server run:\n"
        f"<code>python3 auto_login_zerodha.py --request-token &lt;TOKEN&gt;</code>\n\n"
        f"Kotak book is unaffected."
    )
    sys.exit(1)


# ---------------------------------------------------------------- Playwright

def browser_login(host: str, api_key: str, headed: bool) -> bool:
    """Log into OpenAlgo, then run the Kite OAuth flow in the same browser.

    The redirect lands on /zerodha/callback with a valid OpenAlgo session, so
    OpenAlgo mints and stores the access token itself. This is exactly the
    manual flow, automated.

    An earlier version tried to intercept the redirect and exchange the
    request_token out-of-band. Don't go back to that: the token is single-use,
    so an interception that half-fires burns it, and `page.route` proved
    unreliable on cross-origin navigation redirects.
    """
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("ERROR: playwright not installed (pip install playwright && playwright install chromium)")
        return False

    import pyotp

    oa_user = os.environ["OPENALGO_USER"]
    oa_pass = os.environ["OPENALGO_PASS"]
    user_id = os.environ["ZERODHA_USER_ID"]
    password = os.environ["ZERODHA_PASSWORD"]
    totp_secret = os.environ["ZERODHA_TOTP_SECRET"]

    ok = False
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        page = browser.new_page()
        seen: list[str] = []
        page.on("framenavigated", lambda f: seen.append(f.url))

        try:
            # --- 1. OpenAlgo platform login, in the browser -------------------
            log("Step 1: OpenAlgo platform login (browser)...")
            page.goto(f"{host}/login", timeout=60_000)
            # The OpenAlgo UI is a React SPA -- the form does not exist in the
            # initial HTML, so wait for it to render before touching it.
            page.wait_for_load_state("networkidle", timeout=30_000)
            for u_sel, p_sel in (("input#username", "input#password"),
                                 ("input[name=username]", "input[name=password]"),
                                 ("input[type=text]", "input[type=password]")):
                try:
                    page.wait_for_selector(u_sel, timeout=10_000)
                except PWTimeout:
                    continue
                page.fill(u_sel, oa_user)
                page.fill(p_sel, oa_pass)
                page.click("button[type=submit]")
                break
            else:
                log("ERROR: could not find the OpenAlgo login form")
                browser.close()
                return False
            page.wait_for_timeout(4_000)
            if "/login" in page.url:
                log(f"ERROR: still on the OpenAlgo login page -- bad OPENALGO_USER/PASS? url={page.url}")
                browser.close()
                return False
            log(f"Step 1: OpenAlgo session established ({page.url})")

            # --- 2. Kite OAuth, same browser/session -------------------------
            log("Step 2: Opening Kite login...")
            page.goto(KITE_LOGIN.format(api_key=api_key), timeout=60_000)
            page.fill("input#userid", user_id)
            page.fill("input#password", password)
            page.click("button[type=submit]")
            log("Step 2: Credentials submitted, waiting for 2FA...")

            totp_sel = None
            for sel in ("input#totp", "input#pin", "input[type=number]"):
                try:
                    page.wait_for_selector(sel, timeout=8_000)
                    totp_sel = sel
                    break
                except PWTimeout:
                    continue
            if not totp_sel:
                log("ERROR: could not find the TOTP field -- Kite may have changed its login page")
                browser.close()
                return False

            # Generate as late as possible; the 30s window burns fast.
            page.fill(totp_sel, pyotp.TOTP(totp_secret).now())
            log(f"Step 2: TOTP submitted via {totp_sel}")
            try:
                page.click("button[type=submit]", timeout=5_000)
            except PWTimeout:
                pass  # some versions auto-submit on the 6th digit

            # --- 3. Wait to land back on OpenAlgo ----------------------------
            # NOTE: do not look for "/zerodha/callback" in the navigation trail.
            # `framenavigated` fires with the URL *after* the redirect chain, so
            # the callback never appears as its own event -- a successful login
            # shows up simply as a jump from Kite to /dashboard. Success here is
            # provisional; verify() against the API is the real gate.
            for _ in range(60):
                if host in page.url and "kite.zerodha.com" not in page.url:
                    break
                page.wait_for_timeout(500)
            page.wait_for_timeout(3_000)

            back_at_login = page.url.rstrip("/").endswith("/login")
            if host in page.url and not back_at_login:
                log(f"Step 3: returned to OpenAlgo at {page.url}")
                ok = True
            elif back_at_login:
                log("ERROR: bounced back to the OpenAlgo login page "
                    "-- the session was not carried through the redirect")
            else:
                log(f"ERROR: did not return to OpenAlgo. Final URL: {page.url}")
                trail = " -> ".join(re.sub(r"(request_token=|api_key=)[^&]+", r"\1<REDACTED>", u)
                                    for u in seen[-5:])
                log(f"Navigation trail: {trail}")
        except Exception as e:  # noqa: BLE001
            log(f"ERROR during login: {e}")
        finally:
            browser.close()

    return ok


# ------------------------------------------------------------------ OpenAlgo

def openalgo_session(host: str, user: str, password: str):
    """Log into the OpenAlgo platform; return an opener carrying the session."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj),
        urllib.request.HTTPRedirectHandler(),
    )

    log("Step 0: Fetching CSRF token...")
    resp = opener.open(urllib.request.Request(f"{host}/auth/csrf-token"), timeout=30)
    csrf = json.loads(resp.read().decode())["csrf_token"]
    log(f"CSRF token obtained: {csrf[:20]}...")

    log("Step 0: OpenAlgo platform login...")
    data = urllib.parse.urlencode({"username": user, "password": password}).encode()
    req = urllib.request.Request(
        f"{host}/auth/login", data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": csrf},
    )
    resp = opener.open(req, timeout=30)
    log(f"Platform login OK (HTTP {resp.getcode()})")
    return opener


def exchange_token(opener, host: str, request_token: str) -> bool:
    """Hand the request_token to OpenAlgo, which mints and stores the access token."""
    log("Step 3: Exchanging request_token via /zerodha/callback...")
    url = f"{host}/zerodha/callback?request_token={urllib.parse.quote(request_token)}"
    try:
        resp = opener.open(urllib.request.Request(url), timeout=60)
        body = resp.read().decode()
        log(f"Callback response (HTTP {resp.getcode()})")
        # OpenAlgo renders a page rather than JSON; failures say so in the body.
        if re.search(r"error|failed|invalid", body[:2000], re.I) and "success" not in body[:2000].lower():
            log(f"Callback body suggests failure: {body[:300]}")
            return False
        return True
    except urllib.error.HTTPError as e:
        log(f"Callback FAILED (HTTP {e.code}): {e.read().decode()[:300]}")
        return False


def verify(host: str, api_key: str) -> bool:
    if not api_key:
        log("WARNING: OPENALGO_API_KEY not set; skipping verification")
        return True
    log("Step 4: Verifying broker connection...")
    req = urllib.request.Request(
        f"{host}/api/v1/positionbook",
        data=json.dumps({"apikey": api_key}).encode(),
        method="POST", headers={"Content-Type": "application/json"},
    )
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
        if data.get("status") == "success":
            log("Broker VERIFIED -- API responding")
            return True
        log(f"Broker NOT connected: {data}")
        return False
    except Exception as e:  # noqa: BLE001
        log(f"Broker verification FAILED: {e}")
        return False


def check_master_contract(container: str, min_nfo: int = 1000) -> bool:
    """Verify the master contract is loaded -- do NOT trigger the download.

    OpenAlgo downloads it automatically after a successful broker auth (see
    `master_contract_cache_hook`). Calling `master_contract_download()` through
    `docker exec` runs outside the Flask app context, where `socketio` is None,
    so it blows up in its own error handler with a confusing AttributeError.
    Verify instead.
    """
    log("Step 5: Verifying master contract...")
    code = (
        "import sys; sys.path.insert(0, '/app')\n"
        "from database.symbol import SymToken, db_session\n"
        "from sqlalchemy import func\n"
        "rows = db_session.query(SymToken.exchange, func.count(SymToken.id))"
        ".group_by(SymToken.exchange).all()\n"
        "print(dict(rows))\n"
    )
    try:
        out = subprocess.run(
            ["sudo", "docker", "exec", "-i", container, "/app/.venv/bin/python3", "-c", code],
            capture_output=True, text=True, timeout=180,
        )
        line = (out.stdout or "").strip().splitlines()[-1] if out.stdout.strip() else ""
        if not line.startswith("{"):
            log(f"Could not read symbol counts: {(out.stdout + out.stderr)[-300:]}")
            return False
        counts = eval(line)  # noqa: S307 - our own dict repr from a trusted container
        nfo = counts.get("NFO", 0)
        log(f"  symbols: total={sum(counts.values())} NFO={nfo} BFO={counts.get('BFO', 0)}")
        if nfo < min_nfo:
            log(f"  NFO count {nfo} below minimum {min_nfo} -- master contract looks stale/empty")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log(f"Master contract verification FAILED: {e}")
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--request-token", help="skip Playwright and use a manually captured token")
    ap.add_argument("--headed", action="store_true", help="show the browser (debugging)")
    ap.add_argument("--container", default="openalgo-zerodha")
    ap.add_argument("--retries", type=int, default=2, help="Playwright login attempts")
    args = ap.parse_args()

    load_env(Path(__file__).resolve().parent / ".env")

    host = os.environ.get("HOST_SERVER", "http://127.0.0.1:5001").strip()
    api_key = os.environ.get("BROKER_API_KEY", "").strip()

    required = ["OPENALGO_USER", "OPENALGO_PASS", "BROKER_API_KEY", "BROKER_API_SECRET"]
    if not args.request_token:
        required += ["ZERODHA_USER_ID", "ZERODHA_PASSWORD", "ZERODHA_TOTP_SECRET"]
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if missing:
        fail(f"Missing in .env: {', '.join(missing)}", api_key)

    # --- authenticate --------------------------------------------------------
    if args.request_token:
        # Manual path: owner captured a token by hand from the redirect URL.
        log("Using request_token supplied on the command line")
        try:
            opener = openalgo_session(
                host, os.environ["OPENALGO_USER"], os.environ["OPENALGO_PASS"]
            )
        except Exception as e:  # noqa: BLE001
            fail(f"OpenAlgo platform login failed: {e}", api_key)
        if not exchange_token(opener, host, args.request_token):
            fail("request_token exchange failed (token may be expired or already used).", api_key)
    else:
        # Automatic path: the whole OAuth dance happens inside one browser
        # session, so OpenAlgo stores the access token itself.
        for attempt in range(1, args.retries + 1):
            log(f"Login attempt {attempt}/{args.retries}")
            if browser_login(host, api_key, args.headed):
                break
            if attempt < args.retries:
                log("Retrying in 10s (TOTP window may have rolled)...")
                time.sleep(10)
        else:
            fail("Playwright could not complete the Zerodha login.", api_key)

    if not verify(host, os.environ.get("OPENALGO_API_KEY", "").strip()):
        fail("Broker did not verify after login -- positionbook not responding.", api_key)

    if not check_master_contract(args.container):
        # Symbols cannot resolve without it, so the strategies would fail at entry.
        fail("Master contract missing or stale -- symbols will not resolve.", api_key)

    log("Zerodha auto-login COMPLETE")
    telegram(f"<b>{TAG} auto-login OK</b>\nBroker connected, master contract verified.")


if __name__ == "__main__":
    main()
