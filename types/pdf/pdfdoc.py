# darqos
# Copyright (C) 2026 David Arnold

# Reads PDFs, with Qt PDF: whether some data is one, and its details and
# text.  It knows nothing of items.  It needs a QCoreApplication, but no
# display.

import contextlib
import dataclasses
import time
import typing

from PyQt6.QtCore import QBuffer, QByteArray, QCoreApplication, QIODevice
from PyQt6.QtPdf import QPdfDocument


# Seconds to wait for a PDF to load: from memory, it's normally at once.
LOAD_TIMEOUT = 30.0


class PdfError(ValueError):
    """Some data can't be read as a PDF."""


@dataclasses.dataclass(frozen=True)
class Info:
    """What's known of a PDF.  One protected by a password has only
    that: no pages, or anything else."""
    pages: int = 0
    title: str = ""
    author: str = ""
    protected: bool = False


@contextlib.contextmanager
def opened(data: bytes) -> typing.Iterator[QPdfDocument]:
    """Open a PDF, for the length of a with statement.

    :raises PdfError: if it isn't one."""
    # The document reads from the buffer, and the buffer from the bytes,
    # so both last as long as it does.
    contents = QByteArray(data)
    buffer = QBuffer(contents)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    document = QPdfDocument(None)
    try:
        document.load(buffer)
        deadline = time.monotonic() + LOAD_TIMEOUT
        while document.status() == QPdfDocument.Status.Loading:
            if time.monotonic() > deadline:
                raise PdfError(f"not loaded after {LOAD_TIMEOUT}s")
            QCoreApplication.processEvents()
            time.sleep(0.001)
        if document.status() != QPdfDocument.Status.Ready \
                and document.error() != QPdfDocument.Error.IncorrectPassword:
            raise PdfError(f"can't be opened: {document.error().name}")
        yield document
    finally:
        document.close()
        buffer.close()


def is_protected(document: QPdfDocument) -> bool:
    return document.error() == QPdfDocument.Error.IncorrectPassword


def field(document: QPdfDocument, name: QPdfDocument.MetaDataField) -> str:
    """Return a metadata field's text, or an empty string."""
    return str(document.metaData(name) or "").strip()


def inspect(data: bytes) -> Info:
    """Return a PDF's details.

    :raises PdfError: if it isn't one."""
    with opened(data) as document:
        if is_protected(document):
            return Info(protected=True)
        return Info(document.pageCount(), field(document, QPdfDocument.MetaDataField.Title),
                    field(document, QPdfDocument.MetaDataField.Author))


def text(data: bytes) -> str:
    """Return the text of a PDF's pages, or nothing if it's protected.

    :raises PdfError: if it isn't one."""
    with opened(data) as document:
        if is_protected(document):
            return ""
        return "\n".join(document.getAllText(page).text() for page in range(document.pageCount()))
