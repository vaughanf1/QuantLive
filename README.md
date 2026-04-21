# QuantLive — GoldSignal

An autonomous gold (XAUUSD) trading signal system built with FastAPI, PostgreSQL, and Twelve Data. It ingests price candles, runs rule-based strategies through a backtester and signal pipeline, tracks live outcomes, and can deliver high-conviction alerts to Telegram.

- Live dashboard at `/` shows strategy performance, open signals, and scheduler state
- Live chart at `/chart` renders candles and signal markers
- Background scheduler refreshes candles, runs backtests, scans signals, optimizes params, and checks outcomes 24/7

## What's inside

- **FastAPI** app (`app/main.py`) with a lifespan that seeds the DB and starts APScheduler
- **Four strategies** in `app/strategies/`: liquidity sweep, trend continuation, breakout expansion, EMA momentum
- **Backtester + walk-forward validation** (`app/services/backtester.py`, `walk_forward.py`)
- **Signal pipeline** that scores, risk-checks, and filters trades (`app/services/signal_pipeline.py`)
- **Outcome detector** that tracks TP/SL in real time (`app/services/outcome_detector.py`)
- **Feedback loop** that deprioritises underperforming strategies (`app/services/feedback_controller.py`)
- **Telegram notifier** for signal + daily health digest alerts (optional)
- **PostgreSQL** via SQLAlchemy async + Alembic migrations

## Prerequisites

- Python 3.12
- PostgreSQL 14+ (local or hosted)
- A free [Twelve Data](https://twelvedata.com/) API key
- (Optional) Telegram bot token + chat ID if you want push alerts
- (Optional) Docker, if you'd rather run everything in containers

## Quick start (local)

```bash
# 1. Clone
git clone https://github.com/vaughanf1/QuantLive.git
cd QuantLive

# 2. Create and activate a virtualenv
python3.12 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 4. Create a local Postgres database
createdb goldsignal              # or use psql / a GUI

# 5. Configure environment variables
cp .env.example .env
# then edit .env (see next section)

# 6. Run database migrations
alembic upgrade head

# 7. Start the app
uvicorn app.main:app --reload --port 8080
```

Visit:
- Dashboard → http://localhost:8080/
- Chart → http://localhost:8080/chart
- Health → http://localhost:8080/health
- API docs → http://localhost:8080/docs

On first startup, the app auto-seeds strategies, backfills ~5000 H1/H4/D1 candles from Twelve Data, and runs an initial round of backtests. Expect the first boot to take a few minutes.

## Environment variables

Set these in a `.env` file at the project root (or export them in your shell / Railway dashboard).

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | yes | — | Postgres URL. `postgresql://` and `postgres://` are auto-rewritten to the asyncpg driver. |
| `TWELVE_DATA_API_KEY` | yes | — | API key from twelvedata.com |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_JSON` | no | `false` | Set `true` for JSON logs (useful in production) |
| `CANDLE_REFRESH_DELAY_SECONDS` | no | `60` | Delay after candle close before fetching |
| `ACCOUNT_BALANCE` | no | `100000` | Account size used for position sizing |
| `TELEGRAM_BOT_TOKEN` | no | `""` | Leave blank to disable Telegram alerts |
| `TELEGRAM_CHAT_ID` | no | `""` | Your chat/channel ID for alerts |

Example `.env`:

```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/goldsignal
TWELVE_DATA_API_KEY=your_api_key_here
LOG_LEVEL=INFO
LOG_JSON=false
ACCOUNT_BALANCE=100000
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

## Run with Docker

```bash
docker build -t quantlive .
docker run --rm -p 8080:8080 --env-file .env quantlive
```

The container runs `alembic upgrade head` on boot and then starts uvicorn on port 8080.

## Deploy to Railway

This repo ships with a `railway.json` and `Dockerfile` that Railway picks up automatically.

1. Create a new project on [Railway](https://railway.app/) and point it at your fork.
2. Add a **PostgreSQL** plugin — Railway will inject `DATABASE_URL`.
3. In the service **Variables** tab, set:
   - `TWELVE_DATA_API_KEY`
   - `ACCOUNT_BALANCE` (optional)
   - `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (optional)
4. Deploy. Railway runs the Dockerfile, which applies migrations and starts the app on the port Railway exposes.
5. Health check is `/health`.

## Running the tests

```bash
pytest
```

Most tests use an in-memory SQLite or a local Postgres. Ensure your `.env` is set before running the suite.

## How the schedule works

Once running, APScheduler (UTC) handles everything:

| Job | Schedule |
|---|---|
| Refresh M15 candles | every 15 min at `:01, :16, :31, :46` |
| Refresh H1 candles | hourly at `:01` |
| Refresh H4 candles | every 4h at `:01` |
| Refresh D1 candles | daily at `00:01` |
| Run backtests | every 4h |
| Signal scanner | every 30 min |
| Param optimization | every 6h |
| Outcome checks | every 90 seconds |
| Data retention | daily at `03:00` |
| Health digest | daily at `06:00` |

## Project layout

```
app/
  api/            # FastAPI routers (health, status, candles, chart, dashboard)
  models/         # SQLAlchemy ORM models
  schemas/        # Pydantic schemas
  services/      # Backtester, signal pipeline, outcome detector, telegram, etc.
  strategies/    # Strategy implementations + shared helpers/indicators
  workers/       # APScheduler setup and scheduled jobs
  templates/     # Dashboard + chart HTML
  main.py        # FastAPI app + bootstrap
  config.py      # Pydantic settings
alembic/         # Database migrations
tests/           # Pytest suite
Dockerfile
railway.json
```

## Troubleshooting

- **`Missing TWELVE_DATA_API_KEY`** — make sure `.env` exists and your shell has loaded it (restart the server).
- **`database does not exist`** — run `createdb goldsignal` (or whatever you set in `DATABASE_URL`).
- **Migrations fail on first boot** — run `alembic upgrade head` manually and check the error; the Docker entrypoint logs `Migration failed, starting anyway` and continues.
- **No signals appearing** — it can take a full candle cycle (and enough history) before any strategy triggers. Check the dashboard or logs for scanner runs.
- **Rate limits from Twelve Data** — the free tier is limited; the bootstrapper fetches up to 5000 bars per timeframe on first boot.

## Disclaimer

This project is provided for educational and research purposes. It is not financial advice. Trading gold or any leveraged instrument is risky — test thoroughly on paper before risking real capital.
