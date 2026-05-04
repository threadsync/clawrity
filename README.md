# Clawrity

**Multi-channel AI business intelligence agent.** Enterprise clients interact via Slack (or Teams) and get data-grounded answers, daily digests, budget recommendations, ROI forecasts, and competitor/sector intelligence — all specific to their business data.

---

## Architecture

Built on the **OpenClaw pattern**:
- **ProtocolAdapter** — normalises messages from any channel (Slack, Teams, etc.)
- **SOUL.md** — per-client personality, rules, and business context
- **HEARTBEAT.md** — autonomous daily digest scheduling

All intelligence lives in the Clawrity backend. OpenClaw layer has zero business logic.

## Tech Stack

| Component | Tool |
|---|---|
| Language | Python 3.11 |
| API Framework | FastAPI + uvicorn |
| LLM | Groq API — llama-3.3-70b-versatile |
| Embeddings | sentence-transformers all-MiniLM-L6-v2 (CPU, 384d) |
| Database | PostgreSQL + pgvector |
| Channel (dev) | Slack Bolt SDK (Socket Mode) |
| Channel (demo) | Microsoft Teams Bot Framework SDK |
| Scheduler | APScheduler AsyncIOScheduler |
| Web Search | Tavily API + DuckDuckGo fallback |
| Forecasting | Prophet |

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Docker & Docker Compose
- Groq API key (free: https://console.groq.com)
- Tavily API key (free: https://app.tavily.com)

### 2. Environment Setup

```bash
cp .env.example .env
# Fill in your API keys in .env
```

### 3. Start PostgreSQL + pgvector

```bash
docker compose up -d postgres
```

### 4. Install Dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 5. Download Kaggle Datasets

Download these two datasets and place them in `data/raw/`:

1. **Global Superstore**: https://kaggle.com/datasets/apoorvaappz/global-super-store-dataset
2. **Marketing Campaign Performance**: https://kaggle.com/datasets/manishabhatt22/marketing-campaign-performance-dataset

```bash
mkdir -p data/raw data/processed
# Place downloaded files in data/raw/
```

### 6. Seed Demo Data

```bash
python scripts/seed_demo_data.py --client_id acme_corp \
  --superstore data/raw/Global_Superstore2.csv \
  --marketing data/raw/marketing_campaign_dataset.csv
```

### 7. Run RAG Pipeline

```bash
python scripts/run_rag_pipeline.py --client_id acme_corp
```

### 8. Start the API

```bash
uvicorn main:app --reload --port 8000
```

---

## Slack Bot Setup (Socket Mode)

### Step 1: Create Slack App

1. Go to https://api.slack.com/apps
2. Click **Create New App** → **From scratch**
3. Name it `Clawrity` and select your workspace

### Step 2: Enable Socket Mode

1. In the left sidebar, click **Socket Mode**
2. Toggle **Enable Socket Mode** to ON
3. Click **Generate Token** — name it `clawrity-socket`
4. Copy the `xapp-...` token → paste into `.env` as `SLACK_APP_TOKEN`

### Step 3: Configure Bot Token

1. Go to **OAuth & Permissions**
2. Under **Bot Token Scopes**, add:
   - `app_mentions:read`
   - `chat:write`
   - `channels:history`
   - `channels:read`
3. Click **Install to Workspace**
4. Copy the `xoxb-...` token → paste into `.env` as `SLACK_BOT_TOKEN`

### Step 4: Enable Events

1. Go to **Event Subscriptions**
2. Toggle **Enable Events** to ON (no Request URL needed in Socket Mode)
3. Under **Subscribe to bot events**, add:
   - `app_mention`
   - `message.channels`
4. Click **Save Changes**

### Step 5: Get Signing Secret

1. Go to **Basic Information**
2. Under **App Credentials**, copy **Signing Secret**
3. Paste into `.env` as `SLACK_SIGNING_SECRET`

### Step 6: Invite Bot to Channel

In Slack, go to your desired channel and type:
```
/invite @Clawrity
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Send message → get AI response |
| POST | `/slack/events` | Slack webhook fallback |
| POST | `/compare` | Side-by-side RAG vs no-RAG |
| POST | `/forecast/run/{client_id}` | Trigger Prophet forecasting |
| GET | `/forecast/{client_id}/{branch}` | Get cached forecast |
| GET | `/admin/stats/{client_id}` | RAG monitoring stats |
| GET | `/health` | System status |

## Adding a New Client

1. Create `config/clients/client_newclient.yaml` (copy from `client_acme.yaml`)
2. Create `soul/newclient_soul.md`
3. Create `heartbeat/newclient_heartbeat.md`
4. Place data in `data/raw/` and run seed + RAG scripts
5. Restart — zero code changes required

---

## Project Structure

```
clawrity/
├── main.py                         # FastAPI application
├── config/                         # Configuration
│   ├── settings.py                 # pydantic-settings from .env
│   ├── client_loader.py            # YAML client config loader
│   └── clients/client_acme.yaml    # Per-client config
├── soul/                           # Per-client personality
│   ├── soul_loader.py
│   └── acme_soul.md
├── heartbeat/                      # Autonomous digest scheduling
│   ├── heartbeat_loader.py
│   ├── scheduler.py
│   └── acme_heartbeat.md
├── agents/                         # AI agents
│   ├── gen_agent.py                # Response generation
│   ├── qa_agent.py                 # Quality assurance
│   ├── orchestrator.py             # Pipeline coordinator
│   └── scout_agent.py              # Competitor intelligence
├── skills/                         # Capabilities
│   ├── postgres_connector.py       # DB connection pool
│   ├── nl_to_sql.py                # Natural language → SQL
│   └── web_search.py               # Tavily + DuckDuckGo
├── channels/                       # Message channels
│   ├── protocol_adapter.py         # OpenClaw normalisation
│   ├── slack_handler.py            # Slack Socket Mode
│   └── teams_handler.py            # Teams stub
├── rag/                            # Retrieval-augmented generation
│   ├── preprocessor.py
│   ├── chunker.py
│   ├── vector_store.py
│   ├── retriever.py
│   ├── evaluator.py
│   └── monitoring.py
├── forecasting/
│   └── prophet_engine.py
├── connectors/
│   ├── base_connector.py
│   └── csv_connector.py
├── etl/
│   └── normaliser.py
└── scripts/
    ├── seed_demo_data.py
    └── run_rag_pipeline.py
```
