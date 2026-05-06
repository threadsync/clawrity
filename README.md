# Clawrity

Multi-channel AI business intelligence agent.

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

Fill in `.env`:

```env
GROQ_API_KEY=gsk_...
XIAOMI_API_KEY=your-xiaomi-api-key
XIAOMI_BASE_URL=https://api.xiaomi.com/v1
XIAOMI_REGION=sg
DATABASE_URL=postgresql://user:pass@localhost:5432/clawrity
TAVILY_API_KEY=tvly-...
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
ACME_SLACK_WEBHOOK=https://hooks.slack.com/services/...
```

### 3. Start Database

```bash
docker compose up -d postgres
```

### 4. Seed Data

Download and place in `data/raw/`:
- https://kaggle.com/datasets/apoorvaappz/global-super-store-dataset
- https://kaggle.com/datasets/manishabhatt22/marketing-campaign-performance-dataset

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

Health check: `http://localhost:8000/health`

### 6. Slack

1. Create app at https://api.slack.com/apps
2. Socket Mode → Enable → generate `SLACK_APP_TOKEN`
3. OAuth & Permissions → add scopes: `app_mentions:read`, `chat:write`, `channels:history`, `channels:read`, `im:history`, `im:read`, `im:write` → install → copy `SLACK_BOT_TOKEN`
4. Event Subscriptions → subscribe: `app_mention`, `message.channels`, `message.im`
5. Basic Information → copy `SLACK_SIGNING_SECRET`
6. `/invite @Clawrity` in your channel

### API

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"client_id": "acme_corp", "message": "What is the total revenue for Seattle?"}'
```

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Send message |
| POST | `/compare` | RAG vs no-RAG comparison |
| POST | `/scout` | Competitor intelligence |
| POST | `/scout/digest` | Full scout digest |
| POST | `/digest` | Trigger daily digest |
| GET | `/admin/stats/{client_id}` | RAG stats |
| POST | `/forecast/run/{client_id}` | Run forecasting |
| GET | `/forecast/{client_id}/{branch}` | Get forecast |
| GET | `/health` | Health check |
