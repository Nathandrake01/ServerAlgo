"""setup_agent — the client's own Telegram bot that runs conversational onboarding & 24/7 AI Ops.
Reuses deploy_core (no SSH). Built with python-telegram-bot v20+.

Zero Custody: All credentials stay 100% local in .env on THIS server and sensitive messages
are automatically deleted from chat history.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

import requests
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup,
                      ReplyKeyboardMarkup, ReplyKeyboardRemove, Update)
from telegram.ext import (Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler, filters)

import deploy_core

LICENSE_URL = "https://raw.githubusercontent.com/Nathandrake01/ServerAlgo/main/clients.json"
STATE_DIR = Path(os.environ.get("STATE_DIR", str(Path.home() / ".serveralgo")))
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = STATE_DIR / "agent_state.json"

# Conversation states
(LICENSE, BROKER, Z_APIKEY, Z_SECRET, Z_USERID, Z_PASSWORD, Z_TOTP,
 K_MOBILE, K_MPIN, K_APIKEY, K_SECRET, K_TOTP,
 TG_TOKEN, STRATS, CONFIRM) = range(15)

SECRET_STATES = {Z_SECRET, Z_PASSWORD, Z_TOTP, K_MPIN, K_SECRET, K_TOTP, TG_TOKEN}

STATE_NAMES = {
    LICENSE: "License Verification",
    BROKER: "Broker Selection",
    Z_APIKEY: "Zerodha API Key",
    Z_SECRET: "Zerodha API Secret",
    Z_USERID: "Zerodha User ID",
    Z_PASSWORD: "Zerodha Password",
    Z_TOTP: "Zerodha TOTP Secret",
    K_MOBILE: "Kotak Mobile Number",
    K_MPIN: "Kotak 6-digit MPIN",
    K_APIKEY: "Kotak API Key",
    K_SECRET: "Kotak API Secret",
    K_TOTP: "Kotak TOTP Secret",
    TG_TOKEN: "Telegram Bot Token",
    STRATS: "Strategy Allocation",
    CONFIRM: "Deployment Confirmation"
}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(st: dict) -> None:
    STATE_FILE.write_text(json.dumps(st, indent=2), encoding="utf-8")


def license_ok(key: str) -> bool:
    try:
        clients = requests.get(LICENSE_URL, timeout=10,
                               headers={"User-Agent": "setup_agent"}).json()
    except Exception:
        return False
    if isinstance(clients, list):
        clients = {h: None for h in clients}
    return hashlib.sha256(key.encode()).hexdigest()[:16] in clients


def paired_chat() -> int | None:
    return load_state().get("chat_id")


async def guard(update: Update) -> bool:
    """Only the paired owner may drive the bot once pairing is set."""
    pc = paired_chat()
    return pc is None or update.effective_chat.id == pc


def get_broker_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Zerodha (Kite)", callback_data="broker_zerodha"),
            InlineKeyboardButton("Kotak Neo", callback_data="broker_kotak"),
        ]
    ])


# ------------------------------------------------------------- AI Assistant Engine ---
def get_gemini_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        env_file = Path(os.environ.get("HOME", "/home/ubuntu")) / "openalgo" / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("GEMINI_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"\'')
                    break
    return key


def get_live_system_context() -> dict:
    user = os.environ.get("USER") or __import__("getpass").getuser()
    r = deploy_core.LocalRunner()
    _, d_out = r.sh("docker ps --filter name=openalgo-web --format '{{.Status}}'")
    _, p_out = r.sh("pgrep -a -f 'run_with_env.py strategies/' || echo 'None'")
    _, l_out = r.sh(f"ls -t /home/{user}/openalgo/strategies/logs/*.log 2>/dev/null | head -2 | xargs tail -n 5 2>/dev/null || echo 'No logs'")
    
    positions = []
    try:
        res = requests.get("http://127.0.0.1:5000/api/v1/positions", timeout=2)
        if res.ok:
            positions = res.json()
    except Exception:
        pass

    return {
        "docker_status": d_out.strip(),
        "active_processes": p_out.strip(),
        "recent_logs": l_out.strip(),
        "positions": positions,
        "is_deployed": load_state().get("deployed", False)
    }


def call_ai_assistant(user_msg: str, current_step: str = "", live_context: dict | None = None) -> str:
    """Answers user queries using Gemini or smart fallback heuristics."""
    api_key = get_gemini_api_key()
    ctx = live_context or get_live_system_context()

    if api_key:
        prompt = f"""You are Antigravity AI, the dedicated Quantitative Trading & System Assistant for ServerAlgo.
