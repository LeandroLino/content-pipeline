import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.schemas import IngestPayload

# Local dev-only persistence for extracted content. Not wired to R2/Postgres
# yet (see PLAN.md section 5 "Storage Layer" for the future job-based layout).
INGESTED_DIR = Path("data/ingested")


@dataclass(frozen=True)
class SavedPaths:
    json_path: Path
    md_path: Path


def _slugify(text: str, max_length: int = 60) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug[:max_length] or "untitled"


def _build_markdown(payload: IngestPayload) -> str:
    """Render the payload as Markdown with a metadata header + raw_content body.

    Kept alongside the JSON so both humans and the downstream LLM step can
    read a structured version of the article (JSON alone flattens everything
    into a single string field, losing headings/lists/code formatting).
    """
    lines = [f"# {payload.raw_title or 'Untitled'}", ""]
    lines.append(f"**Source:** {payload.source}  ")
    lines.append(f"**URL:** {payload.original_url}  ")
    author = payload.metadata.get("author")
    if author:
        lines.append(f"**Author:** {author}  ")
    published_at = payload.metadata.get("published_at")
    if published_at:
        lines.append(f"**Published:** {published_at}  ")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(payload.raw_content)
    return "\n".join(lines)


def load_ingest_payload(json_path: Path | str) -> IngestPayload:
    """Load a previously saved IngestPayload JSON file back into a model."""
    return IngestPayload.model_validate_json(Path(json_path).read_text(encoding="utf-8"))


def save_ingest_payload(payload: IngestPayload, base_dir: Path | str = INGESTED_DIR) -> SavedPaths:
    """Persist an IngestPayload as JSON + Markdown under data/ingested/{source}/.

    Files share the same {slug}_{timestamp} stem, e.g.:
      data/ingested/medium/my-article-title_20260714T120000Z.json
      data/ingested/medium/my-article-title_20260714T120000Z.md
    """
    source_dir = Path(base_dir) / payload.source
    source_dir.mkdir(parents=True, exist_ok=True)

    slug = _slugify(payload.raw_title)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{slug}_{timestamp}"

    json_path = source_dir / f"{stem}.json"
    md_path = source_dir / f"{stem}.md"

    json_path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(_build_markdown(payload), encoding="utf-8")

    return SavedPaths(json_path=json_path, md_path=md_path)
