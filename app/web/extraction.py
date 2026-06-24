"""Door 2: extract ledger rows from a photo via the Anthropic API.

Only used when ANTHROPIC_API_KEY is set. Door 1 (paste JSON) needs none of this.
The model is configurable via CLAUDE_MODEL (default claude-sonnet-4-6); switch to
claude-opus-4-8 for harder, denser handwriting without touching code.
"""

from __future__ import annotations

import base64
import os

import anthropic

from app.core.models import Extraction

DEFAULT_MODEL = "claude-sonnet-4-6"

# Kept in sync with the prompt saved in the claude.ai Project used by door 1.
EXTRACTION_PROMPT = """You are transcribing a photo of a handwritten plate-production ledger from \
INGSE (Colombia). Each physical line has these columns:
FECHA (date) | PRODUCCION (order number) | SOLICITADO (who requested) | TIPO (plate type) | \
RANGO (the plate, or a special word) | CIUDAD (city) | CANTIDAD (quantity).

Return ONE object per ledger line with these fields:
- order_number (integer, from PRODUCCION)
- order_date (YYYY-MM-DD, from FECHA)
- requested_by (text, from SOLICITADO — keep the name exactly as written)
- plate_type (text, from TIPO)
- city (text or null, from CIUDAD)
- plate_number (text or null, from RANGO — copy it verbatim, including special \
words like "REPOSICION" or "REPITE")
- quantity (integer, from CANTIDAD; default 1 if blank)
- is_batch_replacement (boolean; leave false — it is inferred downstream)
- is_voided (boolean; leave false — it is inferred downstream)

Transcribe faithfully. Do not invent rows. If a cell is illegible, use your best \
reading. The data is in Spanish; keep it in Spanish."""

_MEDIA_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


def api_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _media_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _MEDIA_TYPES.get(ext, "image/jpeg")


def extract_from_image(image_bytes: bytes, filename: str) -> Extraction:
    """Call Claude with the photo and return a validated Extraction."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    model = os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL)
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        output_format=Extraction,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _media_type(filename),
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": EXTRACTION_PROMPT},
                ],
            }
        ],
    )
    return response.parsed_output