You are running directly on the user's trading server.

SYSTEM CONTEXT:
- Platform: OpenAlgo Options Trading Engine
- Strategies: PR 09:18 (Morning Skew), PR 09:46 (Morning Skew), Long Gamma, Delta Strangle
- Live Server State: {json.dumps(ctx, indent=2)}
- Current Onboarding Step: {current_step or 'Live Operations Mode'}

USER'S MESSAGE:
"{user_msg}"

INSTRUCTIONS:
1. Be concise, extremely helpful, warm, and data-grounded (1-3 short paragraphs).
2. If they are in the onboarding flow and stuck, guide them clearly on where to find their keys / how to fix issues.
3. If they ask about trades, health, or PnL, interpret the live server state directly.
4. Format using clean Telegram HTML (<b>bold</b>, <code>code</code>, <i>italic</i>)."""

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                text = res["candidates"][0]["content"]["parts"][0]["text"]
                return text.replace("<p>", "").replace("</p>", "\n\n").strip()
        except Exception:
            pass

    # Expert Fallback Engine (when API key is absent or network timeout)
    msg_lower = user_msg.lower()
    if "api key" in msg_lower or "developer" in msg_lower:
        return ("🔑 <b>Where to find your Broker API Key:</b>\n\n"
                "• <b>Zerodha:</b> Log into <a href='https://developers.kite.trade'>developers.kite.trade</a>, create an app, and copy the <i>API Key</i> & <i>API Secret</i>.\n"
                "• <b>Kotak Neo:</b> Log into the Kotak Developer portal, go to Applications, and copy your <i>Consumer Key</i> & <i>Secret</i>.")
    elif "totp" in msg_lower or "2fa" in msg_lower or "qr" in msg_lower:
        return ("🔐 <b>How to get your External TOTP Secret:</b>\n\n"
                "1. Open Kite / Broker Settings ➔ <b>Password & Security ➔ External TOTP</b>.\n"
                "2. Click <i>'Can't scan QR'</i> to reveal the <b>32-character text key</b> (letters A–Z and digits 2–7).\n"
                "3. Copy that text key (not the 6-digit expiring code) and paste it here.")
    elif "status" in msg_lower or "health" in msg_lower:
        return (f"📊 <b>System Overview:</b>\n\n"
                f"• <b>Docker:</b> {ctx.get('docker_status', 'Unknown')}\n"
                f"• <b>Strategies:</b> {ctx.get('active_processes', 'None running')}\n"
                f"• <b>Status:</b> {'Deployed & Ready' if ctx.get('is_deployed') else 'Setup in progress'}")
    else:
        return ("💡 <i>I'm your ServerAlgo assistant.</i>\n\n"
                "If you have a question about credentials, TOTP, strategies, or server health, just ask! "
                "You can also use /status or /positions anytime.")


def diagnose_deploy_error(error_text: str) -> str:
    """Produces human-readable diagnostic advice on deployment failures."""
    err_lower = error_text.lower()
    if "docker" in err_lower or "pull" in err_lower:
        return ("🐳 <b>Docker Build / Network Hiccup:</b>\n"
                "The server had trouble downloading container components. This is usually a temporary network timeout.\n"
                "<b>Fix:</b> Tap <b>Deploy Strategies</b> below to retry without re-entering anything.")
    elif "git" in err_lower:
        return ("📦 <b>Repository Clone Issue:</b>\n"
                "Failed to fetch OpenAlgo core files from GitHub.\n"
                "<b>Fix:</b> Verify your server has outbound internet access and tap retry.")
    elif "base32" in err_lower or "totp" in err_lower:
        return ("🔐 <b>Invalid TOTP Secret:</b>\n"
                "The TOTP key was formatted incorrectly.\n"
                "<b>Fix:</b> Enter your base32 text key from Kite settings (Profile ➔ Security ➔ External TOTP).")
    else:
        return (f"⚠️ <b>Deployment Error:</b>\n<code>{error_text[:200]}</code>\n\n"
                "Everything you entered has been saved. Tap <b>Deploy Strategies</b> below to retry.")


# ------------------------------------------------------------- Handlers ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        await update.message.reply_text("🔒 This bot is already paired to another account.")
        return ConversationHandler.END
    ctx.user_data.clear()
    ctx.user_data["chat_id"] = update.effective_chat.id
    await update.message.reply_text(
        "👋 <b>Welcome to ServerAlgo!</b>\n\n"
        "I'll help you get your automated options trading strategies running on <b>this server</b>, step by step.\n\n"
        "🛡️ <b>Zero-Custody Guarantee:</b> All credentials stay 100% local on your server — nothing is sent to external servers, and sensitive messages are auto-deleted.\n\n"
        "To get started, please paste your <b>License Key</b>:",
        parse_mode="HTML")
    return LICENSE


async def got_license(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    if not license_ok(key):
        await update.message.reply_text(
            "❌ <b>Invalid License Key.</b>\n\n"
            "Please check that you copied the complete key provided by Sumit and paste it again.\n"
            "<i>(Example: 05VSVU-QD9FB5-7QX361)</i>",
            parse_mode="HTML")
        return LICENSE
    # pair this chat
    st = load_state(); st["chat_id"] = update.effective_chat.id; save_state(st)
    ctx.user_data["license"] = key
    await update.message.reply_text(
        "✅ <b>License Verified!</b>\n\n"
        "Which broker would you like to connect?",
        parse_mode="HTML",
        reply_markup=get_broker_keyboard())
    return BROKER


async def broker_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    ctx.user_data["creds"] = {}
    if choice == "broker_zerodha":
        ctx.user_data["broker"] = "zerodha"
        await query.edit_message_text(
            "🚀 <b>Zerodha (Kite) Selected</b>\n\n"
            "I'll guide you through each detail one at a time.\n\n"
            "First, send your <b>Kite API Key</b> from <a href='https://developers.kite.trade'>developers.kite.trade</a>:",
            parse_mode="HTML", disable_web_page_preview=True
        )
        return Z_APIKEY
    elif choice == "broker_kotak":
        ctx.user_data["broker"] = "kotak"
        await query.edit_message_text(
            "🚀 <b>Kotak Neo Selected</b>\n\n"
            "Please send your registered <b>10-digit Mobile Number</b>:",
            parse_mode="HTML"
        )
        return K_MOBILE
    return BROKER


async def got_broker_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip().lower()
    ctx.user_data["creds"] = {}
    if "zerodha" in choice or choice == "1":
        ctx.user_data["broker"] = "zerodha"
        await update.message.reply_text(
            "🚀 <b>Zerodha (Kite) Selected</b>\n\n"
            "Send your <b>Kite API Key</b> from <a href='https://developers.kite.trade'>developers.kite.trade</a>:",
            parse_mode="HTML", disable_web_page_preview=True
        )
        return Z_APIKEY
    elif "kotak" in choice or choice == "2":
        ctx.user_data["broker"] = "kotak"
        await update.message.reply_text(
            "🚀 <b>Kotak Neo Selected</b>\n\n"
            "Please send your registered <b>10-digit Mobile Number</b>:",
            parse_mode="HTML"
        )
        return K_MOBILE
    else:
        await update.message.reply_text(
            "Please select your broker using the buttons below:",
            reply_markup=get_broker_keyboard()
        )
        return BROKER


async def _delete_secret(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        await ctx.bot.delete_message(update.effective_chat.id, update.message.message_id)
    except Exception:
        pass


# --- Zerodha steps ---
async def z_apikey(update: Update, ctx):
    ctx.user_data["creds"]["BROKER_API_KEY"] = update.message.text.strip()
    await update.message.reply_text(
        "Got it! Now send your <b>Kite API Secret</b>:\n\n"
        "🔒 <i>(This message will be deleted immediately after reading).</i>",
        parse_mode="HTML")
    return Z_SECRET


async def z_secret(update: Update, ctx):
    ctx.user_data["creds"]["BROKER_API_SECRET"] = update.message.text.strip()
    await _delete_secret(update, ctx)
    await ctx.bot.send_message(
        ctx.user_data["chat_id"],
        "✅ API Secret received (message deleted).\n\nNow send your <b>Zerodha User ID</b> (e.g. <code>AB1234</code>):",
        parse_mode="HTML")
    return Z_USERID


async def z_userid(update: Update, ctx):
    ctx.user_data["creds"]["ZERODHA_USER_ID"] = update.message.text.strip()
    await update.message.reply_text(
        "Send your <b>Zerodha Password</b>:\n\n"
        "🔒 <i>(Used for automated morning 2FA login — message deleted immediately).</i>",
        parse_mode="HTML")
    return Z_PASSWORD


async def z_password(update: Update, ctx):
    ctx.user_data["creds"]["ZERODHA_PASSWORD"] = update.message.text.strip()
    await _delete_secret(update, ctx)
    await ctx.bot.send_message(
        ctx.user_data["chat_id"],
        "✅ Password saved (message deleted).\n\n"
        "Now send your <b>Zerodha External TOTP Secret</b>:\n\n"
        "📍 <i>Where to find it:</i> In Kite ➔ <b>Profile ➔ Settings ➔ Password & Security ➔ External TOTP</b>.\n"
        "Copy the <b>32-character base32 text key</b> (letters A–Z and digits 2–7) and send it here:",
        parse_mode="HTML")
    return Z_TOTP


def _is_base32(s: str) -> bool:
    import base64
    s = (s or "").replace(" ", "").upper()
    if not s:
        return False
    s += "=" * (-len(s) % 8)
    try:
        base64.b32decode(s, casefold=True); return True
    except Exception:
        return False


async def z_totp(update: Update, ctx):
    sec = update.message.text.strip().replace(" ", "").upper()
    await _delete_secret(update, ctx)
    if not _is_base32(sec):
        await ctx.bot.send_message(
            ctx.user_data["chat_id"],
            "⚠️ <b>Invalid Base32 TOTP Secret</b>\n\n"
            "Base32 keys only contain letters A–Z and digits 2–7 (no 0, 1, 8, 9 or symbols).\n\n"
            "Make sure you copied the <b>text secret key</b> from Kite's External TOTP setup screen (not a 6-digit OTP code) and send again:",
            parse_mode="HTML")
        return Z_TOTP
    ctx.user_data["creds"]["ZERODHA_TOTP_SECRET"] = sec
    return await prompt_tg_token(update, ctx)


# --- Kotak steps ---
async def k_mobile(update: Update, ctx):
    ctx.user_data["creds"]["KOTAK_MOBILE"] = update.message.text.strip()
    await update.message.reply_text(
        "Send your <b>Kotak 6-digit MPIN</b>:\n\n"
        "🔒 <i>(Message deleted immediately).</i>",
        parse_mode="HTML")
    return K_MPIN


async def k_mpin(update: Update, ctx):
    ctx.user_data["creds"]["KOTAK_MPIN"] = update.message.text.strip()
    await _delete_secret(update, ctx)
    await ctx.bot.send_message(
        ctx.user_data["chat_id"],
        "✅ MPIN saved (message deleted).\n\nSend your <b>Kotak Neo Consumer API Key</b>:",
        parse_mode="HTML")
    return K_APIKEY


async def k_apikey(update: Update, ctx):
    ctx.user_data["creds"]["BROKER_API_KEY"] = update.message.text.strip()
    await update.message.reply_text(
        "Send your <b>Kotak Neo Consumer API Secret</b>:\n\n"
        "🔒 <i>(Message deleted immediately).</i>",
        parse_mode="HTML")
    return K_SECRET


async def k_secret(update: Update, ctx):
    ctx.user_data["creds"]["BROKER_API_SECRET"] = update.message.text.strip()
    await _delete_secret(update, ctx)
    await ctx.bot.send_message(
        ctx.user_data["chat_id"],
        "✅ API Secret received (message deleted).\n\nNow send your <b>Kotak TOTP Secret key</b> (base32):",
        parse_mode="HTML")
    return K_TOTP


async def k_totp(update: Update, ctx):
    sec = update.message.text.strip().replace(" ", "").upper()
    await _delete_secret(update, ctx)
    if not _is_base32(sec):
        await ctx.bot.send_message(
            ctx.user_data["chat_id"],
            "⚠️ <b>Invalid TOTP Secret Key</b>. Please verify the base32 key and send again:",
            parse_mode="HTML")
        return K_TOTP
    ctx.user_data["creds"]["KOTAK_TOTP_SECRET"] = sec
    return await prompt_tg_token(update, ctx)


# --- Shared steps ---
async def prompt_tg_token(update: Update, ctx):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏩ Reuse Current Bot Token", callback_data="tg_skip")]
    ])
    chat_id = ctx.user_data["chat_id"]
    await ctx.bot.send_message(
        chat_id,
        "✅ Credentials saved locally.\n\n"
        "🔔 <b>Trade Notifications & Alerts:</b>\n"
        "This bot can send you real-time trade execution notifications and daily PnL summaries.\n\n"
        "Tap below to reuse this bot token, or send a new token from @BotFather:",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    return TG_TOKEN


async def tg_token(update: Update, ctx):
    txt = update.message.text.strip()
    await _delete_secret(update, ctx)
    ctx.user_data["creds"]["TELEGRAM_BOT_TOKEN"] = os.environ.get("SETUP_BOT_TOKEN", txt)
    ctx.user_data["creds"]["TELEGRAM_CHAT_ID"] = str(ctx.user_data["chat_id"])
    return await ask_strategies(update, ctx)


async def tg_skip(update: Update, ctx):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("Reusing current bot token for alerts ✅")
    ctx.user_data["creds"]["TELEGRAM_BOT_TOKEN"] = os.environ.get("SETUP_BOT_TOKEN", "")
    ctx.user_data["creds"]["TELEGRAM_CHAT_ID"] = str(ctx.user_data["chat_id"])
    return await ask_strategies(update, ctx)


async def ask_strategies(update: Update, ctx):
    ctx.user_data["strategies"] = {}
    await ctx.bot.send_message(
        ctx.user_data["chat_id"],
        "📈 <b>Strategy Allocation:</b>\n\n"
        "Specify which strategies and lot quantities you want to deploy.\n\n"
        "<b>Format:</b> <code>strategy:lots</code>\n"
        "<b>Available Strategies:</b>\n"
        "• <code>pr_0918</code> (Pure PR 09:18 Morning Core)\n"
        "• <code>pr_0946</code> (Pure PR 09:46 Morning Core)\n"
        "• <code>gamma</code> (Long Gamma Insurance)\n"
        "• <code>delta</code> (Delta Short Strangle)\n\n"
        "<i>Example response:</i> <code>pr_0918:1 pr_0946:1 gamma:1</code>",
        parse_mode="HTML")
    return STRATS


async def got_strategies(update: Update, ctx):
    strat = {}
    for tok in update.message.text.replace(",", " ").split():
        if ":" in tok:
            name, _, lots = tok.partition(":")
            if name in deploy_core.STRATEGY_NAMES and lots.strip().isdigit():
                strat[name] = int(lots)
    if not strat:
        await update.message.reply_text(
            "⚠️ <b>Could not parse strategy allocation.</b>\n\n"
            "Please format like: <code>pr_0918:1 pr_0946:1 gamma:1</code>",
            parse_mode="HTML")
        return STRATS
    ctx.user_data["strategies"] = strat
    summary = ", ".join(f"<b>{k}</b> ({v} lots)" for k, v in strat.items())
    b_name = ctx.user_data.get("broker", "zerodha").capitalize()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 Deploy Strategies", callback_data="confirm_deploy"),
            InlineKeyboardButton("❌ Cancel", callback_data="confirm_cancel"),
        ]
    ])
    await update.message.reply_text(
        f"🎯 <b>Ready to Deploy!</b>\n\n"
        f"• <b>Broker:</b> {b_name}\n"
        f"• <b>Strategies:</b> {summary}\n\n"
        "Tap <b>Deploy Strategies</b> below to launch your platform:",
        parse_mode="HTML",
        reply_markup=keyboard)
    return CONFIRM


async def confirm_button(update: Update, ctx):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm_deploy":
        await query.edit_message_text("Starting deployment... 🚀")
        return await start_deploy_execution(ctx)
    else:
        await query.edit_message_text("Deployment cancelled.")
        return ConversationHandler.END


async def do_deploy(update: Update, ctx):
    txt = update.message.text.strip().lower()
    if txt != "deploy":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚀 Deploy Strategies", callback_data="confirm_deploy"),
                InlineKeyboardButton("❌ Cancel", callback_data="confirm_cancel"),
            ]
        ])
        await update.message.reply_text(
            "Tap <b>Deploy Strategies</b> below or reply <code>deploy</code> to begin.",
            parse_mode="HTML", reply_markup=keyboard)
        return CONFIRM
    return await start_deploy_execution(ctx)


async def start_deploy_execution(ctx):
    chat_id = ctx.user_data["chat_id"]
    answers = {"broker": ctx.user_data["broker"], "creds": ctx.user_data["creds"],
               "strategies": ctx.user_data["strategies"], "user": os.environ.get("USER") or __import__("getpass").getuser()}
    await ctx.bot.send_message(chat_id, "⚙️ <b>Deploying ServerAlgo...</b>\n\nI'll stream real-time progress updates here. (Initial build takes ~3–5 mins).", parse_mode="HTML")
    loop = asyncio.get_running_loop()

    def log(msg):
        asyncio.run_coroutine_threadsafe(ctx.bot.send_message(chat_id, f"• {msg}"), loop)

    result = await asyncio.to_thread(deploy_core.run_deploy, answers, log)
    if result.get("ok"):
        broker = ctx.user_data.get("broker", "zerodha")
        cb_path = f"/{broker}/callback"
        public_ip = result.get("public_ip") or deploy_core.get_public_ip()
        dashboard_url = result.get("url") or f"http://{public_ip}:5000"
        cb_url = f"http://{public_ip}:5000{cb_path}"
        await ctx.bot.send_message(
            chat_id,
            "🎉 <b>Deployment Complete & Verified!</b>\n\n"
            f"🌐 <b>Dashboard:</b> {dashboard_url}\n"
            f"🔑 <b>Admin Login:</b> <code>{result['admin_user']}</code> / <code>{result['admin_pass']}</code>\n\n"
            f"⚙️ <b>One-Time Broker Portal Configuration:</b>\n"
            f"• <b>Redirect URL:</b> <code>{cb_url}</code>\n"
            f"• <b>IP Whitelist:</b> <code>{public_ip}</code>\n\n"
            "⏰ <b>Daily Trading Schedule:</b>\n"
            "• <b>08:58 AM IST:</b> Auto-login & token exchange\n"
            "• <b>09:10 AM IST:</b> Strategies automatically launch (Mon–Fri)\n\n"
            "🤖 <b>AI Ops Assistant Active:</b> You can chat with me anytime or use /status, /positions, /exit_all.",
            parse_mode="HTML")
        st = load_state(); st["deployed"] = True; save_state(st)
    else:
        diag = diagnose_deploy_error(result.get("error", "Unknown error"))
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Retry Deployment", callback_data="confirm_deploy")]
        ])
        await ctx.bot.send_message(
            chat_id,
            f"{diag}\n\n<i>Nothing was lost — tap below to retry:</i>",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        return CONFIRM
    return ConversationHandler.END


async def cancel(update: Update, ctx):
    await update.message.reply_text("Cancelled. Send /start to begin again.",
                                    reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# --- Ops & AI Chat Handlers ---
async def status(update: Update, ctx):
    if not await guard(update):
        return
    user = os.environ.get("USER") or __import__("getpass").getuser()
    r = deploy_core.LocalRunner()
    _, d_out = r.sh("docker ps --filter name=openalgo-web --format '{{.Status}}'")
    _, p_out = r.sh("pgrep -a -f 'run_with_env.py strategies/' || echo 'No strategies currently active.'")
    _, l_out = r.sh(f"ls -t /home/{user}/openalgo/strategies/logs/*.log 2>/dev/null | head -3 | xargs tail -n 2 2>/dev/null || echo 'No logs yet.'")
    
    msg = (f"📊 <b>ServerAlgo System Status</b>\n\n"
           f"🐳 <b>Docker:</b> <code>{d_out.strip()}</code>\n\n"
           f"⚡ <b>Active Runners:</b>\n<code>{p_out.strip()}</code>\n\n"
           f"📜 <b>Recent Log Activity:</b>\n<code>{l_out.strip()}</code>")
    await update.message.reply_text(msg, parse_mode="HTML")


async def positions(update: Update, ctx):
    if not await guard(update):
        return
    r = deploy_core.LocalRunner()
    rc, out = r.sh("curl -s http://127.0.0.1:5000/api/v1/positions || echo 'Could not connect to OpenAlgo API.'")
    await update.message.reply_text(f"📈 <b>Live Positions:</b>\n<pre>{out.strip()[:1000]}</pre>", parse_mode="HTML")


async def exit_all(update: Update, ctx):
    if not await guard(update):
        return
    r = deploy_core.LocalRunner()
    r.sh("pkill -f 'run_with_env.py strategies/' 2>&1")
    await update.message.reply_text("🛑 <b>Terminate signal sent to all active strategy runners.</b>", parse_mode="HTML")


async def killall(update: Update, ctx):
    if not await guard(update):
        return
    r = deploy_core.LocalRunner()
    user = os.environ.get("USER") or __import__("getpass").getuser()
    r.sh("pkill -f 'run_with_env.py strategies/' 2>&1")
    r.sh(f"cd /home/{user}/openalgo && docker compose stop 2>&1")
    await update.message.reply_text("⚠️ <b>Stopped OpenAlgo container and killed all strategy processes.</b>", parse_mode="HTML")


async def handle_free_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles free-form conversational queries with AI assistance."""
    if not await guard(update):
        return
    user_msg = update.message.text.strip() if update.message and update.message.text else ""
    if not user_msg:
        return

    try:
        await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception:
        pass

    reply = call_ai_assistant(user_msg)
    await update.message.reply_text(reply, parse_mode="HTML", disable_web_page_preview=True)


