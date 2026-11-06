Book Type
=========

Books are one of the fundamental Types in DarqOS.  But the type
modelling is not entirely simple.  It is based on the Library
Reference Model (LRM) (formerly known as the Functional Requirements
for Biliographic Records (FRBR), and BIBFRAME which is an RDF-based
implementation of LRM from the US Library of Congress.  The LRM
standard specifies four fundamental types:

* *Work* (the conceptual creation)

  This is the most abstract type, representing a single dictinct
  artistic or intellectual creation.

  eg. George Orwell's "1984".

  It doesn't matter if it's in English, Spanish, a paperback, or an
  audiobook, the "Work" remains 1984.

* *Expression* (the realization of the Work)

  The specific form that the Work takes.  This is where translations,
  revisions, and abridgements live.

  eg. the Spanish translation of 1984 by Rafael Vázquez.

  If the text changes (like a translation), it is a new Expression of
  the same Work.

* *Manifestation* (the physical or digital embodiment)

  eg. the 1950 Signet Classics paperback edition of 1984.

  If it has a different layout, publisher, or cover, it's a different
  manifestation.

* *Item* (a single exemplary instance)

  This represents a single, physical or digital object.  It usually
  has a different asset tag, unique coffee stains, etc.

There's a useful site that has all the entity-relationship model for
the LRM available.

   https://www.iflastandards.info/lrm/lrmer.html

   
A Book Is Not a Format
----------------------

A book is a catalogue fact, not a file format.  A fetched PDF stays a
``com.adobe.pdf`` item: that it's a book is something said about it, so
ingestion doesn't need to guess, and a fetched paper stays a PDF unless
it's said to be otherwise.  It also settles whether an item's identity
should outlive its format: for a book, identity is the Work, and a
format belongs to a Manifestation.

.. code-block:: none

   Work: Bliss (Peter Carey)
   └─ Expression: English text
      ├─ Manifestation: first US edition, Harper & Row, hardcover
      │  └─ LRM Item: my copy
      └─ Manifestation: UK edition, Picador paperback, 1982
         └─ LRM Item: my copy, 9th printing

   Work: 1984 (George Orwell)
   └─ Expression: English text
      ├─ Manifestation: the EPUB   → LRM Item → an EPUB item
      ├─ Manifestation: the PDF    → LRM Item → a PDF item
      └─ Manifestation: the MOBI   → LRM Item → a MOBI item

All four levels are there from the start, though Expression is rarely
of interest in a personal library, as adding it later would mean
migrating every book.  The Work and Expression are made for you:
nobody needs to think about WEMI to catalogue a book.

All four are KB entities, of the kinds below, in ``types/kb``, as
Message is.  There's no BookRecord kind: a paper book is an LRM Item
that gives a location, and an e-book is an LRM Item that gives a file,
as an item's identifier, as URL snapshots do (see :doc:`url-type`).
The file's item isn't changed by being catalogued.

``darq.book`` has their vocabulary, and reads the four levels as one
``Book``, from any of them, writing each field back to its own level.
It knows nothing of how entities are reached, so it's tested alone.

New offers only Book, which makes a work: what's under it is made as
it's needed, so the other three aren't offered.  Each level has a label
of its own, and only the work's is the title, so that Search finds a
book once.

=================  =====================================  ==================
Level              Class                                  Tied to the next by
=================  =====================================  ==================
Work               work (Q111955339)
Expression         expression (Q111955324)                version, edition or
                                                          translation of
                                                          (P629)
Manifestation      manifestation (Q111955382)             P629
LRM Item           individual copy of a book (Q53731850)  exemplar of (P1574)
=================  =====================================  ==================

Wikidata's individual copy is described as physical, and an e-book's
LRM Item isn't, but it's the nearest there is.  The fields are:

=============  ==========================================================
Level          Properties
=============  ==========================================================
Work           Label (title), subtitle (P1680), author (P50), part of the
               series (P179, with series ordinal P1545 for its volume)
Expression     Language (P407), translator (P655), editor (P98), and
               local *started reading* and *finished reading*
Manifestation  Publisher (P123), publication date (P577), edition number
               (P393), ISBN-13 (P212), ISBN-10 (P957), number of pages
               (P1104), and local *imprint* and *binding*
LRM Item       Local *location*, *file*, *printing* and *date of printing*
=============  ==========================================================

An imprint is text, for now, unlike a publisher.  People, publishers,
languages and series are entities: a new name finds
an entity with exactly that label, or makes one, while a name that's
already there keeps its entity, so two people with one name aren't
confused.  A person's page then shows *author of*, through an inverse
label, in a group of Books.

Which Level
~~~~~~~~~~~

* A new publisher, imprint, country, binding or file format is a new
  Manifestation, as is a reprint that changes the text or the cover.
* A reprint that changes nothing is the same Manifestation, with the
  printing on the LRM Item, so print runs don't each make a row.
* Two copies of one printing are two LRM Items of one Manifestation,
  each with its own location, condition, and where it was bought.
* Publication date and edition statements are on the Manifestation, and
  printing number and year on the LRM Item: a 9th printing of a 1982
  edition has both.
* A changed text is, strictly, a new Expression, but editions are
  treated as one Expression unless the text's known to differ: being
  finished is said of the Expression, so splitting it needlessly would
  stop that applying to both, and the card can split them later.
* Identical files, by hash, are one item.

Types
-----

LRM Work
~~~~~~~~

* The LRM Work type, as a KB Kind.
* UTI: ``darq.book.work``
  
  * `lrmer:E2`
    
* Attributes
  
  * Category of Work
  * Note
    
* Relationships
  
  * isRealizedThrough -> Expression (lrmer:R2)
  * wasCreatedBy -> Agent (lrmer:R6)
  * hasAppelation -> Nomen (lrmer:R13)
  * hasAsSubject -> Res (lrmer:R12)

    
LRM Expression
~~~~~~~~~~~~~~

* The LRM Expression type, as a KB Kind.
* UTI: ``darq.book.expression``
* Attributes
* Relationships
  
  * realizes -> Work (lrmer:R2)
  * isEmbodiedIn -> Manifestation (lrmer:R4)
  * wasCreatedBy -> Agent (lrmer:R7)
  * hasAppelation -> Nomen (lrmer:R14)
  * isPartOf -> Expression (lrmer:R18)
  * accompanies/complements -> Expression (lrmer:R20)
  * isTranslationOf -> Expression (lrmer:R21)
  * isDerivationOf -> Expression (lrmer:R24)
    

LRM Manifestation
~~~~~~~~~~~~~~~~~

* The LRM Manifestation type, as a KB Kind.
* UTI: ``darq.book.manifestation``
* Attributes
* Relationships
  
  * embodies -> Expression (lrmer:R4)
  * isExemplifiedBy -> Item (lrmer:R5)
  * wasProductionOf -> Agent (lrmer:R8)
  * hasAppelation -> Nomen (lrmer:R15)

    * This is where the ISBN lives
      
  * isPartOf -> Manifestation (lrmer:R26)
  * accompanies/complements -> Manifestation (lrmer:R28)
  * isReproductionOf -> Manifestation (lrmer:R30)
  *


LRM Item
~~~~~~~~

* The LRM Item type, as a KB Kind.
* UTI: ``darq.book.item``
* Relationships

  * exemplifies -> Manifestation (lrmer:R5)
  * wasModifiedBy -> Agent (lrmer:R9)
    * Custom binding, restoration, or preservation
  * hasOwner/Custodian -> Agent (lrmer:R11)
  * hasAppellation -> Nomen (lrmer:R16)
    * barcodes, RFIDs, custom inventory control numbers, etc
  * isPartOf -> Item (lrmer:R27)
  * accompanies/complements -> Item (lrmer:R16)

    
E-Book
~~~~~~

An item of e-book data.  These are types of their own, conforming to an
abstract ``darq.ebook``, in one implementation, as images are (see
:doc:`image-type`), so a new format needs only an entry in ``type.json``:

=====================  ==============================
Type                   Extensions
=====================  ==============================
``darq.book.epub``     epub
``darq.book.mobi``     mobi, prc
``darq.book.azw``      azw, azw3
=====================  ==============================

The implementation, ``types/ebook``, hides the format.  It offers a lens
one view of any book: its parts as HTML, its style sheets, images and
fonts, its table of contents, and its metadata.

* Every file is kept, and catalogued, whether or not it can be shown.
* EPUB is a zip file of XHTML and CSS, so it needs no library.
* MOBI and AZW use libmobi, a C library, through ``ctypes``, with the
  data loaded from memory.  It's licensed under the LGPL, as Qt PDF is.
  ``mobi`` and KindleUnpack are GPL, as are Lector and Calibre, so none
  of their code can be used in an MIT-licensed project.  ``ebook-rs``,
  in Rust, is MIT-licensed, and is the fallback.
* Parsing happens in the type's process, so a damaged book can't take a
  lens with it.  It's a C parser reading untrusted files: books can come
  from ``darq.http``.
* A book with DRM can be kept and catalogued, but not read, and the lens
  says so.  Nothing tries to remove it.

libmobi rebuilds a KF8 book's skeleton and fragments, and its links, so
that what comes out is ordinary HTML.  Two MOBI and two AZW3 samples
were tried, with no DRM, and all came out correctly, from a parse of 3
to 51 ms: an old MOBI 6 novel, a hybrid book, and pure KF8 books, with
CSS, images and embedded fonts.  An old MOBI book is one long part, so
its ``mbp:pagebreak`` tags need to become page breaks.

Cataloguing
-----------

Cataloguing is an action, not a step of fetching: most fetched PDFs
aren't books, so nothing prompts for it.

From a PDF's or an e-book's lens, B starts it:

1. The lens reads the file's metadata, and looks for an ISBN in the
   first pages.
2. The Book card opens, filled in, and asks whether it's one of the
   books already there, by ISBN and by title and author: each format
   often has an ISBN of its own, and older books may have none.
3. It's another copy of a printing that's there (a new LRM Item), or
   another format or edition (a new Manifestation, copying what's
   shared), or a new book.
4. Confirming writes what's new.  The file's item doesn't change.

So a book in three formats is added once, and the others attached to it.

The Add Loop
~~~~~~~~~~~~

Paper books are the bulk of the library, between 1,000 and 10,000 of
them, so New → Book opens a loop, built for speed:

1. Scan or type an ISBN.  A USB barcode scanner just types.
2. It's looked up, and shown to be checked.  A book with no ISBN falls
   back on a search by title.
3. Set its location, which sticks for the next book.
4. Enter, and on to the next.

Lookups go to Open Library first, as it's open, and needs no account.
It goes through ``darq.http``, and only when asked, since it sends an
ISBN to a third party.  Each statement cites where it came from.

At four entities a book, 10,000 books are 40,000 entities, which the KB
handles easily.

Importing
~~~~~~~~~

* **LibraryThing:** its JSON export comes first, as real records test
  the model, and the books are then there for the add loop to match
  (see :doc:`librarything-import-tool`).
* **Calibre:** its ``metadata.db`` is plain SQLite, and its base tables
  can be read directly, with a book having a row per format in ``data``,
  and its ISBNs in ``identifiers``: an Expression and its Manifestations.
  The importer is written from the schema, using none of Calibre's code.
* **Dropbox:** a folder of files, matched up by name and embedded
  metadata.
* **Kindle:** last, as DRM may mean those can only be catalogued.

Reading
-------

Read opens a book's file in that file's own lens.  A book in several
formats is read in the first of a default order of preference, a
setting, which a book can override with the KB's preferred rank on a
Manifestation.  The card always lets you pick a specific one.

What's been read has two parts:

* That a book's being read, or was finished on some day, is knowledge,
  so it's a statement on the Expression, in the KB.  Reading the PDF
  means the EPUB isn't unread.
* Where you are in a file is view state, kept in the item, in a header
  in its Storage blob.  It's specific to that file, as pages don't
  line up between formats.  DarqOS is a single-user system, so an item
  needs only one.  It stays in the item unless the Metadata Service
  comes to be, and it's why ``GetPosition`` and ``SetPosition`` are calls
  on a reader's protocol: lenses wouldn't change.

A position is a string, which only the lens makes sense of: a page
number for a PDF, and a part and offset for an e-book, as a part can
run to 170 pages.  Since ``Put`` writes a whole value, saving a position
rewrites the blob, the book included, so it's saved when the book's
closed, rather than as you read: a lens that's killed loses its
position.  Ranged access to Storage would change that.

Lenses
------

Work, Expression, Manifestation and LRM Item
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

These open the Book card, with the one you opened highlighted.  The
generic entity lens is still there for all of their statements.

The Book Card
~~~~~~~~~~~~~

There's one card for a book, though it's four levels and any number of
copies: it's centred on the Expression, with what you hold beneath it.
Fields are saved to the right level, and it supports both viewing and
editing, in ``lenses/book``.  Editions are in order of date, as a
link's order means nothing.  A save goes through the same edits as the
entity lens, so a source's statements stay the source's, and a re-import
doesn't fight what's been changed by hand.

* E edits it, Ctrl+S saves, and Escape cancels.  A new book starts in
  the form.  Names in a field are separated by semicolons.
* R reads the first file, and a format's Read reads that one.
* I shows what was opened in the entity lens, with all of its statements.
* A name opens that person or publisher in their own lens.

A save writes each level on its own, so a failure can leave some
written: what's been made is kept in the form, and saving again
finishes it.  An edition or copy can't be removed yet, and covers
aren't shown yet.

.. code-block:: none

   Bliss  Peter Carey
   English · 1981
   ────────────────────────────────────────────
   Paper    Harper & Row, hardcover      study, shelf 3
            Picador, paperback, 1982     office
   ────────────────────────────────────────────
   Finished 3 March

   1984  George Orwell
   ────────────────────────────────────────────
   Formats  EPUB    Read
            PDF     Read
            MOBI    Read

It starts with title, subtitle, contributors with their roles, series,
publisher, date, ISBN, language, pages, cover and location, with title
and author in a larger font.  The rest are ordinary statements, which
the entity lens can add from the first day.  Authors and publishers
should complete from previous entries.  It should be possible to add
front and back cover images, and a photo of an ISBN bar code is a
later possibility.

Readers
~~~~~~~

There's one lens for reflowable formats, and one for PDF, with
near-identical UI, sharing their window, keys, find, contents panel and
look, in ``darq.ui.reader``.  The real difference is that + and −
change the size of the text, rather than zoom.

* It's distraction-free, with hotkeys for turning the pages.

  * Page-forward: Space, Page Down, C-f
  * Page-back: Backspace, Page Up, C-b
  * Home and End go to the front and rear covers

* It offers dark, sepia and light modes.
* It reopens where you left off, as above.
* In a future edition, it should support annotations, but not yet.
* It doesn't change a book's content: only its catalogue entry can be
  edited.

The PDF Lens
~~~~~~~~~~~~

There's one PDF lens, ``lenses/pdf``, with two views.  A lens is found
by type and context, so there's no way to give an item a lens of its
own, and none is needed: being catalogued is the mark.

When it opens, it looks for an LRM Item that gives this item as its
file (``FindByValue``).

* If there is one, it starts in the reading view: a page at a time,
  fitted to the window, with the keys above, and a way to the Book card.
* If not, or the KB isn't there, it's the view it has now: a continuous
  column, fitted to the width.

Cataloguing switches it at once, and it can be switched by hand either
way.  Lookups aren't added to opening other types, and an item's
identifier begins with its type, so giving catalogued PDFs a type of
their own would change their identity, and break what links to them.

A changed PDF, fetched again, is a new item, which isn't catalogued, so
it opens in the plain view: it may well be a new edition.  Offering to
add it, through the URL's snapshots, can come later.

The E-Book Lens
~~~~~~~~~~~~~~~

``lenses/ebook`` renders with Qt WebEngine, which is Chromium, and takes
what the type gives it through a custom ``book:`` URL scheme, so nothing
is written to disk.  Pages are CSS columns, and turning one is a scroll,
which is also what Calibre's viewer does.

``QTextBrowser`` was considered, and needs no dependency, but it has
only part of HTML and CSS, with no SVG or MathML, and a page break must
be worked out by hand, as Lector does.  That's poor for computer science
textbooks, so WebEngine makes for a simpler lens, not a harder one.

* JavaScript is off for the book, while the lens's own scripts run in a
  world of their own, and a book's scripts don't run.
* Remote content isn't loaded, so a book can't call home.  A profile
  setting does that, and a request interceptor makes sure of it.
* A link out of the book is refused by ``acceptNavigationRequest``, and
  offered to ``darq.http``, as a URL in Search is.
* Keys and mouse events reach the lens before Chromium does, so it
  decides what's handled by discarding them, and what's left goes on.
* Page turns must cross parts, and a position snap to a page.  Calls
  into the page are asynchronous, and a refused link reports a failed
  load, which is to be ignored.

It was tried on real hardware, as a client of the compositor, on Intel
UHD 620: it loads a chapter in about half a second, and uses about 270
MB, in a process and 4 of Chromium's.  Killing the process takes those
with it within half a second.  It needs a GPU: under the headless
compositor's software renderer, Chromium draws nothing, so tests use
Qt's offscreen platform.

It takes about 290 MB of Python packages, and 10 small Debian packages
for the dist, some of them X11 libraries (``libnspr4``, ``libnss3``,
``libxcomposite1``, ``libxdamage1``, ``libxfixes3``, ``libxrandr2``,
``libxtst6``, ``libxkbfile1``, ``libxi6`` and ``libxrender1``), and
``libmobi0t64``.  ``requirements.txt`` needs Qt pinned, as the dist has
6.12, and a development environment may have 6.9.

Whether the web lens of Third Edition uses this or CEF is its own
decision (see ``types/html``), which this bears on: sharing an engine
saves a second copy of Chromium, and control of keys and mouse has been
fine with a book, though not yet tried with real pages.

The Order of Work
-----------------

1. The kinds, and the Book card.  (Done.)
2. The LibraryThing import.  (Done: see :doc:`librarything-import-tool`.)
3. The add loop, and ISBN lookups.
4. ``types/ebook``, ``lenses/ebook``, ``darq.ui.reader``, and the
   PDF lens's reading view, starting with a real lens under ``pyloader``.
5. Importing e-books, from Calibre, then Dropbox, then Kindle.

The Complete Data
-----------------

Not every book will populate every field.  Properties use Wikidata's
where it has them, checked as they're built: those so far are in *A
Book Is Not a Format*.  It also has Dewey Decimal (P1036), Library of Congress (P1149)
and OCLC (P243) numbers, an LCCN (P1144), an ASIN (P5749), and ids for
Open Library (P648) and LibraryThing (P1085).  It has no printing, so
that's local.

The complete data should include:

* Title
  
  * Title sort

* Subtitle
* Original title

  * for translations
* Alternative/variant titles
* Uniform title

  * consensus indexing title

* Series Name
* Volume number
    
* Author
  
  * Role
    
    * Afterword
    * Author
    * Composer
    * Contributor
    * Cover Artist
    * Cover Designer
    * Designer
    * Director
    * Editor
    * Foreword
    * Illustration
    * Introduction
    * Narrator
    * Photographer
    * Preface
    * Translator
    * Other
* Tags / keywords
* Collection

  * Which collection within the library is this a part of?  In my case, fiction, non-fiction, science-fiction, space, politics, roman, computer

* Your rating
* Your review
* Media

  * Book

    * Paper book

      * Paper back
      * Hard cover

    * Audio Book
      
      * Digital file
      * CD
      * Tape
	
* Publisher
* Imprint

  * sub-brand or division of publisher

* Date published
* Place published
* Edition
* Printing year
* Printing run
* Date purchased
* Place purchased
* Price purchased
* MSRP (per country)
* Copyright year
* Rights/distribution restrictions
* Availability status

  * In print, Out of print, Forthcoming, Pre-order, Remaindered

* File format

  * For e-books

* file size
* DRM status/type
* Audio bit/sample rate
* Pages
* Extent

  * duration for audio books

* Font / typography details
* Dimensions
* Weight
* Volumes
* Primary Language
* Original Language
* Script / notation
* Dewey decimal
* LCCN
* OCLC Worldcat
* ASIN
* DOI
* BISAC / Subject headings
* Front Cover photograph
* Rear cover photograph
* ISBN-10
* ISBN-13
* EAN
* UPC
* ISSN
* Location (for paper books)

  * Is this a good place to use an encapsulated volumes model for
    physical locations?
  * It'd be good to have some sort of structure here, not just free
    form text

* Notes
* Cataloging date
* Summary / description
* Table of Contents
* Target audience / age group
* Lexile measures / reading age
* Content warning / maturity ratings

Notes
-----

Also, similarly to music, there's an argument to be made that you want
a specialised browser for the collection of books.  A CoverFlow-style
UI would be nice, sorted by author, etc.

I wonder if this can be made general, such that it's the object
selector that provides the browsing experience, but if you constrain
your search to a specific type, perhaps the type implementation could
provide some specialisation to the UI?

Alternatively, there could be a collection type for books, which does
the presentation and links to the actual books?  Not sure that's a
great UX though ... but certainly music, video and books should all be
similarly implemented here.

Open Questions
--------------

* How an old MOBI book's single part should be split, and whether that's
  the type's job or the lens's.
* What a book with DRM should show, which hasn't been tried.
* Whether MARC21 is worth supporting as an import or export format.
* Whether location is free text, or a structure of its own.
* Whether a collection browser belongs to the object selector, to a
  collection type, or to a type's own specialisation of them.
* A later "this is a newer version: add it?" for a re-fetched PDF.
* ``Citable``, for papers as well as books (see :doc:`bib`): cataloguing
  could become "Catalogue as ...", with Book as its first choice.
