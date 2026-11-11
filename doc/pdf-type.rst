PDF Type
========

The PDF type, ``com.adobe.pdf``, holds a PDF document.  An item is
read-only: it can be viewed, searched, exported and destroyed, but its
content never changes.

Items
-----

An item's created from a PDF's data, with ``CreateFrom``: from Import
File in the New panel, say, or by an importer.  ``Create`` is
``UNSUPPORTED``, since an empty PDF means nothing.  Data that a PDF
library can't open as a PDF is ``INVALID_DATA``.  A password-protected
PDF is accepted, as it's still a PDF, though its text can't be read.

An item's identifier is its type and its blob's Storage key, in hex.
The blob is a small header, which is where an item's marked destroyed,
and then the PDF, unchanged, which is what ``Export`` returns.

The item's port serves the ``Pdf`` protocol, in ``lib/didl/pdf.didl``:
the ``Item`` protocol's ``Close``, and

* ``Get()``, which returns the PDF; and
* ``Stat()``, which returns its size, and number of pages.

Rendering is done by the lens: the item is just its bytes, so the same
protocol will serve anything else that wants to show a PDF.  As for other items, the PDF is sent whole in a message, until
Storage has ranged access (see :doc:`storage`).

PDF items are plain Storage items, as text and images are, for now.
Whether they become KB entities is part of the entities-and-items
decision (see :doc:`entity-vs-item`).

Indexing
--------

When an item's created, its entity's names, and its title and author, if
it has them, are published to the Index (see :doc:`search-service`) as
names, and the text of its pages as its body: and again when its entity's
changed, as when it's renamed.  Its entity's named by its title, or else
by the file it came from (see :doc:`entity-vs-item`).  Few PDFs have a title: of 44 from one
Slack workspace, 15 did.  So most are found by their text.  A
destroyed item is withdrawn.  ``IndexEntry`` gives the same, for a
reindex.

Books
-----

A PDF can be catalogued as a book, with B, which doesn't change its
item, and the lens has a reading view for those, a page at a time, which
V switches to or from (see :doc:`book`).  Every PDF has a reading
position, in the blob's header, with ``GetPosition`` and ``SetPosition``
on the protocol, in a second format version, with first-version blobs
still read.

The Lens
--------

The PDF lens, ``lenses/pdf``, is a read-only viewer, in the Terminal
context.  It opens at the top of the document, fitted to the width of
the window, with the pages in one continuous column.

* Zoom with + and −.  W fits the width, F fits a whole page, and 0
  shows the document at actual size.
* The usual keys move: Page Up and Page Down, Space, Home and End.  G
  goes to a page by its number.
* Ctrl+S or Ctrl+F finds text: matches are highlighted, and Enter and Shift+Enter
  (or F3 and Shift+F3) move between them.
* O shows the document's outline, if it has one, in a panel: Enter or a
  click goes to that place.
* V is the reading view, and B catalogues it as a book.
* F1 shows the keys, and F11 is full screen.
* It tells the item it's being viewed, and on which page, and the item's
  type records it in History as ``viewed``, from ``pdf-lens``, noted
  with the page, such as *page 45 of 296*.

Links in a PDF aren't followed, and text can't be selected: Qt's
``QPdfView`` does neither, so each would need the lens to work out
where in which page a click lands.  They may come later, with printing,
annotations, and remembering the page a document was left at.

Implementation
--------------

Both the type and the lens use Qt PDF (``PyQt6.QtPdf``), which is
PDFium, and comes with PyQt6, so it needs no new dependency.  It reads
a PDF from memory, and it's licensed under the LGPL.  The alternatives
considered were:

* PyMuPDF, which is capable, but is AGPL or commercial, in an
  MIT-licensed project;
* pdf2image, which only renders pages to images, with poppler, and has
  no text; and
* PDF-Viewer, which is unmaintained, built with PySimpleGUI, under a
  modified LGPL.  Its outline tree and fit-to-width are useful ideas,
  but its code can't be reused.

``pypdfium2`` is the fallback: it's the same engine, with a permissive
licence, but it's another dependency.

* ``types/pdf/pdfdoc.py`` opens PDFs, and reads their details and text,
  with Qt PDF: it knows nothing of items, so it's tested alone.  It
  needs only a ``QCoreApplication``, so the implementation needs no
  display.
* ``types/pdf/main.py`` is the implementation: the items, their blobs,
  and the protocols.
* ``lenses/pdf/main.py`` is the lens.

The tests are in ``tests/test_pdf_type.py`` and
``tests/test_pdf_lens.py``.
