"""setup_agent — the client's own Telegram bot that runs the onboarding on THEIR
own server. Reuses deploy_core (no SSH). Requires python-telegram-bot v20+.

Env: SETUP_BOT_TOKEN (their @BotFather token), optional STATE_DIR.
Secrets typed into chat are written to .env on THIS server and the messages are
auto-deleted; nothing is ever sent to the provider.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

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


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(st: dict) -> None:
    STATE_FILE.write_text(json.dumps(st, indent=2))


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


# ------------------------------------------------------------- handlers ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        await update.message.reply_text("This bot is already paired to another account.")
        return ConversationHandler.END
    ctx.user_data.clear()
    ctx.user_data["chat_id"] = update.effective_chat.id
    await update.message.reply_text(
        "Welcome to ServerAlgo setup.\n\n"
        "I'll get your trading strategies running on THIS server, step by step. "
        "Everything you type stays on your own machine — nothing is sent to the provider.\n\n"
        "First: paste your *license key*.", parse_mode="Markdown")
    return LICENSE


async def got_license(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    key = update.message.text.strip()
    if not license_ok(key):
        await update.message.reply_text("That key isn't valid. Check it and paste again.")
        return LICENSE
    # pair this chat
    st = load_state(); st["chat_id"] = update.effective_chat.id; save_state(st)
    ctx.user_data["license"] = key
    await update.message.reply_text(
        "License valid ✅\n\nSelect your broker:",
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
            "Zerodha selected ✅\n\nI'll ask for each detail one at a time.\n\nSend your *Kite API key*.",
            parse_mode="Markdown"
        )
        return Z_APIKEY
    elif choice == "broker_kotak":
        ctx.user_data["broker"] = "kotak"
        await query.edit_message_text(
            "Kotak selected ✅\n\nSend your registered *10-digit mobile number*.",
            parse_mode="Markdown"
        )
        return K_MOBILE
    return BROKER


async def got_broker_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip().lower()
    ctx.user_data["creds"] = {}
    if "zerodha" in choice or choice == "1":
        ctx.user_data["broker"] = "zerodha"
        await update.message.reply_text(
            "Zerodha selected ✅\n\nI'll ask for each detail one at a time.\n\nSend your *Kite API key*.",
            parse_mode="Markdown"
        )
        return Z_APIKEY
    elif "kotak" in choice or choice == "2":
        ctx.user_data["broker"] = "kotak"
        await update.message.reply_text(
            "Kotak selected ✅\n\nSend your registered *10-digit mobile number*.",
            parse_mode="Markdown"
        )
        return K_MOBILE
    else:
        await update.message.reply_text(
            "Please select a valid broker using the buttons below:",
            reply_markup=get_broker_keyboard()
        )
        return BROKER


async def _delete_secret(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Delete the user's message so the secret doesn't linger in the chat."""
    try:
        await ctx.bot.delete_message(update.effective_chat.id, update.message.message_id)
    except Exception:
        pass


# --- Zerodha steps ---
async def z_apikey(update: Update, ctx):
    ctx.user_data["creds"]["BROKER_API_KEY"] = update.message.text.strip()
    await update.message.reply_text("Send your *Kite API secret*.", parse_mode="Markdown")
    return Z_SECRET


async def z_secret(update: Update, ctx):
    ctx.user_data["creds"]["BROKER_API_SECRET"] = update.message.text.strip()
    await _delete_secret(update, ctx)
    await ctx.bot.send_message(ctx.user_data["chat_id"],
                               "Got it (message deleted). Send your *Zerodha user ID*.", parse_mode="Markdown")
    return Z_USERID


async def z_userid(update: Update, ctx):
    ctx.user_data["creds"]["ZERODHA_USER_ID"] = update.message.text.strip()
    await update.message.reply_text("Send your *Zerodha password*.", parse_mode="Markdown")
    return Z_PASSWORD


