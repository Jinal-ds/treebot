"""
Router layer — HTTP endpoints only. Each handler parses the request and hands
off to service.py; no business logic or SQL lives here.
"""

import os

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse

import service
import vectorstore
from schema import ChatRequest, ChatResponse

BASE_DIR = os.path.dirname(__file__)

router = APIRouter()


@router.get("/api/trees")
async def list_trees():
    return await service.list_trees()


@router.get("/api/tree/{tree_id}")
async def get_tree(tree_id: str):
    return await service.get_tree(tree_id)


@router.post("/api/chat/{tree_id}", response_model=ChatResponse)
async def chat(tree_id: str, req: ChatRequest):
    reply = await service.chat(tree_id, req.session_id, req.message, req.language)
    return ChatResponse(reply=reply)


@router.get("/api/analytics/scans")
async def scan_analytics():
    return await service.scan_analytics()


@router.get("/api/qr/{tree_id}.png")
async def qr_code(tree_id: str):
    png_bytes = await service.qr_png(tree_id)
    return Response(content=png_bytes, media_type="image/png")


@router.get("/t/{tree_id}")
async def scan_tree(tree_id: str, request: Request):
    """The URL every tree's QR code actually points to. Logs a scan event for
    analytics (which tree is being scanned, and how often), then hands off to
    the same SPA frontend that '/' serves, which reads the tree id from the
    path and loads that tree's details + chat."""
    await service.record_scan(
        tree_id, request.headers.get("user-agent"), request.headers.get("referer")
    )
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@router.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@router.get("/analytics")
def analytics_page():
    return FileResponse(os.path.join(BASE_DIR, "static", "analytics.html"))


@router.get("/healthz")
async def healthz():
    trees = await service.list_trees()
    return {
        "status": "ok",
        "trees_in_postgres": len(trees),
        "chroma_docs": vectorstore.get_collection().count(),
    }
