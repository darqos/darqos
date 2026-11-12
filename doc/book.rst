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

All four are items of the kinds below, all knowledge, in ``types/kb``,
as messages are.  A paper book is an LRM Item that gives a location, and an
e-book is an LRM Item that gives an item identifier, as URL snapshots
do (see :doc:`url-type`).

The e-book item isn't changed by being catalogued.

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
LRM Item       Local *location* (Shelved at), *content*, *printing* and *date of printing*
               (Date printed)
=============  ==========================================================

An imprint is text, for now, unlike a publisher.  People, publishers,
languages and series are items too, which can become Wikidata's, with a
few of its facts (see :doc:`wikidata`): a new name finds
an entity with exactly that label, or makes one, while a name that's
already there keeps its entity, so two people with one name aren't
confused.  A person's entity then shows *author of*, through an inverse
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
one view of any book, through ``lib/didl/ebook.didl``: its parts as HTML,
its table of contents and details, and its style sheets, images and
fonts, by name.  ``bookfile.py`` reads EPUB, and ``mobifile.py`` MOBI and
AZW.

* Every file is kept, and catalogued, whether or not it can be shown.
* EPUB is a zip file of XHTML and CSS, so it needs no library.
* MOBI and AZW use libmobi, a C library, through ``ctypes``, with the
  data loaded from memory, and its structures as of 0.12 declared there.
  It's licensed under the LGPL, as Qt PDF is.  ``DARQ_LIBMOBI`` names it
  for a tree without it installed: without it, a book's kept and
  catalogued, but says it can't be read.
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
its ``mbp:pagebreak`` tags become column breaks, unless they're at the
very end, which would only make an empty page.  It has no table of
contents, either, unless it was given one.  libmobi leaves a placeholder
name, ``XXXXXXXXXXXXXXXX``, for something it can't resolve: it's just not
found.

Cataloguing
-----------

Cataloguing is an action, not a step of fetching: most fetched PDFs
aren't books, so nothing prompts for it.

From a PDF's or an e-book's lens, B starts it (``darq.ui.catalogue``):

1. The lens reads the file's metadata: title, authors, ISBN, publisher,
   date and language, as far as it says, or a PDF's title and author,
   and its first pages are looked through for an ISBN.  Only a file with
   no title asks for one.
2. It looks for the book, by ISBN, and by title and author: each format
   often has an ISBN of its own, and older books may have none.
3. If it might be one that's here, it asks what it is, from a list:
   another copy of an edition that's there, found by ISBN (a new LRM
   Item); another edition of one of its texts (a new Manifestation of
   that Expression), each described by its language and translators; a
   new text of it, such as another translation (a new Expression of the
   same Work); or a new book.  A text in another language than the
   file's isn't offered.  Escape catalogues nothing.
4. What's new's written, and its Book card opens, to be filled in.  The
   file's item doesn't change.  If it's been catalogued, B just opens
   its card.

So a book in three formats is added once, and the others attached to it.

The Add Loop
~~~~~~~~~~~~

Paper books are the bulk of the library, between 1,000 and 10,000 of
them, so New → Book opens a loop, built for speed, as does A on a card:

1. Scan or type an ISBN, and press Enter.  A USB barcode scanner just
   types, and usually sends the Enter itself.  It's looked up, and shown
   to be checked.  Anything else that's typed is a search by title, to
   choose from.  What Open Library doesn't know is typed in.
2. The focus goes to Shelved at, which stays for the next book, as the
   language does.  What the book is to what's here is shown.
3. Enter adds it, and it's ready for the next.  Escape clears what's
   there, and then finishes, showing the last book.

Nearly always, a book's all four levels new, and Enter's all it takes.
Otherwise, it stops and asks:

* The same ISBN, in either form, is a duplicate: Y adds another copy,
  and N doesn't.  It may well be the same book scanned twice, or one
  that was imported, whose copy wants a location on its card instead.
  A copy changes nothing else on the card.
