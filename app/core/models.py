"""Shared data models for plate-production ingestion.

These models describe one extracted row from a ledger page, exactly as Claude is
asked to return it (one JSON object per physical ledger line). The same shape is
produced by every ingestion door: pasted JSON (door 1), image -> API (door 2),
and the historical Excel migration.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator


class ExtractedRow(BaseModel):
    """One ledger line as extracted from the photo (or Excel).

    Field names are English; values stay in Spanish (real business data).
    Flags default to False/None so the paste-JSON door can omit them and let
    `core.normalize` infer them from the RANGO value.
    """

    # TEXT, not int: order numbers are sometimes non-numeric (e.g. "NT 895").
    order_number: str = Field(description="PRODUCCION — the production order number")
    order_date: date = Field(description="FECHA — date of the ledger page / order")
    requested_by: str = Field(description="SOLICITADO — free text, already normalized upstream")
    plate_type: str = Field(description="TIPO — e.g. moto/carro, particular/publico")
    city: str | None = Field(default=None, description="CIUDAD")
    # RANGO: a real plate (e.g. 'HMF922'), or a special token (REPOSICION / REPITE).
    # Kept raw here; core.normalize interprets tokens into flags and may null it out.
    plate_number: str | None = Field(default=None, description="RANGO — plate, or START of a range")
    plate_to: str | None = Field(default=None, description="END of a range (consecutive plates)")
    quantity: int = Field(default=1, ge=0, description="CANTIDAD")
    material: str | None = Field(default=None, description="MATERIAL (rarely filled)")
    prepared_by: str | None = Field(default=None, description="ALISTADO POR (rarely filled)")
    is_batch_replacement: bool = Field(default=False)
    is_voided: bool = Field(default=False)
    is_plate_annulled: bool = Field(default=False)  # plate annulled, blank lost (still consumed)

    @field_validator("order_number", mode="before")
    @classmethod
    def _coerce_order_number(cls, v: object) -> object:
        # Accept numeric inputs (598, 598.0) and stringify cleanly; keep "NT 895" as-is.
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v).strip() if v is not None else v

    @field_validator("requested_by", "plate_type", mode="before")
    @classmethod
    def _strip_required_text(cls, v: object) -> object:
        # Trim surrounding whitespace early; deeper normalization happens in core.normalize.
        return v.strip() if isinstance(v, str) else v

    @field_validator("city", "plate_number", "plate_to", "material", "prepared_by", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class Extraction(BaseModel):
    """Top-level object Claude returns: a list of rows extracted from one page."""

    rows: list[ExtractedRow]
