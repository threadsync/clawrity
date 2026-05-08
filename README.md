**Important Links:**
- [ Watch YouTube Demo](https://youtu.be/sXboHHSg4_k)
- [ Clawrity Pitch Deck](https://github.com/threadsync/clawrity/blob/main/Clawrity_PitchDeck.pptx)
- [ AI Disclosure Form](https://github.com/threadsync/clawrity/blob/main/OpenClaw_AI_Disclosure.docx)

# Clawrity

Multi-channel AI business intelligence agent that delivers data-grounded insights, daily digests, and budget recommendations through Slack.

## The Problem
Organizations are drowning in data but starving for answers. 73% of enterprise data goes completely unused because the gap isn't data availability—it's **data accessibility**. Because data is inaccessible to non-technical users:
- **Business Teams** waste 3-5 hours/week chasing analysts for simple insights.
- **Analysts** lose 40% of their time acting as human query engines for repetitive ad-hoc requests.
- **Executives** are forced to make delayed decisions based on stale, 40-page weekly reports.

## The Solution
Clawrity is an OpenClaw-powered Business Intelligence Agent that brings actionable, data-grounded insights directly to where your team works. Built on the [OpenClaw](https://github.com/openclaw) architecture, Clawrity acts as the **backend intelligence layer** while OpenClaw handles the Slack gateway and routing.

- **Talk to Your Data:** Ask questions in plain English (e.g., *"What are the bottom 3 performing branches?"*) and receive formatted tables, analysis, and recommendations drawn directly from your live PostgreSQL database.
- **Zero Hallucinations:** Our proprietary Gen → QA adversarial loop mathematically fact-checks every single number against the raw data before delivery.
- **Automated Digests:** Scheduled morning briefings (via `HEARTBEAT.md`) pushed directly to executive channels.
- **Zero-Code Onboarding:** Non-technical teams deploy customized agents instantly using just 3 simple text files (YAML + `SOUL.md` + `HEARTBEAT.md`).

## How It Works

```
Slack Message
     |
     v
 OpenClaw (Slack Gateway — Mistral Large)
     |-- Reads SOUL.md for routing rules
     |-- Business query --> POST /chat
     |-- Competitor query --> uses built-in web search
     |-- General chat --> responds directly
     |
     v
 Clawrity Backend (FastAPI)
     |
     |-- Protocol Adapter --- normalises messages from any channel
     |
     |-- NL-to-SQL --------- converts question --> PostgreSQL query
     |
     |-- RAG Retriever ------ fetches historical context from pgvector
     |
     |-- Gen Agent ---------- generates data-grounded response (Groq)
     |
     |-- QA Agent ----------- validates numbers against actual data
     |     |                   rejects hallucinations, retries up to 2x
     |     v
     +-- Response ----------- sent back through OpenClaw to Slack
```

### The OpenClaw Layer

OpenClaw is the conversational AI gateway between Slack and Clawrity:

- **SOUL.md** defines the agent's identity, tone, and routing logic. It tells OpenClaw: business/data questions go to `POST /chat`, competitor/news questions are handled via OpenClaw's built-in web search, and general conversation is handled directly.
- **HEARTBEAT.md** defines automated schedules. OpenClaw triggers the daily 8 AM digest pipeline by calling the `/digest` endpoint, then posts the result to Slack.
- **SKILL.md** (`skills/clawrity/SKILL.md`) teaches OpenClaw when and how to invoke the Clawrity backend -- which endpoint, what payload, how to handle errors.
- **Memory** -- OpenClaw's memory system (`memory/YYYY-MM-DD.md` + `MEMORY.md`) provides session continuity so the agent remembers past interactions and context.

### The Gen --> QA Loop

Every response is **fact-checked before delivery**:

1. **Gen Agent** generates an answer using SQL results + RAG context
2. **QA Agent** checks every number and branch name against the actual query data
3. If QA score < threshold --> Gen Agent **retries** with stricter instructions
4. Max 2 retries. Final response includes a confidence warning if QA never passes

### What Users See on Slack

**Ad-hoc queries** -- mention `@clawrity` in any channel or DM:

> **@clawrity** what are bottom 3 performing branches?

Clawrity responds with a formatted table, analysis, and actionable recommendations -- all grounded in your PostgreSQL data.

**Daily digests** -- automated morning newsletters pushed to Slack at 8:00 AM:

- Executive summary of the last 7 days
- Bottom 3 branches by revenue with recommendations
- Channel efficiency breakdown

## Architecture

```
config/
├── clients/           # One YAML per client (zero-code onboarding)
│   └── client_acme.yaml
├── settings.py        # Environment variable loader
└── llm_client.py      # Multi-provider LLM client (Groq, Ollama, Mistral, NVIDIA)

agents/
├── orchestrator.py    # Pipeline coordinator: NL-to-SQL --> Gen --> QA
├── gen_agent.py       # Response generation (LLM)
├── qa_agent.py        # Hallucination detection + scoring

channels/
├── protocol_adapter.py  # Channel-agnostic message normalisation
├── slack_handler.py     # Slack Socket Mode (primary)
└── teams_handler.py     # Microsoft Teams (stub -- ready to wire up)

skills/
├── nl_to_sql.py         # Natural language --> PostgreSQL SELECT
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

## OpenClaw Workspace

The OpenClaw workspace (`~/.openclaw/workspace/`) contains the files that configure the OpenClaw gateway:

```
~/.openclaw/workspace/
├── SOUL.md              # Agent identity, tone, and routing rules
├── HEARTBEAT.md         # Automated daily digest schedule and tasks
├── AGENTS.md            # Agent behavior, memory, and group chat rules
├── TOOLS.md             # Local environment notes
├── USER.md              # User preferences
├── IDENTITY.md          # Core identity
├── memory/              # Session memory (daily logs + long-term MEMORY.md)
└── skills/
    └── clawrity/
        └── SKILL.md     # Teaches OpenClaw how to call Clawrity backend
```

---

## Setup (Linux / macOS)

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
| `LLM_PROVIDER` | `groq` (recommended) or `ollama` / `mistral` | -- |
| `GROQ_API_KEY` | LLM inference | [console.groq.com](https://console.groq.com) |
| `DATABASE_URL` | PostgreSQL + pgvector | Docker Compose (included) |
| `SLACK_BOT_TOKEN` | Slack bot (xoxb-...) | Slack App --> OAuth & Permissions |
| `SLACK_APP_TOKEN` | Socket Mode (xapp-...) | Slack App --> Socket Mode |
| `SLACK_SIGNING_SECRET` | Request verification | Slack App --> Basic Information |
| `TAVILY_API_KEY` | Web search (Scout Agent) | [app.tavily.com](https://app.tavily.com) |
| `ACME_SLACK_WEBHOOK` | Digest delivery | Slack --> Incoming Webhooks |

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

---

## Setup (Windows)

### Prerequisites

- **Python 3.11+** -- Download from [python.org](https://www.python.org/downloads/). During installation, check "Add Python to PATH".
- **Docker Desktop** -- Download from [docker.com](https://www.docker.com/products/docker-desktop/). Required for PostgreSQL + pgvector. Make sure WSL 2 backend is enabled.
- **Git** -- Download from [git-scm.com](https://git-scm.com/download/win).

### 1. Clone & Install

Open **Command Prompt** or **PowerShell**:

```cmd
git clone <repo-url>
cd clawrity
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

> **Note on Prophet:** If `prophet` fails to install on Windows, install the C++ build tools first:
> 1. Download [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
> 2. Install "Desktop development with C++" workload
> 3. Retry `pip install -r requirements.txt`
>
> Alternatively, if you don't need forecasting, comment out `prophet>=1.1.5` in `requirements.txt` and skip the forecasting feature.

### 2. Configure

```cmd
copy .env.example .env
```

Open `.env` in any text editor (Notepad, VS Code) and fill in the values. See the environment variable table in the Linux section above.

### 3. Start Database

Make sure Docker Desktop is running, then:

```cmd
docker compose up -d postgres
```

### 4. Seed Data

Download the datasets and place them in `data\raw\`:
- [Global Superstore](https://kaggle.com/datasets/apoorvaappz/global-super-store-dataset)
- [Marketing Campaign Performance](https://kaggle.com/datasets/manishabhatt22/marketing-campaign-performance-dataset)

```cmd
if not exist data\raw mkdir data\raw
if not exist data\processed mkdir data\processed

python scripts/seed_demo_data.py --client_id acme_corp --superstore data\raw\Global_Superstore2.csv --marketing data\raw\marketing_campaign_dataset.csv

python scripts/run_rag_pipeline.py --client_id acme_corp
```

### 5. Run

```cmd
uvicorn main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`. Check health at `http://localhost:8000/health`.

---

## Slack App Setup (All Platforms)

1. Create app at [api.slack.com/apps](https://api.slack.com/apps)
2. **Socket Mode** --> Enable 
    generate App-Level Token --> copy to `SLACK_APP_TOKEN`

3. **OAuth & Permissions** --> 
    If openclaw gave a manifest json just copy it and paste in while creating app manifest or later in section called 'app manifest'
    or manually add scopes:
   - `app_mentions:read`, `chat:write`, `channels:history`
   - `channels:read`, `im:history`, `im:read`, `im:write`
   - Install to Workspace --> copy `SLACK_BOT_TOKEN`
4. **Event Subscriptions** --> subscribe to:
   - `app_mention`, `message.channels`, `message.im`
5. **Basic Information** --> copy `SLACK_SIGNING_SECRET`
6. Invite the bot: `/invite @Clawrity` in your channel

## OpenClaw Setup

1. Install OpenClaw following the [OpenClaw documentation](https://github.com/openclaw)
2. Copy the workspace files from this repo's `openclaw-workspace/` directory to `~/.openclaw/workspace/`:
   - `SOUL.md` -- routing rules and personality
   - `HEARTBEAT.md` -- daily digest schedule
   - `skills/clawrity/SKILL.md` -- backend API skill definition
3. Configure OpenClaw to use **Mistral Large** (`mistral-large-latest`) as the LLM
4. OpenClaw's memory system (`memory/` directory) provides session continuity across conversations
5. Start OpenClaw -- it will connect to Slack via Socket Mode and route queries to the Clawrity backend

## Adding a New Client(optional)

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

2. Create `soul/newcorp_soul.md` -- defines personality and business rules
3. Create `heartbeat/newcorp_heartbeat.md` -- defines digest schedule
4. Update OpenClaw's `SOUL.md` with the new client's routing rules
5. Seed their data and restart. Zero code changes.

## Tech Stack

- **Slack Gateway**: OpenClaw (Mistral Large for routing via SOUL.md)
- **Backend**: FastAPI + Uvicorn
- **LLM (Backend)**: Groq -- Llama 3.3 70B Versatile (supports Ollama / Mistral / NVIDIA NIM)
- **Database**: PostgreSQL + pgvector (structured data + vector embeddings)
- **Embeddings**: all-MiniLM-L6-v2 (CPU, 384 dims)
- **Slack SDK**: Bolt SDK (Socket Mode)
- **Scheduler**: APScheduler
- **Forecasting**: Facebook Prophet
- **Web Search**: OpenClaw built-in (competitor/market queries handled natively)

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/chat` | Send a business question, get a data-grounded response |
| POST | `/digest` | Trigger daily digest pipeline manually |
| POST | `/forecast/run/{client_id}` | Run Prophet forecasting for a client |
| GET | `/forecast/{client_id}/{branch}` | Get cached forecast for a branch |
| GET | `/health` | System health check |
| GET | `/admin/stats/{client_id}` | RAG monitoring stats |
