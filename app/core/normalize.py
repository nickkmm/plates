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
from typing import TypeGuard

from app.core.models import ExtractedRow

# Curated requested_by normalization, confirmed manually with the user (the same
# decisions used to clean the first ledger; see data/normalize.py). Maps known
# spelling variants of the SAME intermediary to one canonical form. Entities that
# are genuinely different are deliberately NOT merged (e.g. MUVA GRANADA vs MUVA
# SERGIO, DIANA.P vs DIANA.Z, MAGDA vs MAGDA ISABEL RUBIANO, each CDA/GRANADA branch).
# Keys are matching-normalized (uppercase, accent-stripped, no dots/spaces); the
# canonical short form (NAME.INITIAL) is used only when it already existed in the data.
_REQUESTED_BY_MAP_RAW = {
    "PTOV": "PTO.V", "PTODEVENTA": "PTO.V", "PTOVENTA": "PTO.V", "PUNTODEVENTA": "PTO.V",
    "DIANAP": "DIANA.P", "DIANAPAREDES": "DIANA.P",
    "DIANAZ": "DIANA.Z", "DIANAZAMORA": "DIANA.Z", "DIANAR": "DIANA.R",
    "TSANTAELENA": "T.SANTA ELENA", "TSTAELENA": "T.SANTA ELENA",
    "STAELENA": "T.SANTA ELENA", "SANTAELENA": "T.SANTA ELENA",
    "JENNYR": "JENNY.R", "JENNYROJAS": "JENNY.R", "YENYROJAS": "JENNY.R",
    "JENIROJASPINO": "JENNY.R", "JENNYVR": "JENNY.R",
    "GRANADAL": "GRANADA.L", "GRANADAC": "GRANADA.C",
    "WILTHER": "WILTHER.O", "WILTHERO": "WILTHER.O",
    "CALISUR": "CALISUR", "CALISURV": "CALISUR", "CALISURVILLA": "CALISUR", "CALISURPTO": "CALISUR",
    "SINTEL": "SINTEL", "SINTELSAS": "SINTEL",
    "CARLOSV": "CARLOS.V", "DPOLLO": "D.POLLO",
    "FLORENCIAL": "FLORENCIA.L", "FLORENCIA": "FLORENCIA.L", "FLORENCIALISBEP": "FLORENCIA.L",
    "ADRIANV": "ADRIAN.V", "ADRIAN": "ADRIAN.V", "ADRIANOV": "ADRIAN.V", "ADRINAV": "ADRIAN.V",
    "ADRIANAV": "ADRIANA.V",
    "BERTHAT": "BERTHA.T", "BERTHATAVARES": "BERTHA.T",
    "ONEYDA": "ONEYDA VELEZ", "ONEYDAVELEZ": "ONEYDA VELEZ",
    "ANDALUCIA": "CARLOS ANDALUCIA", "CARLOSANDALUCIA": "CARLOS ANDALUCIA",
    "TUQUERRES": "TUQUERRES", "TUQUERRESR": "TUQUERRES",
    "CREARO": "CREAR.O", "CREARDEOCC": "CREAR.O",
    "EMILIOMONTE": "EMILIO.MONTE", "LINAP": "LINA.P",
    # Variants confirmed from the live ledger (June 2026 data, not in the first
    # cleaning script). SERGIO and qualified PTO.V points of sale are left separate
    # on purpose, pending review.
    "D.WELLMAN": "D.WILLIAM", "D.WILLIAN": "D.WILLIAM",
    "INELMA SAS": "INELMA", "INELMA SRS": "INELMA",
    "ING CAMELITA": "ING CARMELITA",
    "ADRIANA": "ADRIANA.V",
    "JUAN CALI": "JUAN.CALI", "JUAN MEJIA": "JUAN.M",
    "ALFREDO VILLAVICENCIO": "ALFREDO.V",
    "ING.INCAUCA": "INCAUCA", "ING.RIOPAILA": "RIOPAILA", "ING.PROVIDENCIA": "PROVIDENCIA",
    "ALIRIO PTO ASS": "ALIRIO.P",
    "ANGEL PINO": "ANGEL.P",
    "JAIRO LUDEGAS": "JAIRO.L",
    "MARIO TUQUERRES": "MARIO.T",
    "WILTHER APARA": "WILTHER.O",
    "GPS SKY": "GPS",
    # Confirmed 2026-08-08: same entity, word order swapped (10 orders as "MUVA
    # SERGIO" vs 1 as "SERGIO MUVA"). SERGIO.M/SERGIO.N and the other qualified
    # PTO.V sub-points are still deliberately left separate, unconfirmed.
    "SERGIO MUVA": "MUVA SERGIO",
    # Confirmed 2026-08-15: typo/variant of PTO.V, not a real separate sub-point
    # (unlike PTO.V FABIO M / PTO.V.LUIS L / PTO.V STA ANITA / PTO.VHSA, which
    # ARE real and stay separate).
    "PTO.V VULKAN": "PTO.V",
}