* The same title and author is only a guess, so it asks what it is, by
  number: another edition of one of the book's texts (a new
  Manifestation of that Expression), each described by its language and
  translators, or a new text of it, such as another translation (a new
  Expression of the same Work); or N adds a new book.  A text in another
  language than the one being added isn't offered, as it isn't for
  guessing.

Once it's added, it says what it was added to, and how many there are
there now, and how many books it's added: *Added a copy of Bliss, now 2
copies for this Manifestation.  Total 6 books added so far.*

It asks on keys of their own, as a scanner usually sends Enter by itself,
and a stray one mustn't count as a yes.  Other keys do nothing.  If a new
book's empty item isn't used, it's cleared away.

Lookups go to Open Library, as it's open, and needs no account, through
``darq.http``, and only when Enter's pressed, since what's typed goes to
a third party.  They're in ``darq.openlibrary``, which knows nothing of
how they're fetched.  What's confirmed is the operator's, as it's been
checked, with Open Library's identifier (P648) kept on the edition.

* Its Books API's ``data`` form is used for an ISBN, as the one request
  with authors' names, publishers and identifiers.  Open Library calls it
  legacy: Search is its successor, but has the work, not the edition.  It
  gives no language, so that's the field that stays.
* An ISBN's check digit is checked first, and a repeated digit refused.
  A record has to carry the ISBN asked for, in either form, as a junk
  ISBN found a stray record.
* It's one book at a time.  Open Library's APIs are limited to a request
  a second, or three for an application that gives its contact, and
  aren't for bulk use, so nothing here looks up a batch: use its monthly
  data dumps for that.  ``darq.http``'s ``User-Agent`` doesn't identify
  the operator, so these are ordinary requests.

At four entities a book, 10,000 books are 40,000 entities, which the KB
handles easily.

Importing
~~~~~~~~~

* **LibraryThing:** its JSON export comes first, as real records test
  the model, and the books are then there for the add loop to match
  (see :doc:`librarything-import-tool`).
* **Calibre:** (not needed: Import File and B will do) its ``metadata.db`` is plain SQLite, and its base tables
  can be read directly, with a book having a row per format in ``data``,
  and its ISBNs in ``identifiers``: an Expression and its Manifestations.
  The importer is written from the schema, using none of Calibre's code.
* **Dropbox:** a folder of files, matched up by name and embedded
  metadata.
* **Kindle:** last, as DRM may mean those can only be catalogued.

Reading
-------

Read opens a book's content in that item's own lens.  The card lets you
pick a specific one, and R reads the one that's preferred, or else the
first.  A copy's preferred by having the characteristic (P1552)
preference (Q908656): the card marks it with a star, and offers to make
another the one to read, when a book has more than one to read.  Only one
of a book's copies is, across all its texts.  Preferred rank was
considered, and isn't what that's for: it's about how true a statement
is.

What's been read has two parts:

* That a book's being read, or was finished on some day, is knowledge,
  so it's a statement on the Expression, in the KB.  Reading the PDF
  means the EPUB isn't unread.
* Where you are in a file is view state, kept in the item, in a header
  in its Storage blob.  It's specific to that file, as pages don't
  line up between formats.  DarqOS is a single-user system, so an item
  needs only one, and it's why ``GetPosition`` and ``SetPosition`` are
  calls on a reader's protocol.

A position is a string, which only the lens makes sense of: a page
number for a PDF, and a part and how far through it for an e-book, as a
part can run to 170 pages, and a page depends on the window.  ``Put``
writes a whole value, so saving a position rewrites the blob, the book
included, and it's saved when the book's closed, rather than as you
read: a lens that's killed loses its position.  Ranged access to Storage
would change that.  Every PDF has one, catalogued or not, and a blob of
the first version is read, and upgraded when it's next written.

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

* E edits it, Ctrl+S saves, and Escape cancels.  Names in a field are
  separated by semicolons.  What's made and changed is recorded in
  History against each level's own item, as well as any people and
  publishers that are made, so they all have their histories.
