"""Render a sworn declaration as a PDF for the client to get notarized.

PLACEHOLDER: the real generator replaces `build_declaration_pdf`'s body; the
signature is the contract the review screen codes against.
"""

from __future__ import annotations

from datetime import datetime

from app.core.db.declarations import DeclarationCardRecord, DeclarationRecord


def build_declaration_pdf(
    *, declaration: DeclarationRecord, cards: list[DeclarationCardRecord],
    signature_png: bytes | None, issued_at: datetime,
) -> bytes:
    """The declaration as a PDF (bytes starting with `%PDF-`)."""
    return b"%PDF-1.4\n% placeholder\n%%EOF\n"