_WHITESPACE = re.compile(r"\s+")


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def _fold(text: str) -> str:
    """Uppercase, accent-stripped, single-spaced — for token matching only."""
    return _WHITESPACE.sub(" ", _strip_accents(text).upper()).strip()


_DOTS_SPACES = re.compile(r"[.\s]+")
_REDUNDANT_DOT = re.compile(r"\s*\.\s*")
_TRAILING_DOT = re.compile(r"\.+$")
_NUMERIC = re.compile(r"\d+(\.0)?")


def _rb_key(text: str) -> str:
    """Matching key for requested_by: uppercase, accent-stripped, no dots/spaces."""
    return _DOTS_SPACES.sub("", _strip_accents(text).upper())


# Derived once: map keyed by the robust matching key (collapses accent variants).
_REQUESTED_BY_MAP = {_rb_key(k): v for k, v in _REQUESTED_BY_MAP_RAW.items()}


def _normalize_spelling(value: str) -> str:
    """Generic spelling cleanup (case, spaces, dots) WITHOUT merging entities.

    Keeps accents (BOGOTÁ, GUTIÉRREZ). 'X . Y' -> 'X.Y'; drops a dangling dot.
    """
    s = _REDUNDANT_DOT.sub(".", value.strip().upper())
    s = _TRAILING_DOT.sub("", s)
    return _WHITESPACE.sub(" ", s).strip()


def normalize_requested_by(value: str) -> str:
    """Canonical requested_by.

    Maps known spelling variants of the SAME intermediary to one curated form
    (see _REQUESTED_BY_MAP_RAW); otherwise just cleans spelling. Distinct
    intermediaries are never merged. Numeric runaways (a misaligned row that put a
    number in this column) are left untouched.
    """
    s = str(value).strip()
    if not s:
        return value
    if _NUMERIC.fullmatch(s):
        return value
    key = _rb_key(s)
    if key in _REQUESTED_BY_MAP:
        return _REQUESTED_BY_MAP[key]
    return _normalize_spelling(s)


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
    "MOTO PARTICULAR": re.compile(r"[A-Z]{3}\d{2}[A-Z]?"),       # AAA00A or AAA00 (old motos had no trailing letter)
    "CARRO PARTICULAR": re.compile(r"[A-Z]{3}\d{3}"),            # AAA000  e.g. HMF922
    "CARRO PÚBLICO": re.compile(r"[A-Z]{3}\d{3}"),               # AAA000
    "MOTO CARRO PARTICULAR": re.compile(r"\d{3}[A-Z]{3}"),       # 000AAA  e.g. 923NAA
    "MOTO CARRO PÚBLICO": re.compile(r"\d{3}[A-Z]{3}"),          # 000AAA
    "REMOLQUE": re.compile(r"[RS]\d{5}"),                        # R/S + 5 dígitos
    "ORNAMENTAL": re.compile(r"[A-Z]{3}\d{3}"),                  # AAA000 (forma de carro)
}

