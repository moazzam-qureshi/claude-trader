# VPS deploy runbook

Direct `docker compose` deploy. ~30 min from blank VPS to live trader.

## Sizing

- **Minimum:** 2 vCPU, 4GB RAM, 40GB SSD
- **Comfortable:** 4 vCPU, 8GB RAM, 80GB SSD
- Hetzner CX22 (~$5/mo) works for the minimum.

## On the VPS

### 1. Base packages

```bash
apt update && apt install -y docker.io docker-compose-plugin git tmux mosh
systemctl enable --now docker
ufw allow OpenSSH
ufw allow mosh
ufw enable
```

### 2. Clone

```bash
cd /opt  # or wherever you want it
git clone git@github.com:moazzam-qureshi/claude-trader.git
cd claude-trader
```

### 3. Secrets

Copy the .env example and fill it with **fresh credentials**:

```bash
cp .env.example .env
nano .env
```

Required values:
- `BINANCE_API_KEY`, `BINANCE_API_SECRET` — **rotated keys**, Spot Trading only, NO withdrawals, NO margin (halal spot constraint)
- `BINANCE_TESTNET` — `false` for mainnet
- `DISCORD_UNIVERSE_WEBHOOK_URL` — webhook for the universe-events channel
- Postgres + Redis URLs — leave the defaults if you're using the bundled containers

### 4. Bootstrap STATE.md from the example

```bash
cp runtime/STATE.md.bootstrap.example runtime/STATE.md
```

This file is gitignored — each deployment has its own STATE that the
heartbeat trader rewrites every shift.

### 5. Build + migrate + bring up the stack

```bash
docker compose build
docker compose run --rm tools alembic upgrade head
docker compose up -d
docker compose ps
```

### 6. Authenticate Claude with your Max plan

The `triage-worker` container runs `claude` (Anthropic CLI) as a
subprocess on every heartbeat shift. The CLI needs an OAuth token bound
to your Max plan. The token persists in the `claude-oauth` named volume
so you only do this once per VPS.

```bash
# Open a shell inside the triage-worker container
docker compose exec triage-worker bash

# Inside the container — start the device-flow login
claude /login

# Claude prints a URL like:
#   Visit: https://claude.ai/oauth/device?user_code=ABCD-1234
# Open that URL on your laptop/phone, sign in to your Anthropic account
# (the one with the Max plan), enter the code, approve.

# Verify the token is recognized
claude /status
# Should print your account info + plan tier

exit  # back to the VPS host
```

### 7. Smoke test — first manual heartbeat shift

```bash
docker compose exec -T triage-worker python -c "from trading_sandwich.triage.heartbeat import heartbeat_tick; import asyncio; asyncio.run(heartbeat_tick()); print('done')"
```

If this succeeds:
- A row appears in `heartbeat_shifts`
- `runtime/STATE.md` is rewritten by Claude
- A diary entry appears in `runtime/diary/<today>.md`
- A Discord card may post (if Claude touches the universe — usually not on first shift)

Verify:
```bash
docker compose run --rm tools python //app/scripts/watch_trader.py --once
```

### 8. Persistent monitoring

Detach a long-running dashboard inside `tmux`:

```bash
tmux new -s trader
docker compose run --rm tools python //app/scripts/watch_trader.py --interval 5
# Ctrl-B then D to detach (dashboard keeps running in background)
```

From your laptop or phone:
```bash
mosh root@your.vps.ip
tmux attach -t trader
```

`mosh` is much better than plain SSH for unstable mobile networks — the
session survives wifi changes, sleep, etc.

## Operational commands

| What | Command |
|---|---|
| View live dashboard | `docker compose run --rm tools python //app/scripts/watch_trader.py` |
| One-shot snapshot | `docker compose run --rm tools python //app/scripts/watch_trader.py --once` |
| Tail all worker logs | `docker compose logs -f triage-worker execution-worker celery-beat` |
| Recent shifts (CLI) | `docker compose run --rm tools python -m trading_sandwich.cli heartbeat shifts --limit 10` |
| Universe state | `docker compose run --rm tools python -m trading_sandwich.cli heartbeat universe show` |
| Universe events | `docker compose run --rm tools python -m trading_sandwich.cli heartbeat universe events` |
| Trip kill-switch | `docker compose run --rm cli trading pause --reason "manual halt"` |
| Resume | `docker compose run --rm cli trading resume --ack-reason "cleared"` |

## Updating

Pull and restart — **never auto-deploy a live trading system**:

```bash
# 1. Stop the heartbeat scheduler so no shifts fire mid-deploy
docker compose stop celery-beat triage-worker

# 2. Pull the new code
git pull

# 3. Rebuild any changed images
docker compose build

# 4. Apply any new migrations
docker compose run --rm tools alembic upgrade head

# 5. Bring everything back up
docker compose up -d

# 6. Verify
docker compose run --rm tools python //app/scripts/watch_trader.py --once
```

## Backup

Postgres holds your trader's memory (`heartbeat_shifts`, `universe_events`,
plus Phase 1/2 tables). Daily `pg_dump` to S3/Backblaze recommended:

```bash
# Crude but works — runs daily at 03:00 UTC
echo "0 3 * * * cd /opt/claude-trader && docker compose exec -T postgres pg_dump -U postgres trading > /opt/backups/trading_\$(date +\%F).sql" | crontab -
```

Replace with rclone-to-cloud if you don't trust the VPS disk alone.

## When the trader needs attention

If the Discord channel goes silent for >24h AND you haven't paused:
1. SSH in, `docker compose ps` — anything restarting / stopped?
2. `docker compose logs --tail=100 triage-worker` — error spam?
3. Check the OAuth token — `docker compose exec triage-worker claude /status` may show "session expired" → re-do step 6.
