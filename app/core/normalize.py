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


def annulment_kind(*folded_texts: str) -> str | None:
    """Classify annulment from SOLICITADO/RANGO/CIUDAD (already folded).

    Returns:
      "order" -> order annulled (or REPITE): material NOT consumed -> is_voided.
      "plate" -> plate annulled ("anulado placa"): the blank WAS lost -> consumed.
      "bare"  -> the word ANULAD with no orden/placa qualifier (often a misread):
                 treated conservatively as not consumed -> is_voided.
      None    -> not annulled.
    """
    blob = " ".join(t for t in folded_texts if t)
    if "ANULAD" not in blob and "REPITE" not in blob:
        return None
    if "ORDEN" in blob or "REPITE" in blob:
        return "order"
    if "PLACA" in blob:
        return "plate"
    return "bare"


def normalize_row(row: ExtractedRow) -> ExtractedRow:
    """Return a new row with requested_by cleaned and flags inferred.

    Annulment is recorded inconsistently in the ledger — the word ANULAD(A/O)
    shows up in SOLICITADO POR, RANGO, or CIUDAD — so we check all three.
    Respects flags already set upstream (the migration Excel may have them
    pre-applied) and only adds inferences; never clears a True flag.
    """
    data = row.model_dump()
    data["requested_by"] = normalize_requested_by(row.requested_by)

    plate_folded = _fold(row.plate_number) if row.plate_number else ""
    city_folded = _fold(row.city) if row.city else ""

    kind = annulment_kind(data["requested_by"], plate_folded, city_folded)

    if plate_folded and _is_reposicion(plate_folded):
        data["is_batch_replacement"] = True
        data["plate_number"] = None  # batch: no per-plate transcription
    elif plate_folded and ("ANULAD" in plate_folded or "REPITE" in plate_folded):
        data["plate_number"] = None  # the RANGO cell holds the token, not a plate
    else:
        # Real plate (or empty). Uppercase it for consistent dedup matching.
        data["plate_number"] = plate_folded or None

    # order/bare annulment -> not consumed (is_voided). plate annulment -> the
    # blank was lost, so it stays is_voided=False and counts as consumed.
    if kind in ("order", "bare"):
        data["is_voided"] = True

    return ExtractedRow(**data)
