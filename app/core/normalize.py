"""Normalization and business-rule inference for ledger rows.

Pure functions only (no DB access) so they are trivial to unit-test and are
shared identically by the web ingestion doors and the Excel migration.

Two responsibilities:
  1. Normalize `requested_by` text conservatively (casing/whitespace + a few
     known tokens) WITHOUT merging distinct intermediaries
     (e.g. "MUVA GRANADA" and "MUVA SERGIO" must stay separate).
  2. Infer special flags from the RANGO value:
       REPOSICION  -> is_batch_replacement = True, plate_number = None
       REPITE / anulada -> is_voided = True
"""

from __future__ import annotations

import re
import unicodedata

from app.core.models import ExtractedRow

# Known canonical tokens for requested_by. These are exact-value replacements
# only — anything not listed is left as-is (just cleaned), so distinct
# intermediaries are never collapsed into one another.
_REQUESTED_BY_ALIASES = {
    "PTO V": "PTO.V",
    "PTO.V": "PTO.V",
    "PTOV": "PTO.V",
    "PUNTO DE VENTA": "PTO.V",
    "INGSE": "INGSE",
}

_WHITESPACE = re.compile(r"\s+")


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def _fold(text: str) -> str:
    """Uppercase, accent-stripped, single-spaced — for token matching only."""
    return _WHITESPACE.sub(" ", _strip_accents(text).upper()).strip()


def normalize_requested_by(value: str) -> str:
    """Conservative normalization: clean whitespace/casing, map known tokens.

    Never merges two different free-text names — only canonicalizes exact
    matches in the alias table.
    """
    folded = _fold(value)
    if folded in _REQUESTED_BY_ALIASES:
        return _REQUESTED_BY_ALIASES[folded]
    return folded


def _is_reposicion(folded: str) -> bool:
    return "REPOSICION" in folded


def _is_voided(folded: str) -> bool:
    # "REPITE" (repeated by mistake) or an explicit annulment note.
    return "REPITE" in folded or "ANULAD" in folded


def normalize_row(row: ExtractedRow) -> ExtractedRow:
    """Return a new row with requested_by cleaned and flags inferred from RANGO.

    Respects flags already set upstream (the migration Excel may have them
    pre-applied) and only adds inferences; never clears a True flag.
    """
    data = row.model_dump()
    data["requested_by"] = normalize_requested_by(row.requested_by)

    raw_plate = (row.plate_number or "").strip()
    folded = _fold(raw_plate) if raw_plate else ""

    if folded and _is_reposicion(folded):
        data["is_batch_replacement"] = True
        data["plate_number"] = None  # batch: no per-plate transcription
    elif folded and _is_voided(folded):
        data["is_voided"] = True
        # The RANGO cell holds the token, not a real plate number.
        data["plate_number"] = None
    else:
        # Real plate (or empty). Uppercase it for consistent dedup matching.
        data["plate_number"] = folded or None

    return ExtractedRow(**data)
