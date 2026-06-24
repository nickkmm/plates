"""Historical migration: normalized Excel -> production_orders + plates.

Reuses the exact same backend (core.process_rows) as the web ingestion, so the
business rules are guaranteed identical. Does NOT call the Anthropic API.

Usage:
    python -m app.migrate /data/historico.xlsx [--sheet Hoja1]

Assumed columns (confirm/override with --columns if they differ):
    FECHA, PRODUCCION, SOLICITADO, TIPO, RANGO, CIUDAD, CANTIDAD
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
    "PRODUCCION": "order_number",
    "SOLICITADO": "requested_by",
    "TIPO": "plate_type",
    "RANGO": "plate_number",
    "CIUDAD": "city",
    "CANTIDAD": "quantity",
}


def _to_row(record: dict) -> ExtractedRow:
    raw = {field: record.get(col) for col, field in COLUMN_MAP.items()}
    # pandas uses NaN for blanks; turn those into None.
    for k, v in list(raw.items()):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            raw[k] = None
    # order_date may come as a pandas Timestamp.
    if hasattr(raw.get("order_date"), "date"):
        raw["order_date"] = raw["order_date"].date()
    if raw.get("quantity") is None:
        raw["quantity"] = 1
    return ExtractedRow(**raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate normalized Excel into Postgres.")
    parser.add_argument("excel_path")
    parser.add_argument("--sheet", default=0, help="Sheet name or index (default: first).")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report only; no insert.")
    args = parser.parse_args(argv)

    df = pd.read_excel(args.excel_path, sheet_name=args.sheet)
    missing = [c for c in COLUMN_MAP if c not in df.columns]
    if missing:
        print(f"ERROR: missing columns {missing}. Found: {list(df.columns)}", file=sys.stderr)
        return 2

    rows: list[ExtractedRow] = []
    unmapped: list[tuple[int, str]] = []
    for i, record in enumerate(df.to_dict(orient="records"), start=2):  # +2 ≈ Excel row number
        try:
            rows.append(_to_row(record))
        except (ValidationError, ValueError, TypeError) as e:
            unmapped.append((i, str(e).splitlines()[0]))

    print(f"Parsed {len(rows)} rows; {len(unmapped)} could not be mapped.")
    for line, reason in unmapped:
        print(f"  Excel row ~{line}: {reason}")

    if args.dry_run:
        print("Dry run — nothing inserted.")
        return 0

    summary = process_rows(rows)
    print("\n=== Migration summary ===")
    print(f"Orders inserted:        {summary.orders_inserted}")
    print(f"Plates inserted:        {summary.plates_inserted}")
    print(f"Possible duplicates:    {summary.possible_duplicates}")
    print(f"Voided:                 {summary.voided}")
    print(f"Batch replacements:     {summary.batch_replacements}")
    if summary.review_notes:
        print("Review notes:")
        for n in summary.review_notes:
            print(f"  - {n}")
    if unmapped:
        print(f"\n⚠ {len(unmapped)} rows were NOT inserted (see above) — review them manually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
