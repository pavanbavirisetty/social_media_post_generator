from __future__ import annotations

import asyncio
import json
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Iterable

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from src.main import pipeline_stream, run_pipeline
from src.utils.config import InstagramAsset, get_settings
from src.utils.logger import get_logger


app = FastAPI(title="social_media_post_generator", version="0.1.0")
logger = get_logger("WebServer")

base_path = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(base_path / "templates"))

settings = get_settings()
app.mount("/static", StaticFiles(directory=settings.output_dir), name="static")


class GenerateRequest(BaseModel):
    industry: str = Field(..., min_length=1, description="Industry name to target during generation.")


def _serialize_asset(asset: InstagramAsset) -> dict[str, str]:
    output_root = get_settings().output_dir
    try:
        relative_path = asset.image_path.relative_to(output_root).as_posix()
    except ValueError:
        relative_path = asset.image_path.name
    return {
        "headline": asset.idea.headline,
        "caption": asset.caption,
        "image_url": f"/static/{relative_path}",
        "source_url": asset.idea.source_url,
    }


def _serialize_assets(assets: Iterable[InstagramAsset]) -> list[dict[str, str]]:
    return [_serialize_asset(asset) for asset in assets]


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/generate")
async def generate(request_data: GenerateRequest) -> dict[str, object]:
    industry = request_data.industry.strip()
    if not industry:
        raise HTTPException(status_code=400, detail="Industry name must not be empty.")

    logger.info("Received generation request for industry '%s'", industry)
    try:
        assets = await run_in_threadpool(run_pipeline, industry)
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Generation failed for industry '%s': %s", industry, exc)
        raise HTTPException(status_code=500, detail="Failed to generate assets.") from exc

    if not assets:
        return {"status": "empty", "message": "No assets were generated. Try another industry."}

    serialized = _serialize_assets(assets)
    logger.info("Successfully generated %d assets for '%s'", len(serialized), industry)
    return {"status": "ok", "results": serialized}


def _format_sse(event: str, data: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/generate-stream")
async def generate_stream(request: Request, industry: str) -> StreamingResponse:
    industry = industry.strip()
    if not industry:
        raise HTTPException(status_code=400, detail="Industry name must not be empty.")

    logger.info("Starting streaming generation for industry '%s'", industry)
    queue: Queue[dict[str, object] | None] = Queue()

    def worker() -> None:
        try:
            generated = False
            for asset in pipeline_stream(industry):
                generated = True
                queue.put({"event": "asset", "data": _serialize_asset(asset)})
            if generated:
                queue.put(
                    {
                        "event": "complete",
                        "data": {"message": f"Finished generating assets for '{industry}'."},
                    }
                )
            else:
                queue.put(
                    {
                        "event": "empty",
                        "data": {"message": "No assets were generated. Try another industry."},
                    }
                )
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Streaming generation failed for industry '%s': %s", industry, exc)
            queue.put(
                {"event": "error", "data": {"message": "Failed to generate assets. Check server logs."}}
            )
        finally:
            queue.put(None)

    thread = Thread(target=worker, daemon=True)
    thread.start()

    async def event_generator():
        yield _format_sse("status", {"message": f"Generating content for '{industry}'... This may take a while depending on the system configuration."})
        loop = asyncio.get_running_loop()
        while True:
            item = await loop.run_in_executor(None, queue.get)
            if item is None:
                break
            if await request.is_disconnected():
                logger.info("Client disconnected from stream for industry '%s'", industry)
                break
            event = item["event"]
            data = item["data"]
            yield _format_sse(event, data)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

