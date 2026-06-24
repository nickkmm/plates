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

from app.core.models import ExtractedRow
from app.core.process import process_rows

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
    try:
        raw["quantity"] = 1 if raw.get("quantity") is None else int(float(raw["quantity"]))
    except (ValueError, TypeError):
        raw["quantity"] = 1  # non-numeric CANT (e.g. "ANULADO") -> default 1

    for f in ("requested_by", "plate_type", "plate_number", "city", "material", "prepared_by"):
        if raw.get(f) is not None:
            raw[f] = str(raw[f])

    return ExtractedRow(**raw)


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

    summary = process_rows(rows)
    print("\n=== Migration summary ===")
    print(f"Orders inserted:        {summary.orders_inserted}")
    print(f"Plates inserted:        {summary.plates_inserted}")
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