_PLATE_DESC = {
    "MOTO PARTICULAR": "3 letras y 2 números, con o sin 1 letra al final (ej. KWD91G o KWD91)",
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


# Fase 2 (POS) fixed vocabularies -- constant NAMES are English (code/schema
# identifiers, per CLAUDE.md), the VALUES are the Spanish business terms
# staff actually type/see. Every one of these is a <select> in the /ingest
# form (Fase 3), never free text, so normalize_choice below only ever needs
# to reconcile case/accent variance, never invent a value it wasn't given.
RECEIPT_TYPES = ("FACTURA", "RECIBO", "OTROS")
CUSTOMER_TYPES = ("PERSONA NATURAL", "TRÁNSITO", "EMPRESA", "OTROS")
DOCUMENT_TYPES = ("NIT", "CÉDULA", "OTROS")
# The plan's draft data had "POSEDOR" -- a typo; the real word is POSEEDOR.
DOCUMENT_RELATIONS = ("DUEÑO", "POSEEDOR", "OTROS")
PAYMENT_METHODS = ("EFECTIVO", "TRANSFERENCIA", "CRÉDITO", "OTROS")
REASONS = ("DETERIORO", "PÉRDIDA", "OTROS")


def normalize_choice(value: str | None, allowed: tuple[str, ...]) -> str | None:
    """Match `value` against a fixed vocabulary (RECEIPT_TYPES, CUSTOMER_TYPES,
    DOCUMENT_TYPES, DOCUMENT_RELATIONS, PAYMENT_METHODS, REASONS, ...),
    returning the CANONICAL spelling from `allowed` or None.

    Blank/None -> None. Otherwise compares fold()ed (case/accent-insensitive)
    against each option in `allowed` and returns that option's real spelling
    verbatim on a match. No match -> None -- this NEVER guesses/invents a
    value, same "faithful, not clever" spirit as is_real_plate/pdf_extract:
    an unrecognized value is the caller's job to surface (e.g. as a PDF
    extraction notice) so a human picks the right one from the dropdown,
    rather than silently coercing it to something that might be wrong.
    """
    if not value:
        return None
    folded = _fold(value)
    for option in allowed:
        if _fold(option) == folded:
            return option
    return None


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


# Only these two types have genuinely city-specific plates. Every other type in
# PLATE_TYPES is implicitly nationwide ("COLOMBIA") — which is exactly why CIUDAD
# is blank for most of them in the historical ledger (88% blank for MOTO
# PARTICULAR). Used as an inference-on-blank rule ONLY: a row of another type
# that does carry a real city keeps it untouched.
_CITY_SPECIFIC_TYPES = ("CARRO PARTICULAR", "CARRO PÚBLICO")
_IMPLICIT_CITY = "COLOMBIA"


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


def is_real_plate(plate_number: str | None) -> TypeGuard[str]:
    """True only for values that identify a unique plate (eligible for dedup).

    Excludes None, sample/internal markers, and serial notes like 'S/43050'.
    Typed as a TypeGuard so callers filtering with this (e.g. process.py's
    comprehensions) get `str` narrowing instead of `str | None`.
    """
    if not plate_number:
        return False
    f = plate_number.strip().upper()
    return not (f in _NON_PLATE_EXACT or f.startswith("S/"))


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
    # blank was lost, so it stays is_voided=False and counts as consumed, but
    # flagged for waste tracking (mirrors the web form's dedicated "Placa
    # anulada" line type, which sets is_plate_annulled directly instead of
    # inferring it from ledger text).
    if kind in ("order", "bare"):
        data["is_voided"] = True
    elif kind == "plate":
        data["is_plate_annulled"] = True

    # If the annulment was written in the CIUDAD cell, that's not a real city.
    if city_folded and "ANULAD" in city_folded:
        data["city"] = None

    # Types other than CARRO PARTICULAR/PÚBLICO are nationwide, so a blank CIUDAD
    # means COLOMBIA rather than "unknown". Inference on blank only — a row of one
    # of those types that DOES name a real city (FLORENCIA, GRANADA, ...) keeps it.
    # Evaluated last, so it also covers a city cell emptied by the ANULAD rule above.
    if data["plate_type"] not in _CITY_SPECIFIC_TYPES and not data["city"]:
        data["city"] = _IMPLICIT_CITY

    return ExtractedRow(**data)