* A adds more books (see *The Add Loop*), which is where a new book
  starts.
* R reads the first of its content, and a format's Read reads that one.
* I shows what was opened in the entity lens, with all of its statements.
* A name opens that person or publisher in their own lens.

A save writes each level on its own, so a failure can leave some
written: what's been made is kept in the form, and saving again
finishes it.  Covers aren't shown yet.

.. code-block:: none

   Harry Potter and the Philosopher's Stone
   J. K. Rowling
   ────────────────────────────────────────────
       English
       Finished 3 March
   ────────────────────────────────────────────
           Bloomsbury, paperback, 1997
           ISBN 0747532699
               study, shelf 3 · printing 1
   ────────────────────────────────────────────
           Faber, EPUB, 2010
               Read
   ────────────────────────────────────────────
       Latin text
   ────────────────────────────────────────────
           Bloomsbury, 2003
               lounge

Faint lines separate its entities, unlabelled, and each level's indented
under the one above, so that it's clear what belongs to which, without
anybody needing the words: the Work, its Expressions, their
Manifestations, in order of date, whether they're paper or digital, and
their LRM Items.  An LRM Item that's digital says Read, as its
Manifestation has its format.

Every Expression's shown, so a translation can be seen for what it is,
even with nothing known of it, so that its links have a place.  The one
that was opened, or the first, comes first, and it's the one E edits:
another's opened by its link, and edited there.  The form has the same
divisions.  An Expression's own title, if it has one, as a translation
does, is its label: Wikidata's title (P1476).  It's not the Work's, so
Search finds a book once.

Dim links add each level: **+ Add Expression** under a Work's last,
**+ Add Manifestation** under an Expression's last, and **+ Add Copy**
under a Manifestation's last.  Each opens a short form of that level's
fields, and Ctrl+S saves it.  Add Manifestation has the add loop's ISBN
box.  A new Manifestation has no copies until one's added, as not every
edition catalogued is one you have.  A digital copy comes from its item,
by B, so a Manifestation with one has no Add Copy link.

A Manifestation can have a cover, an image item (a local *cover*
property, which links to its entity, so the image's entity says it's the
*cover of* the Manifestation), shown small with it.  A copy's *content*
links to its item in the same way, as *content of*.  One from before
links, a String of the item's identifier, on a property relabelled
*(identifier, old)*, is still read, and made a link when it's saved, or
by Give Items Entities (see :doc:`item`).  B takes an EPUB's own; an ISBN lookup
fetches Open Library's, through ``darq.http``, after it's added; and a
Manifestation's Edit form can take one from an image file on the host.
A cover that can't be had isn't a failure.  MOBI and AZW files don't say
which image is their cover.

Names are offered as they're typed, from what's here: people for
authors, translators and editors, organisations for a publisher, and
other entities for a language or series.  In a field of several, it's the
last that's completed.

An Edit link beside each level opens the same form, filled in, wherever
it is on the card, with a Remove button, and, but for a Work's, a Move
button, which moves it to another book, chosen by its title: an
Expression to its Work, a Manifestation to one of its Expressions, and a
Copy to one of its Manifestations.  Two Works are merged by moving one's
Expressions to the other, and removing it.  The form  Only something with nothing
under it can be removed, once it's been confirmed, so a branch goes a
level at a time.  Removing a digital copy takes it out of the catalogue,
and leaves its item alone.  An add that fails clears away what it made.

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
near-identical UI, sharing their keys, and what they say about them, in
``darq.ui.reader``.  The real difference is that + and − change the size
of the text, rather than zoom.

* It's distraction-free, with hotkeys for turning the pages.

  * Page-forward: Space, Page Down, C-f
  * Page-back: Backspace, Page Up, C-b
  * Home and End go to the front and rear covers
  * O shows the contents, C-s finds, and B catalogues it
  * S-x closes it, as with any window

* M goes through dark, sepia and light modes, in the e-book lens.  A
  PDF's pages are as they are.
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
content (``FindByValue``).

