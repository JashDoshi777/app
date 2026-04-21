"""
FastAPI Application — serves the dashboard and REST API.
"""

import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATE_DIR = Path(__file__).parent / "templates"

app = FastAPI(
    title="NIFTY OI Tracker",
    description="Live NIFTY option chain data dashboard",
    version="2.0.0",
)

from starlette.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    logger.info("Static files mounted from: %s", STATIC_DIR)
else:
    logger.error("STATIC DIR NOT FOUND: %s", STATIC_DIR)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the dashboard — read HTML directly to avoid Jinja2 version issues."""
    html_file = TEMPLATE_DIR / "index.html"
    if html_file.exists():
        return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>index.html not found</h1>", status_code=500)
