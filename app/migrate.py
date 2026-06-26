"""Historical migration: normalized Excel -> production_orders + plates.

Reuses the exact same backend (core.process_rows) as the web ingestion, so the
business rules are guaranteed identical. Does NOT call the Anthropic API.

The source file (BASE_DE_DATOS_PRODUCCION_PLACAS_normalizado.xlsx) has the data
on sheet "BD " with the header on the SECOND row and a leading empty column.

Usage:
    python -m app.migrate /data/BASE_DE_DATOS_PRODUCCION_PLACAS_normalizado.xlsx
    python -m app.migrate /data/file.xlsx --sheet "BD " --header-row 1 --dry-run
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd
from pydantic import ValidationError

from app.core import db
from app.core.models import ExtractedRow
from app.core.process import process_rows

# Categories tracked in inventory (must match TIPO values and Hoja2 headers).
INVENTORY_CATEGORIES = ["MOTO PARTICULAR", "CARRO PARTICULAR", "CARRO PÚBLICO", "MOTO PÚBLICO"]

# Excel header -> ExtractedRow field.
COLUMN_MAP = {
    "FECHA": "order_date",
    "ORDEN DE PRODUCCION": "order_number",
    "SOLICITADO POR": "requested_by",
    "TIPO": "plate_type",
    "RANGO": "plate_number",
    "CIUDAD": "city",
    "CANT": "quantity",
    "MATERIAL": "material",
    "ALISTADO POR": "prepared_by",
}


def _clean(v):
    """NaN/empty -> None."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, str) and not v.strip():
        return None
    return v


def _to_row(record: dict) -> ExtractedRow:
    raw = {field: _clean(record.get(col)) for col, field in COLUMN_MAP.items()}

    if hasattr(raw.get("order_date"), "date"):
        raw["order_date"] = raw["order_date"].date()
    # order_number stays text — the model coerces 598.0 -> "598" and keeps "NT 895".
    # Quantity is in CANT for plates, but in MATERIAL for ALISTAMIENTO rows — fall back.
    qty = raw.get("quantity")
    if qty is None:
        qty = raw.get("material")
    try:
        raw["quantity"] = int(float(qty)) if qty is not None else 1
    except (ValueError, TypeError):
        raw["quantity"] = 1  # non-numeric (e.g. "ANULADO", "ORNAMENTAL") -> default 1

    for f in ("requested_by", "plate_type", "plate_number", "city", "material", "prepared_by"):
        if raw.get(f) is not None:
            raw[f] = str(raw[f])

    return ExtractedRow(**raw)


def read_initial_stock(path: str):
    """Read the 'alistado' baseline per category + its date from the Hoja2 sheet.

    Returns (stock: dict[category, qty], base_date or None).
    """
    df = pd.read_excel(path, sheet_name="Hoja2", header=1)
    first = df.iloc[0]
    stock = {}
    for cat in INVENTORY_CATEGORIES:
        if cat in df.columns and pd.notna(first.get(cat)):
            stock[cat] = int(first[cat])
    base_date = first.get("FECHA")
    base_date = base_date.date() if hasattr(base_date, "date") else None
    return stock, base_date


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate normalized Excel into Postgres.")
    parser.add_argument("excel_path")
    parser.add_argument("--sheet", default="BD ", help='Sheet name (default: "BD ").')
    parser.add_argument("--header-row", type=int, default=1, help="0-based header row (default: 1).")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report only; no insert.")
    args = parser.parse_args(argv)

    df = pd.read_excel(args.excel_path, sheet_name=args.sheet, header=args.header_row)
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]  # drop empty index col

    missing = [c for c in COLUMN_MAP if c not in df.columns]
    if missing:
        print(f"ERROR: missing columns {missing}. Found: {list(df.columns)}", file=sys.stderr)
        return 2

    # In the physical ledger, group-level fields are written once and left blank
    # on continuation rows (e.g. an order with many plates of the same TIPO).
    # Drop fully-empty separator rows, then forward-fill those columns so
    # continuation rows inherit their order's values instead of being dropped.
    df = df.dropna(how="all")

    # Drop "empty" ledger lines that carry no real content: no order number, no
    # plate (RANGO), and no quantity. These leftover/half-filled rows would
    # otherwise be swept into the previous order by the forward-fill below
    # (creating phantom plates).
    def _has_content(r):
        cant = r["CANT"]
        return not (
            pd.isna(r["ORDEN DE PRODUCCION"])
            and pd.isna(r["RANGO"])
            and (pd.isna(cant) or cant == 0)
        )

    df = df[df.apply(_has_content, axis=1)]

    for c in ["FECHA", "ORDEN DE PRODUCCION", "SOLICITADO POR", "TIPO"]:
        df[c] = df[c].ffill()

    rows: list[ExtractedRow] = []
    unmapped: list[tuple[int, str]] = []
    for i, record in enumerate(df.to_dict(orient="records"), start=args.header_row + 2):
        try:
            rows.append(_to_row(record))
        except (ValidationError, ValueError, TypeError) as e:
            unmapped.append((i, str(e).splitlines()[0]))

    print(f"Parsed {len(rows)} rows OK; {len(unmapped)} could not be mapped (skipped).")
    for line, reason in unmapped[:60]:
        print(f"  Excel row ~{line}: {reason}")
    if len(unmapped) > 60:
        print(f"  ... and {len(unmapped) - 60} more.")

    if args.dry_run:
        print("\nDry run — nothing inserted.")
        return 0

    # The Feb baseline (Hoja2) is loaded as the first ALISTAMIENTO events, so
    # "alistado" includes it and available = prepared - consumed.
    stock, base_date = read_initial_stock(args.excel_path)
    print(f"Loading baseline alistado from Hoja2 ({base_date}): {stock}")
    with db.get_conn() as conn:
        for category, qty in stock.items():
            db.insert_blank_preparation(
                conn, prep_date=base_date, category=category,
                quantity=qty, prepared_by="INICIAL (Hoja2)",
            )
        conn.commit()

    summary = process_rows(rows)
    print("\n=== Migration summary ===")
    print(f"Orders inserted:        {summary.orders_inserted}")
    print(f"Plates inserted:        {summary.plates_inserted}")
    print(f"Blank preparations:     {summary.blank_preparations}")
    print(f"Possible duplicates:    {summary.possible_duplicates}")
    print(f"Voided:                 {summary.voided}")
    print(f"Batch replacements:     {summary.batch_replacements}")
    if summary.review_notes:
        print(f"Review notes: {len(summary.review_notes)} (multiple requested_by per order)")
    if unmapped:
        print(f"\n⚠ {len(unmapped)} rows were NOT inserted — review them manually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
