"""Shared data models for plate-production ingestion.

These models describe one extracted row from a ledger page, exactly as Claude is
asked to return it (one JSON object per physical ledger line). The same shape is
produced by every ingestion door: pasted JSON (door 1), image -> API (door 2),
and the historical Excel migration.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class ExtractedRow(BaseModel):
    """One ledger line as extracted from the photo (or Excel), plus (Fase 2)
    the POS fields captured by /ingest and its future PDF automation.

    Field names are English; values stay in Spanish (real business data).
    Flags default to False/None so the paste-JSON door can omit them and let
    `core.normalize` infer them from the RANGO value. Every Fase 2 field
    below is likewise optional with a default of None, for the same reason:
    app/migrate.py (the historical Excel door) builds ExtractedRow without
    any of them and must keep working untouched.
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

    # --- Fase 2 (POS): order-level fields -- one real value per order/pedido,
    # even though (like requested_by) every row in the group repeats it; see
    # ingestion._ORDER_LEVEL_FIELDS, which flags a review_notes entry when a
    # group's rows disagree on one of these. Field names match their
    # production_orders column 1:1 (see docs/DATABASE.md / init.sql).
    receipt_type: str | None = Field(default=None, description="COMPROBANTE — tipo (FACTURA/RECIBO/OTROS)")
    receipt_number: str | None = Field(default=None, description="COMPROBANTE — número")
    payment_method: str | None = Field(default=None, description="FORMA DE PAGO / FORMAS DE PAGO")
    tracking_number: str | None = Field(default=None, description="GUÍA #")
    client_order_number: str | None = Field(
        default=None, description="PEDIDO # — reclamo de cara al cliente, distinto de order_number"
    )
    delivery_date: date | None = Field(default=None, description="FECHA ENTREGA")
    payment_date: date | None = Field(default=None, description="FECHA TRANSF./PAGO")
    dispatched_by: str | None = Field(default=None, description="RESPONSABLE DESPACHO (INGSE SAS)")
    received_by: str | None = Field(default=None, description="NOMBRE QUIEN RECIBE EL PEDIDO")
    notes: str | None = Field(default=None, description="OBSERVACIONES")

    # --- Fase 2 (POS): per-line fields -- genuinely one value per plate/
    # vehicle, unlike the order-level block above (a single order can cover
    # several vehicles), so these are deliberately EXCLUDED from
    # ingestion._ORDER_LEVEL_FIELDS. Field names match their `plates` column
    # 1:1.
    reason: str | None = Field(
        default=None, description="MOTIVO / FABRICADA POR — DETERIORO/PÉRDIDA/OTROS"
    )
    vehicle_brand: str | None = Field(default=None, description="MARCA VEHICULO/MOTO")
    # Money: Decimal, never float (see app/core/db/pricing.py). The suggested
    # plate_type_prices tariff pre-fills this client-side and it stays
    # editable (real prices vary by negotiated discount) -- whatever is set
    # here at insert time is what gets saved verbatim, never re-derived from
    # the tariff table after the fact. See the re-snapshot rule in
    # app/core/ingestion.py / app/core/db/plates.py.
    unit_price: Decimal | None = Field(default=None, ge=0, description="VALOR PLACA")

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

    @field_validator(
        "city", "plate_number", "plate_to", "material", "prepared_by",
        "receipt_type", "receipt_number", "payment_method", "tracking_number",
        "client_order_number", "dispatched_by", "received_by", "notes",
        "reason", "vehicle_brand", "unit_price",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("delivery_date", "payment_date", mode="before")
    @classmethod
    def _blank_date_to_none(cls, v: object) -> object:
        # An <input type="date"> left empty POSTs "" (not omitted), and
        # pydantic's date parser rejects "" outright -- kept as its own
        # validator (rather than folded into _blank_to_none above) because it
        # applies to typed `date` fields, not the free-text ones that
        # validator covers. delivery_date is deliberately NEVER checked
        # against "no future date" (unlike order_date, see
        # app/web/helpers.py._validate_order_header) -- a future delivery
        # date is normal and expected, not a data-entry mistake.
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class Extraction(BaseModel):
    """Top-level object Claude returns: a list of rows extracted from one page."""

    rows: list[ExtractedRow]