* If there is one, it starts in the reading view: a page at a time,
  fitted to the window, with the keys above, and a way to the Book card.
* If not, or the KB isn't there, it's the view it has now: a continuous
  column, fitted to the width.

Cataloguing switches it at once, and V switches it by hand either way.
In the plain view, C-f finds, as well as C-s, as the keys are only the
reader's in the reading view.  Lookups aren't added to opening other types, and an item's
identifier begins with its type, so giving catalogued PDFs a type of
their own would change their identity, and break what links to them.

A changed PDF, fetched again, is a new item, which isn't catalogued, so
it opens in the plain view: it may well be a new edition.  Offering to
add it, through the URL's snapshots, can come later.

The E-Book Lens
~~~~~~~~~~~~~~~

``lenses/ebook`` renders with Qt WebEngine, which is Chromium, and takes
what the type gives it through a custom ``book:`` URL scheme, so nothing
is written to disk.  Pages are CSS columns, as in Calibre's viewer.
They aren't scrolled to, as the last can't quite be reached: the body's
moved with a transform, so every page is an exact number of windows
along.  Without a doctype, the body's width is the viewport's, which
changes with the transform, so it's measured without one.  Paging's in
``ebook_pages.py``, which uses no Qt, so it's tested without Chromium.

A part's laid out, and moved to its page, once it's loaded, but
Chromium can paint it before that: as the book styles it, light, and at
its first page, even when it's arrived at backwards, at its last.  So
the ``book:`` scheme serves each document with the layout and the mode's
colours already in its head, and its text hidden, which keeps its
layout, until it's at its page, when the style goes.  The page's own
background is the mode's, for the moment between parts.

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
* Page turns cross parts, and going back arrives at the last page of
  one.  A link within a book's the lens's to follow, to a part and a
  fragment's page.  The wheel turns pages.  Calls into the page are
  asynchronous, and a refused link reports a failed load, which is
  ignored.
* WebEngine won't start without a program name, so unlike other lenses'
  its ``QApplication`` is given one, and its URL scheme's registered
  before there's an application.

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
``libmobi0t64``, all in ``bin/install``.  ``requirements.txt`` pins
``PyQt6``, ``PyQt6-WebEngine`` and ``PyQt6-Pdf`` together, so a
development environment has the dist's Qt: from 6.12, Qt PDF's bindings
are ``PyQt6-Pdf``'s, not ``PyQt6``'s.  Upgrading from before 6.12 can
lose them, as the old ``PyQt6`` takes its own with it, so
``bin/install`` reinstalls ``PyQt6-Pdf`` if they're missing.

Whether the web lens of Third Edition uses this or CEF is its own
decision (see ``types/html``), which this bears on: sharing an engine
saves a second copy of Chromium, and control of keys and mouse has been
fine with a book, though not yet tried with real pages.

The Order of Work
-----------------

1. The kinds, and the Book card.  (Done.)
2. The LibraryThing import.  (Done: see :doc:`librarything-import-tool`.)
3. The add loop, and ISBN lookups.  (Done.)
4. ``types/ebook``, ``lenses/ebook``, ``darq.ui.reader``, and the
   PDF lens's reading view, starting with a real lens under ``pyloader``.
   (Done.)
5. Importing e-books: Import File, and B, one at a time, which is
   enough.  A Calibre library's links between formats, and its tidied
   details, aren't used.

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

* What a book with DRM should show: it's kept, it says so, and B still
  catalogues it, as ``tests/test_ebook_type.py`` has it, but a real one
  hasn't been tried.
* Whether MARC21 is worth supporting as an import or export format.
* Whether location is free text, or a structure of its own.
* Whether a collection browser belongs to the object selector, to a
  collection type, or to a type's own specialisation of them.
* A later "this is a newer version: add it?" for a re-fetched PDF.
* ``Citable``, for papers as well as books (see :doc:`bib`): cataloguing
  could become "Catalogue as ...", with Book as its first choice.
