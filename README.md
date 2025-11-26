# Freedom Assignment

## Prerequisites

- Python 3.11+
- `pip` for dependency installation

Install the project dependencies:

```bash
pip install -r requirements.txt
```

## Running the FastAPI service

Launch the development server:

```bash
uvicorn src.api.server:app --reload
```

Open your browser at http://127.0.0.1:8000 to access the UI. Enter an industry name and click **Generate**; the backend will run the news → idea → image pipeline and stream the results back to the page. Generated assets are saved under the configured `output` directory and are served directly from the `/static` route.

## CLI pipeline (optional)

The existing CLI entry point still works:

```bash
python -m src.main
```

Export the `INDUSTRY` environment variable before running the script to select a different segment.

## Workflow diagram

```
Browser UI
   │ (industry prompt)
   ▼
FastAPI server (`/generate`)
   │ orchestrates async job
   ├─▶ News scraper (`src/news/scraper.py`)
   │       ▲ uses cached rows (`src/news/storage.py`)
   │       └ pulls top headlines + metadata
   ├─▶ Idea generator (`src/llm/idea_generator.py`)
   │       ▲ powered by local Ollama / configured LLM
   │       └ crafts hooks + captions
   ├─▶ Image generator (`src/images/generator.py`)
   │       └ renders storyboard frames via Stable Diffusion
   ▼
Result bundler (`src/news/service.py`)
   │ persists images + metadata under `output/<timestamp>`
   ▼
Browser stream (Server-Sent Events)
```

## How the project works

- **FastAPI UI server (`src/api/server.py`)** exposes `/` for the HTML front end and `/generate` for the streaming API. When the user submits an industry, the server spins up an asynchronous pipeline job and streams partial updates (text + image paths) back to the browser.
- **News ingestion layer** combines `src/news/scraper.py` for live article pulls (NewsAPI or configured feeds) and `src/news/storage.py` for lightweight SQLite caching/deduplication so repeated runs reuse existing context.
- **Idea generation** in `src/llm/idea_generator.py` feeds curated headlines into the configured LLM (Ollama by default, overridable via env/config) to craft story beats, angles, and captions tailored to the chosen industry.
- **Image generation** in `src/images/generator.py` turns each idea into a prompt, calls the selected diffusion backend, saves JPEGs under `output/<run_id>`, and records prompt → asset metadata.
- **Orchestration** happens inside `src/news/service.py` and `src/main.py`, which coordinate scraping, LLM ideation, and image rendering, then emit structured events that the web UI consumes to progressively reveal text + imagery.
- **Outputs** are stored on disk (images + `metadata.json`) and simultaneously streamed to the client. The `/static` route serves completed assets, allowing the UI to show thumbnails immediately after each frame is generated.