async def z_password(update: Update, ctx):
    ctx.user_data["creds"]["ZERODHA_PASSWORD"] = update.message.text.strip()
    await _delete_secret(update, ctx)
    await ctx.bot.send_message(
        ctx.user_data["chat_id"],
        "Got it (message deleted).\n\nNow the *Zerodha TOTP secret*: Kite → Profile → "
        "Settings → Password & security → External TOTP. Copy the base32 secret "
        "(letters A–Z, digits 2–7) and send it.", parse_mode="Markdown")
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
        await ctx.bot.send_message(ctx.user_data["chat_id"],
                                   "That isn't a valid base32 TOTP secret. Copy the exact "
                                   "External-TOTP key from Kite and send again.")
        return Z_TOTP
    ctx.user_data["creds"]["ZERODHA_TOTP_SECRET"] = sec
    return await prompt_tg_token(update, ctx)


# --- Kotak steps ---
async def k_mobile(update: Update, ctx):
    ctx.user_data["creds"]["KOTAK_MOBILE"] = update.message.text.strip()
    await update.message.reply_text("Send your *Kotak 6-digit MPIN*.", parse_mode="Markdown")
    return K_MPIN


async def k_mpin(update: Update, ctx):
    ctx.user_data["creds"]["KOTAK_MPIN"] = update.message.text.strip()
    await _delete_secret(update, ctx)
    await ctx.bot.send_message(ctx.user_data["chat_id"],
                               "Got it (message deleted). Send your *Kotak Neo Consumer API Key*.", parse_mode="Markdown")
    return K_APIKEY


async def k_apikey(update: Update, ctx):
    ctx.user_data["creds"]["BROKER_API_KEY"] = update.message.text.strip()
    await update.message.reply_text("Send your *Kotak Neo Consumer API Secret*.", parse_mode="Markdown")
    return K_SECRET


async def k_secret(update: Update, ctx):
    ctx.user_data["creds"]["BROKER_API_SECRET"] = update.message.text.strip()
    await _delete_secret(update, ctx)
    await ctx.bot.send_message(
        ctx.user_data["chat_id"],
        "Got it (message deleted).\n\nNow send your *Kotak TOTP Secret key* (base32).", parse_mode="Markdown")
    return K_TOTP


async def k_totp(update: Update, ctx):
    sec = update.message.text.strip().replace(" ", "").upper()
    await _delete_secret(update, ctx)
    if not _is_base32(sec):
        await ctx.bot.send_message(ctx.user_data["chat_id"],
                                   "That isn't a valid base32 TOTP secret. Check and send again.")
        return K_TOTP
    ctx.user_data["creds"]["KOTAK_TOTP_SECRET"] = sec
    return await prompt_tg_token(update, ctx)


# --- Shared telegram/strategy/deploy steps ---
async def prompt_tg_token(update: Update, ctx):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏩ Reuse Current Bot Token", callback_data="tg_skip")]
    ])
    chat_id = ctx.user_data["chat_id"]
    await ctx.bot.send_message(
        chat_id,
        "Got it (message deleted). This bot's token will be used for trade alerts.\n\n"
        "Send a new bot token from @BotFather, or tap below to reuse this bot token.",
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
        "Which strategies + how many lots? Reply like:\n`pr_0918:1 gamma:1 delta:1`\n"
        "(Available: pr_0918, pr_0946, gamma, delta)", parse_mode="Markdown")
    return STRATS


