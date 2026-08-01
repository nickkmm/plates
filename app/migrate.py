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
from datetime import date

import pandas as pd
from pydantic import ValidationError

from app.core import db
from app.core.models import ExtractedRow
from app.core.process import _is_alistado, process_rows

# Alistamiento (blanks prepared) reconciled against the physical book, Feb–Jun 2026.
# This REPLACES the Excel's ALISTAMIENTO rows, which were unreliable: MAT-only rows
# silently dropped by the filter, misreads (7300 -> 2x1300), duplicates (823 x2,
# 1370 -> 1310), and a wrong Hoja2 baseline. The first three rows are the corrected
# initial baseline. Source of truth: data/alistamiento_conciliacion.md.
#   (date, category, quantity, prepared_by)
RECONCILED_ALISTAMIENTO = [
    ("2026-02-23", "CARRO PARTICULAR", 362, "INICIAL"),
    ("2026-02-23", "MOTO PARTICULAR", 1486, "INICIAL"),
    ("2026-02-23", "PÚBLICO", 731, "INICIAL"),
    ("2026-03-27", "CARRO PARTICULAR", 550, "GILMER GUTIÉRREZ"),
    ("2026-03-27", "MOTO PARTICULAR", 1300, "GILMER GUTIÉRREZ"),
    ("2026-03-30", "MOTO PARTICULAR", 1300, "GILMER GUTIÉRREZ"),
    ("2026-04-01", "CARRO PARTICULAR", 376, None),
    ("2026-04-21", "MOTO PARTICULAR", 1300, "GILMER GUTIÉRREZ"),
    ("2026-05-13", "CARRO PARTICULAR", 200, "GILMER GUTIÉRREZ"),
    ("2026-05-16", "CARRO PARTICULAR", 100, "GILMER GUTIÉRREZ"),
    ("2026-05-22", "CARRO PARTICULAR", 825, "GILMER GUTIÉRREZ"),
    ("2026-05-22", "MOTO PARTICULAR", 1300, "GILMER GUTIÉRREZ"),
    ("2026-05-22", "MOTO PARTICULAR", 1300, "GILMER GUTIÉRREZ"),
    ("2026-05-22", "PÚBLICO", 100, "GILMER GUTIÉRREZ"),
    ("2026-06-03", "PÚBLICO", 823, "GILMER GUTIÉRREZ"),
    ("2026-06-05", "MOTO PARTICULAR", 1310, "GILMER GUTIÉRREZ"),
    ("2026-06-24", "MOTO PARTICULAR", 1310, "GILMER GUTIÉRREZ"),  # confirmed from the live ledger
]

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


def _shift_future_dates(dates: pd.Series, today: pd.Timestamp) -> tuple[pd.Series, int]:
    """Shift year-typo future dates back one year (e.g. order 1068 written
    2027-03-28 instead of 2026 -> 2026-03-28).

    Returns (corrected_series, count_corrected). The comparison mask is built
    from a coerced copy (so unparseable values compare as NaT, i.e. not
    future) but the subtraction is applied to the ORIGINAL series, matching
    the exact semantics `main()` used before this was factored out.
    """
    future = pd.to_datetime(dates, errors="coerce") > today
    shifted = dates.copy()
    if int(future.sum()):
        # pandas-stubs has no Series-minus-DateOffset overload for a Series of
        # unspecified (object/mixed) dtype, though this is standard, valid
        # pandas at runtime.
        shifted.loc[future] = shifted.loc[future] - pd.DateOffset(years=1)  # type: ignore[operator]
    return shifted, int(future.sum())


def _key_val(v: object) -> str:
    """Type-normalized dedup key so 598 == '598' and 2.0 == 2 collapse (the
    live ledger mixes int/text and int/float for the same logical value)."""
    if pd.isna(v):  # type: ignore[call-overload]  # pd.isna accepts any scalar at runtime
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip().upper()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate normalized Excel into Postgres.")
    parser.add_argument("excel_path")
    parser.add_argument("--sheet", default="BD ", help='Sheet name (default: "BD ").')
    parser.add_argument("--header-row", type=int, default=1, help="0-based header row (default: 1).")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report only; no insert.")
    parser.add_argument("--keep-duplicates", action="store_true",
                        help="Do not drop exact-duplicate ledger lines (debugging).")
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

    # Future-dated rows are year typos (e.g. order 1068 written 2027-03-28 instead
    # of 2026). Shift them back one year so they land on the real (past) date.
    today = pd.Timestamp(date.today())
    df["FECHA"], n_shifted = _shift_future_dates(df["FECHA"], today)
    if n_shifted:
        print(f"Corrected {n_shifted} future-dated rows (year typo, shifted back 1 year).")

    # The photo-extraction agent transcribes some ledger pages twice, producing
    # exact-duplicate lines (same fecha/orden/tipo/rango/cant). A real plate can't
    # be made twice and identical reposición/muestra lines are double-reads, so we
    # drop exact duplicates (keep the first). Annulments/remakes carry different
    # RANGO text, so they survive. The key is type-normalized so 598 == "598" and
    # 2.0 == 2 collapse (the live ledger mixes int/text and int/float).
    dedup_cols = ["FECHA", "ORDEN DE PRODUCCION", "TIPO", "RANGO", "CANT"]
    key = df[dedup_cols].apply(lambda col: col.map(_key_val))
    before = len(df)
    if not args.keep_duplicates:
        df = df[~key.duplicated(keep="first")]
    print(f"Dropped {before - len(df)} exact-duplicate ledger lines (agent double-reads).")

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

    # The Excel's ALISTAMIENTO rows are unreliable, so discard them and load the
    # reconciled set (verified against the book). Plate rows still come from Excel.
    plate_rows = [r for r in rows if not _is_alistado(r)]
    discarded = len(rows) - len(plate_rows)
    print(f"Discarded {discarded} Excel ALISTAMIENTO rows; "
          f"loading {len(RECONCILED_ALISTAMIENTO)} reconciled ones.")
    with db.get_conn() as conn:
        for d, category, qty, by in RECONCILED_ALISTAMIENTO:
            db.insert_blank_preparation(
                conn, prep_date=date.fromisoformat(d), category=category,
                quantity=qty, prepared_by=by,
            )
        conn.commit()

    summary = process_rows(plate_rows)
    print("\n=== Migration summary ===")
    print(f"Orders inserted:        {summary.orders_inserted}")
    print(f"Plates inserted:        {summary.plates_inserted}")
    print(f"Blank preparations:     {len(RECONCILED_ALISTAMIENTO)} (reconciled)")
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
