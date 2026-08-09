from __future__ import annotations

import os
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ai_agent.config import get_config
from ai_agent.db import init_db, dispose_engine
from ai_agent.llm import close_llm
from ai_agent.routes.sessions import router as sessions_router
from ai_agent.services.auto_step import start_auto_stepper, stop_auto_stepper

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    config = get_config()
    auto_task = None
    if config.AUTO_STEP:
        auto_task = start_auto_stepper()

    try:
        yield
    finally:
        if auto_task:
            stop_auto_stepper()
        await close_llm()
        dispose_engine()
        logger.info("Agent service shut down")


app = FastAPI(
    title="CTF AI Red Team Agent",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(sessions_router, prefix="/api/agent")
