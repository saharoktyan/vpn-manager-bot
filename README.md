# vpn-manager-bot

Telegram bot for provisioning VPN access and managing VPN nodes.

The bot runs as a control plane: it can live on any VPS or in Docker, while VPN nodes are registered and managed separately over SSH.

## Features

- Telegram-based admin UI for managing servers and user profiles
- SSH key onboarding directly from the bot
- Node bootstrap from inside the bot
- Per-server protocol provisioning
- SQLite-based local state
- User key issuance with QR codes and fallback config download
- Russian and English interface support

## Supported Protocols

Current protocol support:

- `VLESS` over `Xray Reality`
  - transports: `tcp`, `xhttp`
- `AmneziaWG`

## Container Images

The project currently uses these runtime images for protocol nodes:

- Xray: `ghcr.io/xtls/xray-core:25.12.8`
- AmneziaWG base image: `amneziavpn/amneziawg-go:latest`

For AWG nodes, the bot builds and deploys its own wrapper image on the target host during bootstrap.

## Quick Start

```bash
cp .env.example .env
docker compose up -d --build
```

Then:

1. Open the bot as admin.
2. Open `Admin -> SSH Key` and add the generated public key to the target server.
3. Open `Admin -> Servers`, add a server, then run `Probe` and `Bootstrap`.
4. Open `Admin -> Profiles`, create a profile.
5. Test key issuance from a normal Telegram account.

## Docs

- [`INSTALL.md`](/home/saharoktyan/projects/vpn-bot-public/INSTALL.md) — first-time setup and deployment

## Runtime Layout

```bash
/opt/vpn-bot
  ├─ app/
  ├─ data/
  └─ ssh/
```
