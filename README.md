# vpn-bot

Telegram bot for issuing VPN configs and managing Xray/AWG nodes.

This repo contains the current control-plane version of the bot.

## What it does

The bot is built as a control plane:

- the bot itself can run on any VPS or inside Docker
- VPN nodes are registered separately
- nodes can be bootstrapped from inside the bot
- Xray and AWG are managed per node, not via hardcoded countries
- storage is SQLite-based

## Quick Start

```bash
cp .env.example .env
docker compose up -d --build
```

Then:

1. Open the bot as admin.
2. Open `Админ: SSH ключ` and add the generated public key to the target server.
3. Open `Админ: серверы`, add a server, then run `Probe` and `Bootstrap`.
4. Open `Админ: профили`, create a profile.
5. Test key issuance from a normal Telegram account.

## Docs

- [`README.md`](/home/saharoktyan/projects/vpn-bot-public/README.md) — project overview
- [`INSTALL.md`](/home/saharoktyan/projects/vpn-bot-public/INSTALL.md) — first-time setup and deployment

## Runtime Layout

```bash
/opt/vpn-bot
  ├─ app/
  ├─ data/
  └─ ssh/
```
