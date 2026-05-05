# Clawrity

**Multi-channel AI business intelligence agent.** Ask questions in natural language via Slack or REST API and get data-grounded answers with specific numbers, daily digests, budget recommendations, ROI forecasts, and competitor intelligence.

---

## Architecture

```
User (Slack/API) → ProtocolAdapter → Orchestrator → NL-to-SQL → PostgreSQL
                                              ↓
                                    Gen Agent (LLM) → QA Agent → Response
                                              ↑
                                    RAG Retriever (pgvector)
                                              ↑
                                    Scout Agent (web search)
```

- **Orchestrator** — coordinates the full pipeline with retry logic
- **Gen Agent** — generates data-grounded responses with specific figures
- **QA Agent** — validates responses for hallucinations (branch names, numbers)
- **Scout Agent** — fetches competitor/sector news via Tavily
- **RAG Retriever** — semantic search over historical business data (pgvector)
- **SOUL.md** — per-client personality and rules
- **HEARTBEAT.md** — autonomous daily digest scheduling

---

## Tech Stack

| Component | Tool |
|---|---|
| Language | Python 3.11 |
| API Framework | FastAPI + uvicorn |
| LLM | Groq (llama-3.3-70b-versatile) or NVIDIA NIM |
| Embeddings | sentence-transformers all-MiniLM-L6-v2 (384d) |
| Database | PostgreSQL + pgvector |
| Channel | Slack Bolt SDK (Socket Mode) |
| Scheduler | APScheduler |
| Web Search | Tavily API + DuckDuckGo fallback |
| Forecasting | Prophet |

---

