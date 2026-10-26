"""Tests for app.web.uploads (no DB; writes only under tmp_path)."""

from __future__ import annotations

import base64
import io
import re

import pytest
from werkzeug.datastructures import FileStorage

from app.web import uploads
from app.web.uploads import UploadError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(autouse=True)
def upload_root(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    monkeypatch.setenv("UPLOADS_DIR", str(root))
    return root


def _fs(content: bytes, filename: str = "x.pdf") -> FileStorage:
    return FileStorage(stream=io.BytesIO(content), filename=filename)


def _data_url(raw: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(raw).decode()


def test_uploads_dir_created_on_demand(upload_root):
    assert not upload_root.exists()
    assert uploads.uploads_dir() == upload_root
    assert upload_root.is_dir()


def test_save_pdf_ok_and_name_random(upload_root):
    name = uploads.save_pdf(_fs(b"%PDF-1.7 hello", filename="../../evil.exe"))
    assert re.fullmatch(r"[0-9a-f]{32}\.pdf", name)
    assert "evil" not in name
    assert (upload_root / name).read_bytes() == b"%PDF-1.7 hello"
    assert uploads.save_pdf(_fs(b"%PDF-1.4")) != name


def test_save_pdf_none():
    with pytest.raises(UploadError):
        uploads.save_pdf(None)


def test_save_pdf_empty():
    with pytest.raises(UploadError, match="vacío"):
        uploads.save_pdf(_fs(b""))


def test_save_pdf_not_a_pdf_even_with_pdf_extension():
    with pytest.raises(UploadError, match="PDF válido"):
        uploads.save_pdf(_fs(b"<html>nope</html>", filename="doc.pdf"))


def test_save_pdf_too_big(upload_root):
    with pytest.raises(UploadError, match="10 MB"):
        uploads.save_pdf(_fs(b"%PDF-" + b"0" * uploads.MAX_PDF_BYTES))
    assert not upload_root.exists()  # nothing stored (dir is created lazily)


def test_save_pdf_exactly_max_ok():
    name = uploads.save_pdf(_fs(b"%PDF-" + b"0" * (uploads.MAX_PDF_BYTES - 5)))
    assert name.endswith(".pdf")


def test_save_signature_ok(upload_root):
    name = uploads.save_signature_png(_data_url(PNG))
    assert re.fullmatch(r"[0-9a-f]{32}\.png", name)
    assert (upload_root / name).read_bytes() == PNG


@pytest.mark.parametrize("value", [None, "", "hello", "data:image/jpeg;base64,AAAA", "data:image/png,AAAA"])
def test_save_signature_bad_prefix(value):
    with pytest.raises(UploadError):
        uploads.save_signature_png(value)  # type: ignore[arg-type]


def test_save_signature_bad_base64():
    with pytest.raises(UploadError, match="formato"):
        uploads.save_signature_png("data:image/png;base64,@@@not-base64@@@")


def test_save_signature_empty_payload():
    with pytest.raises(UploadError):
        uploads.save_signature_png("data:image/png;base64,")


def test_save_signature_wrong_magic():
    with pytest.raises(UploadError, match="PNG"):
        uploads.save_signature_png(_data_url(b"GIF89a" + b"\x00" * 20))


def test_save_signature_too_big(upload_root):
    with pytest.raises(UploadError, match="grande"):
        uploads.save_signature_png(_data_url(PNG + b"\x00" * uploads.MAX_SIGNATURE_BYTES))
    assert not upload_root.exists()


def test_resolve_roundtrip(upload_root):
    name = uploads.save_pdf(_fs(b"%PDF-1"))
    assert uploads.resolve_upload_path(name) == upload_root / name


@pytest.mark.parametrize(
    "name",
    [
        "../" + "a" * 32 + ".pdf",
        "/etc/passwd",
        "/" + "a" * 32 + ".pdf",
        "a" * 32 + ".pdf\x00.png",
        "a" * 32 + ".pdf\n",
        "a" * 32 + ".exe",
        "a" * 32 + ".PDF",
        "a" * 32 + ".pdf/../x",
        "A" * 32 + ".pdf",
        "a" * 31 + ".pdf",
        "a" * 33 + ".png",
        "..\\" + "a" * 32 + ".pdf",
        "",
        "a" * 32,
    ],
)
def test_resolve_rejects_bad_names(name):
    with pytest.raises(UploadError):
        uploads.resolve_upload_path(name)


def test_resolve_rejects_non_string():
    with pytest.raises(UploadError):
        uploads.resolve_upload_path(None)  # type: ignore[arg-type]


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16


@pytest.mark.parametrize("content,extension", [(JPEG, ".jpg"), (PNG, ".png"), (WEBP, ".webp")])
def test_save_photo_sniffs_the_type_from_the_content(upload_root, content, extension):
    name = uploads.save_photo(_fs(content, "whatever.txt"), "Cédula (frente)")
    assert name.endswith(extension)
    assert (upload_root / name).read_bytes() == content
    assert uploads.resolve_upload_path(name) == upload_root / name


@pytest.mark.parametrize("storage", [None, FileStorage(stream=io.BytesIO(b""), filename="")])
def test_save_photo_missing(storage):
    with pytest.raises(UploadError, match="Falta la foto: Cédula"):
        uploads.save_photo(storage, "Cédula (frente)")


def test_save_photo_empty():
    with pytest.raises(UploadError, match="vacía"):
        uploads.save_photo(_fs(b"", "a.jpg"), "Cédula (frente)")


def test_save_photo_rejects_a_pdf_named_jpg():
    with pytest.raises(UploadError, match="no es una foto válida"):
        uploads.save_photo(_fs(b"%PDF-1.4 x", "a.jpg"), "Cédula (frente)")


def test_save_photo_too_big():
    with pytest.raises(UploadError, match="5 MB"):
        uploads.save_photo(_fs(JPEG + b"\x00" * uploads.MAX_PHOTO_BYTES, "a.jpg"), "Cédula (frente)")
