"""
Clawrity — FastAPI Application

Main entry point. Initializes database, loads client configs,
starts Slack bot, and exposes REST endpoints.
"""

import asyncio
import logging
import traceback
from contextlib import asynccontextmanager
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents.orchestrator import Orchestrator
from channels.protocol_adapter import ProtocolAdapter, NormalisedMessage
from channels.slack_handler import SlackHandler
from config.client_loader import ClientConfig, load_client_configs
from config.settings import get_settings
from skills.postgres_connector import get_connector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
client_configs: Dict[str, ClientConfig] = {}
orchestrator: Optional[Orchestrator] = None
protocol_adapter: Optional[ProtocolAdapter] = None
slack_handler: Optional[SlackHandler] = None
scheduler = None  # Set by heartbeat.scheduler


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    global client_configs, orchestrator, protocol_adapter, slack_handler, scheduler

    logger.info("=== Clawrity starting up ===")

    # 1. Init database schema
    try:
        db = get_connector()
        db.init_schema()
        logger.info("Database schema ready")
    except Exception as e:
        logger.error(f"Database init failed: {e}")
        logger.warning("Starting in degraded mode — database unavailable")

    # 2. Load client configs
    try:
        client_configs = load_client_configs()
        logger.info(
            f"Loaded {len(client_configs)} client(s): {list(client_configs.keys())}"
        )
    except Exception as e:
        logger.error(f"Client config loading failed: {e}")
        client_configs = {}

    # 3. Init orchestrator
    try:
        orchestrator = Orchestrator()
    except Exception as e:
        logger.error(f"Orchestrator init failed: {e}")

    # 4. Try to attach RAG retriever
    try:
        from rag.retriever import Retriever

        retriever = Retriever()
        orchestrator.set_retriever(retriever)
        logger.info("RAG retriever attached to orchestrator")
    except Exception as e:
        logger.info(f"RAG retriever not available (Phase 2): {e}")

    # 5. Init protocol adapter
    try:
        protocol_adapter = ProtocolAdapter(client_configs)
    except Exception as e:
        logger.error(f"Protocol adapter init failed: {e}")

    # 6. Start Slack bot
    if orchestrator and protocol_adapter:
        try:
            slack_handler = SlackHandler(protocol_adapter, client_configs, orchestrator)
            slack_handler.start()
        except Exception as e:
            logger.warning(f"Slack bot not started: {e}")
    else:
        logger.warning(
            "Slack bot not started — orchestrator or protocol adapter missing"
        )

    # 7. Start scheduler
    try:
        from heartbeat.scheduler import start_scheduler

        scheduler = start_scheduler(client_configs, orchestrator)
        logger.info("HEARTBEAT scheduler started")
    except Exception as e:
        logger.warning(f"Scheduler not started: {e}")

    logger.info("=== Clawrity ready ===")

    yield  # App runs here

    # Shutdown
    logger.info("=== Clawrity shutting down ===")
    try:
        if slack_handler:
            slack_handler.stop()
    except Exception as e:
        logger.warning(f"Slack handler stop error: {e}")
    try:
        if scheduler:
            scheduler.shutdown(wait=False)
    except Exception as e:
        logger.warning(f"Scheduler stop error: {e}")
    try:
        db.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Clawrity",
    description="Multi-channel AI business intelligence agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all exception handler to prevent process crashes."""
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}: {exc}\n"
        f"{traceback.format_exc()}"
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "path": str(request.url.path),
        },
    )


# ---------------------------------------------------------------------------
# Request/Response Models
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    client_id: str
    message: str


class ChatResponse(BaseModel):
    response: str
    qa_score: float
    qa_passed: bool
    retries: int
    sql: Optional[str] = None
    data_rows: int = 0
    rag_chunks_used: int = 0
    elapsed_seconds: float = 0.0


class CompareRequest(BaseModel):
    client_id: str
    message: str


class CompareResponse(BaseModel):
    without_rag: ChatResponse
    with_rag: ChatResponse


class ScoutRequest(BaseModel):
    client_id: str
    query: str