async def got_strategies(update: Update, ctx):
    strat = {}
    for tok in update.message.text.replace(",", " ").split():
        if ":" in tok:
            name, _, lots = tok.partition(":")
            if name in deploy_core.STRATEGY_NAMES and lots.strip().isdigit():
                strat[name] = int(lots)
    if not strat:
        await update.message.reply_text("Couldn't parse that. Example: `pr_0918:1 gamma:1`", parse_mode="Markdown")
        return STRATS
    ctx.user_data["strategies"] = strat
    summary = ", ".join(f"{k} {v}x" for k, v in strat.items())
    b_name = ctx.user_data.get("broker", "zerodha").capitalize()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 Deploy Strategies", callback_data="confirm_deploy"),
            InlineKeyboardButton("❌ Cancel", callback_data="confirm_cancel"),
        ]
    ])
    await update.message.reply_text(
        f"Ready to deploy:\n*Broker:* {b_name}\n*Strategies:* {summary}\n\nClick *Deploy Strategies* below to start.",
        parse_mode="Markdown",
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
        await update.message.reply_text("Click *Deploy Strategies* below or reply *deploy* to start.",
                                        parse_mode="Markdown", reply_markup=keyboard)
        return CONFIRM
    return await start_deploy_execution(ctx)


async def start_deploy_execution(ctx):
    chat_id = ctx.user_data["chat_id"]
    answers = {"broker": ctx.user_data["broker"], "creds": ctx.user_data["creds"],
               "strategies": ctx.user_data["strategies"], "user": os.environ.get("USER") or __import__("getpass").getuser()}
    await ctx.bot.send_message(chat_id, "Deploying — I'll post progress here. This can take 5–10 min.")
    loop = asyncio.get_running_loop()

    def log(msg):
        asyncio.run_coroutine_threadsafe(ctx.bot.send_message(chat_id, f"• {msg}"), loop)

    result = await asyncio.to_thread(deploy_core.run_deploy, answers, log)
    if result.get("ok"):
        broker = ctx.user_data.get("broker", "zerodha")
        cb_path = f"/{broker}/callback"
        await ctx.bot.send_message(
            chat_id,
            "✅ Deployment complete!\n\n"
            f"Admin login: {result['admin_user']} / {result['admin_pass']}\n"
            "Strategies launch 09:10 IST, Mon–Fri.\n\n"
            f"One-time setup on broker portal: set Redirect URL to http://127.0.0.1:5000{cb_path} "
            "and whitelist this server's IP.\n\n"
            "Each morning, connect your broker in the OpenAlgo dashboard before 09:10.\n"
            "Control commands: /status, /positions, /exit_all, /killall")
        st = load_state(); st["deployed"] = True; save_state(st)
    else:
        await ctx.bot.send_message(chat_id, f"⚠️ Deployment failed: {result.get('error')}\n"
                                            "Fix and reply *deploy* to retry (nothing is re-entered).",
                                   parse_mode="Markdown")
        return CONFIRM
    return ConversationHandler.END


async def cancel(update: Update, ctx):
    await update.message.reply_text("Cancelled. Send /start to begin again.",
                                    reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# --- Ops control handlers ---
async def status(update: Update, ctx):
    if not await guard(update):
        return
    user = os.environ.get("USER") or __import__("getpass").getuser()
    r = deploy_core.LocalRunner()
    _, d_out = r.sh("docker ps --filter name=openalgo-web --format 'Container: {{.Status}}'")
    _, p_out = r.sh("pgrep -a -f 'run_with_env.py strategies/' || echo 'No strategies currently active.'")
    _, l_out = r.sh(f"ls -t /home/{user}/openalgo/strategies/logs/*.log 2>/dev/null | head -3 | xargs tail -n 2 2>/dev/null || echo 'No logs yet.'")
    
    msg = (f"📊 *ServerAlgo System Status*\n\n"
           f"*Docker:* {d_out.strip()}\n\n"
           f"*Active Strategies:*\n`{p_out.strip()}`\n\n"
           f"*Latest Log Tail:*\n`{l_out.strip()}`")
    await update.message.reply_text(msg, parse_mode="Markdown")


async def positions(update: Update, ctx):
    if not await guard(update):
        return
    r = deploy_core.LocalRunner()
    rc, out = r.sh("curl -s http://127.0.0.1:5000/api/v1/positions || echo 'Could not connect to OpenAlgo API.'")
    await update.message.reply_text(f"📈 *OpenAlgo Positions:*\n```json\n{out.strip()[:1000]}\n```", parse_mode="Markdown")


async def exit_all(update: Update, ctx):
    if not await guard(update):
        return
    r = deploy_core.LocalRunner()
    rc, out = r.sh("pkill -f 'run_with_env.py strategies/' 2>&1")
    await update.message.reply_text("🛑 Sent terminate signal to all active strategy runners.", parse_mode="Markdown")


async def killall(update: Update, ctx):
    if not await guard(update):
        return
    r = deploy_core.LocalRunner()
    user = os.environ.get("USER") or __import__("getpass").getuser()
    r.sh("pkill -f 'run_with_env.py strategies/' 2>&1")
    r.sh(f"cd /home/{user}/openalgo && docker compose stop 2>&1")
    await update.message.reply_text("⚠️ Stopped OpenAlgo container and killed all active strategy processes.", parse_mode="Markdown")


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
    app.run_polling()


if __name__ == "__main__":
    main()