## Quick Start (From Scratch)

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- [Groq API key](https://console.groq.com) (free)
- [Tavily API key](https://app.tavily.com) (free)

### 1. Clone & Setup

```bash
git clone <your-repo-url>
cd clawrity

# Create virtual environment
python3 -m venv venv
source venv/bin/activate   # Linux/Mac
# venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and fill in your keys:

```env
GROQ_API_KEY=gsk_...              # from console.groq.com
DATABASE_URL=postgresql://user:pass@localhost:5432/clawrity
TAVILY_API_KEY=tvly-...           # from app.tavily.com

# Slack (optional — for Slack integration)
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...

# Digest webhook (optional)
ACME_SLACK_WEBHOOK=https://hooks.slack.com/services/...
```

### 3. Start PostgreSQL + pgvector

```bash
docker compose up -d postgres
```

Wait ~10 seconds for PostgreSQL to initialize, then verify:

```bash
docker compose ps
# postgres should show "healthy"
```

### 4. Download Datasets

Download these two Kaggle datasets and place the files in `data/raw/`:

1. **Global Superstore**: https://kaggle.com/datasets/apoorvaappz/global-super-store-dataset
2. **Marketing Campaign Performance**: https://kaggle.com/datasets/manishabhatt22/marketing-campaign-performance-dataset

```bash
mkdir -p data/raw data/processed
# Place Global_Superstore2.csv and marketing_campaign_dataset.csv in data/raw/
```

### 5. Seed Demo Data

```bash
python scripts/seed_demo_data.py --client_id acme_corp \
  --superstore data/raw/Global_Superstore2.csv \
  --marketing data/raw/marketing_campaign_dataset.csv
```

### 6. Run RAG Pipeline

```bash
python scripts/run_rag_pipeline.py --client_id acme_corp
```

### 7. Start the Server

```bash
uvicorn main:app --reload --port 8000
```

Server runs at `http://localhost:8000`. Health check: `http://localhost:8000/health`

---

## Test the API

```bash
# Simple question
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"client_id": "acme_corp", "message": "What is the total revenue for the Seattle branch?"}'

# Recommendation question
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"client_id": "acme_corp", "message": "How can we improve revenue for the Seattle branch?"}'

# Trigger digest
curl -X POST http://localhost:8000/digest \
  -H "Content-Type: application/json" \
  -d '{"client_id": "acme_corp"}'
```

---

## Slack Bot Setup (Socket Mode)

### 1. Create Slack App

1. Go to https://api.slack.com/apps
2. Click **Create New App** → **From scratch**
3. Name it `Clawrity` and select your workspace

### 2. Enable Socket Mode

1. Left sidebar → **Socket Mode** → Toggle ON
2. Generate Token → name it `clawrity-socket`
3. Copy the `xapp-...` token → paste into `.env` as `SLACK_APP_TOKEN`

### 3. Configure Bot Permissions

1. **OAuth & Permissions** → **Bot Token Scopes**, add:
   - `app_mentions:read`
   - `chat:write`
   - `channels:history`
   - `channels:read`
   - `im:history`
   - `im:read`
   - `im:write`
2. Click **Install to Workspace**
3. Copy the `xoxb-...` token → paste into `.env` as `SLACK_BOT_TOKEN`

### 4. Enable Events

1. **Event Subscriptions** → Toggle ON
2. Under **Subscribe to bot events**, add:
   - `app_mention`
   - `message.channels`
   - `message.im`
3. Click **Save Changes**

### 5. Get Signing Secret

1. **Basic Information** → **App Credentials**
2. Copy **Signing Secret** → paste into `.env` as `SLACK_SIGNING_SECRET`

### 6. Invite Bot to Channel

```
/invite @Clawrity
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/chat` | Send message → get AI response |
| `POST` | `/compare` | Side-by-side RAG vs no-RAG comparison |
| `POST` | `/scout` | Targeted competitor/market intelligence search |
| `POST` | `/scout/digest` | Full scout agent digest for a client |
| `POST` | `/digest` | Manually trigger daily digest pipeline |
| `GET` | `/admin/stats/{client_id}` | RAG monitoring stats |
| `POST` | `/forecast/run/{client_id}` | Trigger Prophet forecasting |
| `GET` | `/forecast/{client_id}/{branch}` | Get cached forecast |
| `GET` | `/health` | System health check |

---

## Example Questions to Ask

| Category | Question |
|----------|----------|
| Simple data | "What is the total revenue for the Seattle branch?" |
| Channel analysis | "Show me revenue by channel for Seattle" |
| Rankings | "What are the top 5 branches by revenue?" |
| ROI | "What is the ROI for New York City?" |
| Country drill-down | "Show me total revenue by country for Australia" |
| Recommendations | "How can we improve revenue for the Seattle branch?" |
| Strategy | "What strategy would you recommend for the London branch?" |
| Trends | "What is the revenue trend from 2011 to 2014?" |
| Channel comparison | "Which channel has the highest ROI overall?" |
| Bottom performers | "What are the bottom 10 performing branches?" |

---

## Adding a New Client

1. Create `config/clients/client_<name>.yaml` (copy from `client_acme.yaml`)
2. Create `soul/<name>_soul.md` with personality/rules
3. Create `heartbeat/<name>_heartbeat.md` with schedule
4. Place data in `data/raw/` and run seed + RAG scripts
5. Restart — zero code changes required

---

## Project Structure

```
clawrity/
├── main.py                         # FastAPI application + lifespan
├── agents/
│   ├── orchestrator.py             # Pipeline coordinator (retry loop)
│   ├── gen_agent.py                # LLM response generation
│   ├── qa_agent.py                 # Hallucination checker
│   └── scout_agent.py              # Competitor intelligence
├── config/
│   ├── settings.py                 # pydantic-settings from .env
│   ├── llm_client.py               # LLM factory (Groq/NVIDIA) with retry
│   ├── client_loader.py            # YAML client config loader
│   └── clients/client_acme.yaml
├── channels/
│   ├── protocol_adapter.py         # Message normalisation
│   ├── slack_handler.py            # Slack Socket Mode
│   └── teams_handler.py            # Teams stub
├── skills/
│   ├── nl_to_sql.py                # Natural language → SQL
│   ├── postgres_connector.py       # PostgreSQL + pgvector
│   └── web_search.py               # Tavily + DuckDuckGo
├── rag/
│   ├── preprocessor.py             # Data cleaning
│   ├── chunker.py                  # Semantic chunking
│   ├── vector_store.py             # Embed + pgvector store
│   ├── retriever.py                # Intent-based retrieval
│   ├── evaluator.py                # RAG quality metrics
│   └── monitoring.py               # JSONL interaction logging
├── soul/
│   ├── soul_loader.py
│   └── acme_soul.md
├── heartbeat/
│   ├── heartbeat_loader.py
│   ├── scheduler.py                # APScheduler digest jobs
│   └── acme_heartbeat.md
├── forecasting/
│   └── prophet_engine.py           # Prophet time series
├── connectors/
│   ├── base_connector.py
│   └── csv_connector.py
├── etl/
│   └── normaliser.py
├── scripts/
│   ├── seed_demo_data.py           # Seed PostgreSQL from CSV
│   └── run_rag_pipeline.py         # Preprocess → chunk → embed
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `Connection refused` on /chat | PostgreSQL not running — `docker compose up -d postgres` |
| `Rate limited (429)` | LLM API throttling — system auto-retries with backoff |
| `No module named 'X'` | Activate venv: `source venv/bin/activate` |
| Slack bot not responding | Check `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` in `.env` |
| `Clawrity digest unavailable` | Set valid `ACME_SLACK_WEBHOOK` in `.env` |
| Embeddings slow on first run | MiniLM downloads ~80MB on first use — subsequent runs are cached |

---

## License

Private — internal use only.