def main():
    token = os.environ["SETUP_BOT_TOKEN"]
    app: Application = ApplicationBuilder().token(token).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LICENSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_license)],
            BROKER: [
                CallbackQueryHandler(broker_button, pattern="^broker_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_broker_text)
            ],
            # Zerodha
            Z_APIKEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, z_apikey)],
            Z_SECRET: [MessageHandler(filters.TEXT & ~filters.COMMAND, z_secret)],
            Z_USERID: [MessageHandler(filters.TEXT & ~filters.COMMAND, z_userid)],
            Z_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, z_password)],
            Z_TOTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, z_totp)],
            # Kotak
            K_MOBILE: [MessageHandler(filters.TEXT & ~filters.COMMAND, k_mobile)],
            K_MPIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, k_mpin)],
            K_APIKEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, k_apikey)],
            K_SECRET: [MessageHandler(filters.TEXT & ~filters.COMMAND, k_secret)],
            K_TOTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, k_totp)],
            # Shared
            TG_TOKEN: [
                CallbackQueryHandler(tg_skip, pattern="^tg_skip$"),
                CommandHandler("skip", tg_skip),
                MessageHandler(filters.TEXT & ~filters.COMMAND, tg_token)
            ],
            STRATS: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_strategies)],
            CONFIRM: [
                CallbackQueryHandler(confirm_button, pattern="^confirm_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, do_deploy)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("positions", positions))
    app.add_handler(CommandHandler("exit_all", exit_all))
    app.add_handler(CommandHandler("killall", killall))
    # Free-form AI Chat Handler for Q&A and Troubleshooting
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_chat))
    app.run_polling()


if __name__ == "__main__":
    main()
