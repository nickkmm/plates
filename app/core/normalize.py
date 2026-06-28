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


# The plate TYPE stored per plate (granular, what the user picks in the form).
PLATE_TYPES = (
    "MOTO PARTICULAR", "CARRO PARTICULAR",
    "MOTO CARRO PARTICULAR", "MOTO CARRO PÚBLICO",
    "CARRO PÚBLICO", "REMOLQUE", "ORNAMENTAL",
)
# The INVENTORY materials (blanks) a type discounts from.
INVENTORY_CATEGORIES = ("CARRO PARTICULAR", "MOTO PARTICULAR", "PÚBLICO")

# Type -> material grouping:
_PUBLICO_TYPES = ("CARRO PÚBLICO", "MOTO CARRO PÚBLICO", "REMOLQUE")  # share the público blank
_NON_MATERIAL_TYPES = ("ORNAMENTAL",)  # consume no blank; just a counter


def inventory_category(plate_type: str | None) -> str | None:
    """Map a plate TYPE to the inventory MATERIAL it consumes (None = no material).

    - moto carro particular -> MOTO PARTICULAR
    - carro/moto-carro público + remolque -> PÚBLICO
    - ornamental -> None (counted separately, discounts nothing)
    """
    if plate_type in _NON_MATERIAL_TYPES:
        return None
    if plate_type in _PUBLICO_TYPES:
        return "PÚBLICO"
    if plate_type == "MOTO CARRO PARTICULAR":
        return "MOTO PARTICULAR"
    return plate_type  # MOTO PARTICULAR, CARRO PARTICULAR


# Plate format per TYPE (Colombian plates).
PLATE_PATTERNS = {
    "MOTO PARTICULAR": re.compile(r"[A-Z]{3}\d{2}[A-Z]"),       # AAA00A  e.g. KWD91G
    "CARRO PARTICULAR": re.compile(r"[A-Z]{3}\d{3}"),            # AAA000  e.g. HMF922
    "CARRO PÚBLICO": re.compile(r"[A-Z]{3}\d{3}"),               # AAA000
    "MOTO CARRO PARTICULAR": re.compile(r"\d{3}[A-Z]{3}"),       # 000AAA  e.g. 923NAA
    "MOTO CARRO PÚBLICO": re.compile(r"\d{3}[A-Z]{3}"),          # 000AAA
    "REMOLQUE": re.compile(r"[RS]\d{5}"),                        # R/S + 5 dígitos
    "ORNAMENTAL": re.compile(r"[A-Z]{3}\d{3}"),                  # AAA000 (forma de carro)
}

_PLATE_DESC = {
    "MOTO PARTICULAR": "3 letras, 2 números y 1 letra (ej. KWD91G)",
    "CARRO PARTICULAR": "3 letras y 3 números (ej. HMF922)",
    "CARRO PÚBLICO": "3 letras y 3 números (ej. HMF922)",
    "MOTO CARRO PARTICULAR": "3 números y 3 letras (ej. 923NAA)",
    "MOTO CARRO PÚBLICO": "3 números y 3 letras (ej. 923NAA)",
    "REMOLQUE": "R o S y 5 números (ej. R12345)",
    "ORNAMENTAL": "3 letras y 3 números (ej. HMF922)",
}


def normalize_plate_type(value: str | None) -> str | None:
    """Canonical plate TYPE. Keeps types granular; material grouping is separate.

    Maps the historical label 'MOTO PÚBLICO' to today's 'MOTO CARRO PÚBLICO'.
    """
    if not value:
        return value
    f = _fold(value)  # uppercase, accent-stripped, single-spaced
    if "ORNAMENTAL" in f:
        return "ORNAMENTAL"
    if "REMOLQUE" in f:
        return "REMOLQUE"
    if "MOTO CARRO" in f or f.replace(" ", "").startswith("MOTOCARRO"):
        return "MOTO CARRO PARTICULAR" if "PARTICULAR" in f else "MOTO CARRO PÚBLICO"
    if f == "MOTO PUBLICO":          # historical label for the público moto-carro
        return "MOTO CARRO PÚBLICO"
    if "PUBLIC" in f:                # "CARRO PÚBLICO", or a bare "PÚBLICO"
        return "CARRO PÚBLICO"
    return f                          # MOTO PARTICULAR, CARRO PARTICULAR


def clean_plate(value: str) -> str:
    """Uppercase and remove spaces so 'arn 911' -> 'ARN911'."""
    return _WHITESPACE.sub("", value.strip().upper())


def validate_plate(value: str, plate_type: str) -> str:
    """Return the cleaned plate if it matches its category format, else raise.

    Used at creation time (the form); historical migration is not validated.
    """
    cleaned = clean_plate(value)
    pattern = PLATE_PATTERNS.get(plate_type)
    if pattern and not pattern.fullmatch(cleaned):
        raise ValueError(
            f"La placa '{value}' no tiene el formato de {plate_type}: "
            f"{_PLATE_DESC.get(plate_type, '')}."
        )
    return cleaned


def normalize_city(value: str | None) -> str | None:
    """Uppercase + single-space the city so 'Cali' and 'CALI' collapse into one.

    Keeps accents (BOGOTÁ). Returns None for blanks or punctuation-only junk ('-', '//').
    """
    if not value:
        return None
    v = _WHITESPACE.sub(" ", value.strip().upper())
    if not any(ch.isalnum() for ch in v):
        return None
    return v or None


def _is_reposicion(folded: str) -> bool:
    return "REPOSICION" in folded


# Values that are NOT unique plate identifiers, so they must not be treated as
# duplicates even when they repeat (samples, internal markers, serial notes).
_NON_PLATE_EXACT = {"MUESTRA", "MUESTRAS", "INGSE", "KWP"}


def is_real_plate(plate_number: str | None) -> bool:
    """True only for values that identify a unique plate (eligible for dedup).

    Excludes None, sample/internal markers, and serial notes like 'S/43050'.
    """
    if not plate_number:
        return False
    f = plate_number.strip().upper()
    if f in _NON_PLATE_EXACT or f.startswith("S/"):
        return False
    return True


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
    data["plate_type"] = normalize_plate_type(row.plate_type)  # merge público variants
    data["city"] = normalize_city(row.city)
    data["plate_to"] = _fold(row.plate_to) if row.plate_to else None  # range end, uppercased

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

    # If the annulment was written in the CIUDAD cell, that's not a real city.
    if city_folded and "ANULAD" in city_folded:
        data["city"] = None

    return ExtractedRow(**data)
