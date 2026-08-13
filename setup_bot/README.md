# ServerAlgo Setup Bot (Phase 1)

A conversational alternative to `deploy.py`. Instead of running a program on your
laptop, you run a one-line bootstrap on **your own server**, then finish setup by
**chatting with your own Telegram bot**. Secrets stay on your machine — the
provider never sees them.

## Pieces
- **`../bootstrap.sh`** — run once on the server (or as cloud-init user-data).
  Installs Docker + this agent as a `systemd` service bound to your bot token.
- **`setup_agent.py`** — the Telegram bot: license check → broker creds
  (step-by-step, secret messages auto-deleted) → strategies → deploy → ops.
- **`deploy_core.py`** — the SSH-free deploy logic (mirrors the validated
  `deploy.py`; runs locally on the server). Strategy parameters are **not** here —
  they live compiled in the engine (`locked_params`).

## Client flow
1. Create a bot with **@BotFather**, copy the token.
2. On the server:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/Nathandrake01/ServerAlgo/main/bootstrap.sh \
     | SETUP_BOT_TOKEN=<token> bash
   ```
3. Open the bot, send **/start**, follow the chat.

## Status / testing
- Pure logic in `deploy_core.py` (config/env/preserve) is unit-verified.
- The bot imports and wires cleanly (python-telegram-bot v20+).
- **Not yet run end-to-end on a live server** — Phase 1 needs a real bot token +
  ARM64/Ubuntu-22.04 box to validate the full chat→deploy path.

## Not yet built (later phases)
- Kotak flow (Zerodha only for now).
- Post-deploy TOTP/whitelist guidance in-chat, EOD PnL, richer ops commands.
- LLM "help me when stuck".
- Pairing hardening + cloud-init path.
