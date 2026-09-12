"""
AI Tree Chatbot — application entrypoint.

Everything actually happens in router.py (endpoints), service.py (business
logic), repository.py (queries), db.py (connection/schema), and vectorstore.py
(ChromaDB). This file only wires the FastAPI app together.
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv(override=True)  # must run before router/service read env vars at import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import service
from router import router

BASE_DIR = os.path.dirname(__file__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await service.startup()
    yield
    await service.shutdown()


app = FastAPI(title="AI Tree Chatbot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