class ClientRequest(BaseModel):
    client_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Send a message and get an AI response."""
    if request.client_id not in client_configs:
        raise HTTPException(
            status_code=404, detail=f"Client not found: {request.client_id}"
        )
    if not orchestrator or not protocol_adapter:
        raise HTTPException(
            status_code=503, detail="Service not fully initialized. Check /health."
        )

    config = client_configs[request.client_id]
    message = protocol_adapter.normalise_api(request.client_id, request.message)

    result = await orchestrator.process(message, config)
    return ChatResponse(**result)


@app.post("/compare", response_model=CompareResponse)
async def compare(request: CompareRequest):
    """Side-by-side comparison: with RAG vs without RAG."""
    if request.client_id not in client_configs:
        raise HTTPException(
            status_code=404, detail=f"Client not found: {request.client_id}"
        )

    config = client_configs[request.client_id]
    message = protocol_adapter.normalise_api(request.client_id, request.message)

    # Without RAG
    saved_retriever = orchestrator.retriever
    orchestrator.retriever = None
    result_no_rag = await orchestrator.process(message, config)
    orchestrator.retriever = saved_retriever

    # With RAG
    result_with_rag = await orchestrator.process(message, config)

    return CompareResponse(
        without_rag=ChatResponse(**result_no_rag),
        with_rag=ChatResponse(**result_with_rag),
    )


@app.post("/scout")
async def scout(request: ScoutRequest):
    """Run a targeted scout search for competitor/market intelligence."""
    if request.client_id not in client_configs:
        raise HTTPException(
            status_code=404, detail=f"Client not found: {request.client_id}"
        )

    config = client_configs[request.client_id]

    try:
        from agents.scout_agent import ScoutAgent

        scout_agent = ScoutAgent()
        result = await scout_agent.search_query(config, request.query)

        if result is None:
            return {
                "response": "No relevant competitor or market news found for this query.",
                "has_results": False,
            }

        return {"response": result, "has_results": True}
    except Exception as e:
        logger.error(f"Scout endpoint failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/scout/digest")
async def scout_digest(request: ClientRequest):
    """Run full scout agent digest for a client."""
    if request.client_id not in client_configs:
        raise HTTPException(
            status_code=404, detail=f"Client not found: {request.client_id}"
        )

    config = client_configs[request.client_id]

    try:
        from agents.scout_agent import ScoutAgent

        scout_agent = ScoutAgent()
        result = await scout_agent.gather_intelligence(config)

        if result is None:
            return {
                "response": "No relevant market intelligence found.",
                "has_results": False,
            }

        return {"response": result, "has_results": True}
    except Exception as e:
        logger.error(f"Scout digest failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/digest")
async def trigger_digest(request: ClientRequest):
    """Manually trigger the daily digest pipeline (same as scheduled job)."""
    if request.client_id not in client_configs:
        raise HTTPException(
            status_code=404, detail=f"Client not found: {request.client_id}"
        )

    config = client_configs[request.client_id]

    try:
        from heartbeat.scheduler import run_digest

        digest_text = await run_digest(config, orchestrator)

        if digest_text is None:
            raise HTTPException(
                status_code=500, detail="Digest generation failed after all retries"
            )

        return {"response": digest_text, "status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Manual digest trigger failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/stats/{client_id}")
async def admin_stats(client_id: str):
    """RAG monitoring stats for a client."""
    if client_id not in client_configs:
        raise HTTPException(status_code=404, detail=f"Client not found: {client_id}")

    try:
        from rag.monitoring import get_stats

        return get_stats(client_id)
    except Exception as e:
        return {"error": str(e), "message": "Monitoring not yet configured"}


@app.post("/forecast/run/{client_id}")
async def run_forecast(client_id: str):
    """Trigger Prophet forecasting for a client."""
    if client_id not in client_configs:
        raise HTTPException(status_code=404, detail=f"Client not found: {client_id}")

    try:
        from forecasting.prophet_engine import ProphetEngine

        engine = ProphetEngine()
        results = engine.train_and_forecast(client_id)
        return {"status": "success", "branches_forecast": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/forecast/{client_id}/{branch}")
async def get_forecast(client_id: str, branch: str):
    """Get cached forecast for a branch."""
    if client_id not in client_configs:
        raise HTTPException(status_code=404, detail=f"Client not found: {client_id}")

    try:
        from forecasting.prophet_engine import ProphetEngine

        engine = ProphetEngine()
        forecast = engine.get_cached_forecast(client_id, branch)
        if not forecast:
            raise HTTPException(
                status_code=404, detail=f"No forecast found for {branch}"
            )
        return forecast
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    """System health check."""
    db = get_connector()
    db_connected = False
    try:
        db.execute_raw("SELECT 1")
        db_connected = True
    except Exception:
        pass

    scheduled_jobs = []
    if scheduler and hasattr(scheduler, "get_jobs"):
        try:
            scheduled_jobs = [
                {"id": job.id, "name": job.name, "next_run": str(job.next_run_time)}
                for job in scheduler.get_jobs()
            ]
        except Exception:
            pass

    return {
        "status": "healthy" if db_connected else "degraded",
        "database": "connected" if db_connected else "disconnected",
        "clients": list(client_configs.keys()),
        "scheduler_running": scheduler is not None and scheduler.running
        if scheduler
        else False,
        "scheduled_jobs": scheduled_jobs,
        "slack_active": slack_handler is not None and slack_handler._thread is not None,
    }


@app.post("/slack/events")
async def slack_events():
    """Slack webhook endpoint (HTTP mode fallback). Socket Mode is primary."""
    return {
        "message": "Slack events are handled via Socket Mode. This endpoint is a fallback."
    }
