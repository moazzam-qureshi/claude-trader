# trading-sandwich

24/7 crypto market analysis + execution system, built as an instance of the
MCP-Sandwich pattern (see `architecture.md`).

See `docs/superpowers/specs/` for the current design and
`docs/superpowers/plans/` for phased implementation plans.

## Quickstart (Phase 0)

    cp .env.example .env         # fill in values
    docker compose up -d
    docker compose run --rm cli doctor

## Phase 1 deploy runbook

1. Pull latest `main`, ensure `.env` is present.
2. Rebuild images:

        docker compose build

3. Start dependencies only:

        docker compose up -d postgres pgbouncer redis

4. Apply migrations (Alembic bypasses pgbouncer and connects direct to Postgres):

        docker compose run --rm tools alembic upgrade head

5. REST-backfill 1 year of raw candles for the universe (minutes per symbol).
   The backfill tool also creates any raw_candles partitions outside the ±6
   months that migration 0009 seeds:

        docker compose run --rm tools python -m trading_sandwich.ingestor.rest_backfill \
            --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT \
            --timeframes 5m,15m,1h,4h,1d --days 365

6. REST-backfill microstructure (30 d funding + 7 d open-interest snapshots):

        docker compose run --rm tools python -m trading_sandwich.ingestor.rest_backfill_microstructure \
            --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT

7. Compute Phase 1 features for every eligible historical candle:

        docker compose run --rm tools python -m trading_sandwich.features.backfill

8. Start the full stack:

        docker compose up -d

9. Sanity check:

        docker compose run --rm cli doctor
        docker compose run --rm cli stats

10. Open Grafana at `http://localhost:3000` (user `admin`, password from
    `.env`). Verify the *Trading Sandwich Health* dashboard is populating:
    candles-in / features / signals / outcomes stats, per-archetype
    and per-gating-outcome rates, pgbouncer pool saturation, backfill
    completeness.
11. Watch for 1 hour. Expected: `raw_candles` growing every 5 min,
    `features` populated per symbol × timeframe, `signals` emerging
    occasionally, `signal_outcomes` starting 15 min after the first
    `claude_triaged` signal.

### Updating the pgbouncer password

`pgbouncer/userlist.txt` ships with an `md5REPLACE_ME_BEFORE_DEPLOY`
placeholder. Generate the real hash before first boot:

    echo -n "${POSTGRES_PASSWORD}${POSTGRES_USER}" | md5sum | awk '{print "md5" $1}'

Replace the placeholder with that value and:

    docker compose up -d pgbouncer
