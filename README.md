# Clawrity

Multi-channel AI business intelligence agent that delivers data-grounded insights, daily digests, and budget recommendations through Slack.

Built on the [OpenClaw](https://github.com/openclaw) architecture — Clawrity acts as the **backend intelligence layer** while OpenClaw handles the Slack gateway and routing. Each client gets a YAML config, a `SOUL.md` (personality + rules), and a `HEARTBEAT.md` (automated schedule) — zero code changes to onboard a new client.

## How It Works

```
Slack Message
     │
     ▼
 OpenClaw (Slack Gateway)
     │
     ▼
 Clawrity Backend (FastAPI)
     │
     ├── Protocol Adapter ─── normalises messages from any channel
     │
     ├── NL-to-SQL ─────────── converts question → PostgreSQL query
     │
     ├── RAG Retriever ─────── fetches historical context from pgvector
     │
     ├── Gen Agent ─────────── generates data-grounded response (LLM)
     │
     ├── QA Agent ──────────── validates numbers against actual data
     │     │                    rejects hallucinations, retries up to 2x
     │     ▼
     └── Response ──────────── sent back through Slack
```

### The Gen → QA Loop

Every response is **fact-checked before delivery**:

1. **Gen Agent** generates an answer using SQL results + RAG context
2. **QA Agent** checks every number and branch name against the actual query data
3. If QA score < threshold → Gen Agent **retries** with stricter instructions
4. Max 2 retries. Final response includes a confidence warning if QA never passes

### What Users See on Slack

**Ad-hoc queries** — mention `@clawrity` in any channel or DM:

> **@clawrity** what are bottom 3 performing branches?

Clawrity responds with a formatted table, analysis, and actionable recommendations — all grounded in your PostgreSQL data.

**Daily digests** — automated morning newsletters pushed to Slack at 8:00 AM:

- Executive summary of the last 7 days
- Bottom 3 branches by revenue with recommendations
- Channel efficiency breakdown
- Market intelligence from Scout Agent (competitor/sector news)

## Architecture

```
config/
├── clients/           # One YAML per client (zero-code onboarding)
│   └── client_acme.yaml
├── settings.py        # Environment variable loader
└── llm_client.py      # Multi-provider LLM client (Groq, Ollama, Mistral, NVIDIA)

agents/
├── orchestrator.py    # Pipeline coordinator: NL-to-SQL → Gen → QA
├── gen_agent.py       # Response generation (LLM)
├── qa_agent.py        # Hallucination detection + scoring
└── scout_agent.py     # Competitor/sector news via web search

channels/
├── protocol_adapter.py  # Channel-agnostic message normalisation
├── slack_handler.py     # Slack Socket Mode (primary)
└── teams_handler.py     # Microsoft Teams (stub — ready to wire up)

skills/
├── nl_to_sql.py         # Natural language → PostgreSQL SELECT
├── postgres_connector.py # PostgreSQL + pgvector connection manager
└── web_search.py        # Tavily (primary) + DuckDuckGo (fallback)

rag/
├── chunker.py           # Aggregation-based semantic chunking
├── retriever.py         # Intent-based chunk type selection
├── vector_store.py      # pgvector embedding + search
├── preprocessor.py      # Data cleaning for RAG pipeline
├── evaluator.py         # RAG quality evaluation
└── monitoring.py        # Interaction logging + stats

soul/                    # Personality + rules per client
├── acme_soul.md

heartbeat/               # Automated digest schedules per client
├── acme_heartbeat.md
├── heartbeat_loader.py
└── scheduler.py         # APScheduler cron jobs

forecasting/
└── prophet_engine.py    # Prophet time series forecasting (6-month horizon)
```

## Setup

### 1. Clone & Install

```bash
git clone <repo-url>
cd clawrity
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Fill in your `.env`:

| Variable | What | Where to get it |
|----------|------|-----------------|
| `LLM_PROVIDER` | `groq` (recommended) or `ollama` / `mistral` | — |
| `GROQ_API_KEY` | LLM inference | [console.groq.com](https://console.groq.com) |
| `DATABASE_URL` | PostgreSQL + pgvector | Docker Compose (included) |
| `SLACK_BOT_TOKEN` | Slack bot (xoxb-...) | Slack App → OAuth & Permissions |
| `SLACK_APP_TOKEN` | Socket Mode (xapp-...) | Slack App → Socket Mode |
| `SLACK_SIGNING_SECRET` | Request verification | Slack App → Basic Information |
| `TAVILY_API_KEY` | Web search (Scout Agent) | [app.tavily.com](https://app.tavily.com) |
| `ACME_SLACK_WEBHOOK` | Digest delivery | Slack → Incoming Webhooks |

### 3. Start Database

```bash
docker compose up -d postgres
```

### 4. Seed Data

Download and place in `data/raw/`:
- [Global Superstore](https://kaggle.com/datasets/apoorvaappz/global-super-store-dataset)
- [Marketing Campaign Performance](https://kaggle.com/datasets/manishabhatt22/marketing-campaign-performance-dataset)

```bash
mkdir -p data/raw data/processed

python scripts/seed_demo_data.py --client_id acme_corp \
  --superstore data/raw/Global_Superstore2.csv \
  --marketing data/raw/marketing_campaign_dataset.csv

python scripts/run_rag_pipeline.py --client_id acme_corp
```

### 5. Run

```bash
uvicorn main:app --reload --port 8000
```

### 6. Slack App Setup

1. Create app at [api.slack.com/apps](https://api.slack.com/apps)
2. **Socket Mode** → Enable → generate App-Level Token → copy to `SLACK_APP_TOKEN`
3. **OAuth & Permissions** → add scopes:
   - `app_mentions:read`, `chat:write`, `channels:history`
   - `channels:read`, `im:history`, `im:read`, `im:write`
   - Install to Workspace → copy `SLACK_BOT_TOKEN`
4. **Event Subscriptions** → subscribe to:
   - `app_mention`, `message.channels`, `message.im`
5. **Basic Information** → copy `SLACK_SIGNING_SECRET`
6. Invite the bot: `/invite @Clawrity` in your channel


## Adding a New Client

1. Create `config/clients/client_newcorp.yaml`:
   ```yaml
   client_id: newcorp
   client_name: NewCorp Inc
   countries: ["US"]
   hallucination_threshold: 0.75
   digest_schedule: "09:00"
   timezone: "America/New_York"
   channels:
     slack_webhook: "${NEWCORP_SLACK_WEBHOOK}"
   soul_file: "soul/newcorp_soul.md"
   heartbeat_file: "heartbeat/newcorp_heartbeat.md"
   ```

2. Create `soul/newcorp_soul.md` — defines personality and business rules
3. Create `heartbeat/newcorp_heartbeat.md` — defines digest schedule
4. Seed their data and restart. Zero code changes.

## Tech Stack

- **Backend**: FastAPI + Uvicorn
- **Database**: PostgreSQL + pgvector (structured data + vector embeddings)
- **LLM**: Groq / Ollama / Mistral / NVIDIA NIM (OpenAI-compatible)
- **Embeddings**: all-MiniLM-L6-v2 (CPU, 384 dims)
- **Slack**: Bolt SDK (Socket Mode)
- **Scheduler**: APScheduler
- **Forecasting**: Facebook Prophet
- **Web Search**: Tavily + DuckDuckGo fallback
