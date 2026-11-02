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

   
Within this framework, there are (at least) three relevant Darq Item
types that will be needed:

* For e-books, an actual blob containing the PDF, AZW, Mobi, ePub,
  etc, data.  These will be *LRM Items* in the WEMI hierarchy, and will
  have specific *Manifestation*, *Expression*, and *Work* parent
  relationships.
* For paper books, an *LRM Item*'s Darq Item will be a record
  representing the existence of a particular real paper book.  It will
  combine the links to the WEMI hierarchy with details about its
  physical location.
* Citations, from papers and bibliographies, will likely be KB links
  to an *LRM Manifestation*, *LRM Expression*, or *LRM Work*,
  depending on the specificity of the reference.  Page numbers imply a
  *Manifestation*, whereas an author and title might just be a *Work*.

So, an e-book or audio book LRM item will be a Darq Item, with the
implementation using a Storage blob to maintain the content.

A paper book's catalog record will probably implemented using the KB
(or perhaps, Metadata?) backend to store information about where it is
(or should be).

Works, Expressions, and Manifestations should probably be implemented
using the KB backend, because it's ideal for keeping track of their
interrelationships with other entities, and maps well to the RDF
triples of the BIBFRAME implementation.

Types
-----

LRM Work
~~~~~~~~

* The LRM Work type, as a Darq Item and KB Kind.
* UTI: ???
  
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

* The LRM Expression type, as a Darq Item and KB Kind.
* UTI: ???
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

* The LRM Manifestation type, as a Darq Item and KB Kind.
* UTI: ???
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


Item
~~~~
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

* An item for various e-book format data (.mobi, .epub, .azw, etc)
* Show allow PDF also
* Conforms to LRM Item
* Attributes
* Should be implemented as a combination of a KB entity with
  associated relationships, and a Storage element to maintain the
  e-book content.
  
Book Record
~~~~~~~~~~~

* A "catalog card" item for a real physical book instance.
* Conforms to LRM Item
* Attributes
* Should be implemented using the KB service for persistence, and for
  simple relationships with the related entities, like people,
  organisational authors, publishers, places of publishing, etc.

  * All content should reasonably go in the KB service.

* Creating a new book record should be a process that's launched in
  the usual way from the factory menu, but the type implementation
  should support lookup (eg. Amazon, LOC, NLA, BL, whatever; and the
  Dublin Core from epub, etc) so you can populate all the metadata
  without needing to type it all in manually.


Lenses
------

The base UI is essentially a table of name:value pairs.  Some
sensitivity in laying out authors, title, etc, will give a decent
result.

UI Fields
~~~~~~~~~

* Not every book will populate every field.
* Title and Author should use a larger font, especially title.
* It'd be good if authors, publishers, place published, etc, had
  completion based on previous entries.
* Should this be using the MARC21 (MARCXML, MARCJSON, ISO2709)
  standard for bibliographic data?  it's potentially a good idea ...
* Examples:

  * Books.app (macOS, was iBooks))
  * Calibre (macOS)
  * iTunes (macOS)
  * Bookpedia (macOS)
  * Clearview (macOS)
  * Kindle (macOS, iOS)
  * Lector (macOS, Windows, Linux)
    

Work
~~~~

I think this can fall back to the generic KB entity lens.

Expression
~~~~~~~~~~

I think this can fall back to the generic KB entity lens.

Manifestation
~~~~~~~~~~~~~

I think this can fall back to the generic KB Entity lens.

Item
~~~~

I think this can fall back to the generic KB Entity lens.  Note that
most LRM Items will in fact be E-Book or Book Record instances, and
this will be left for things like an Audio Book which doesn't have a
specialization.


E-Book
~~~~~~

The e-book lens (lenses/ebook) facilitates reading an e-book.

* It should have a distraction-free UI with hotkeys for turning the
  pages.

  * Page-forward

    * Space
    * Page Down
    * C-f

  * Page-back

    * Backspace
    * Page Up
    * C-b

  * Home / End should go to front and rear covers

* The text size should be able to be increased or decreased for
  reflowable formats (ie. not PDF).
* PDF should zoom to fit in the window.
* It should remember the last page being read for each book, and
  default to starting from there when opened again
* In a future edition, it should support annotations, but not yet.
* It should offer a dark-mode, sepia-mode, and light-mode choices for
  rendering the pages.

It should support:

* PDF
* ePub
* Mobi
* Whatever is possible with Amazon's azw formats

Note

   The E-Book lens does not, currently, implement editing of its
   content, other than that of the WEMI properties: you cannot alter
   the actual content of the book with this (yet).

   In a future edition, it might support full e-book editing.

Implementation:

* Lector is a PyQT5-based e-book reader.  It's not great, but it's
  likely to be a good start.
* PyMuPDF is a Python/C++ PDF renderer (which Lector uses).  It might
  be a better basis, given how flakey Lector is?


Book Record
~~~~~~~~~~~

This is essentially a card catalog entry for a physical paper book.
It describes the Item properties of that book, collected from the
Item, Manifestation, Expression, and Work.

This lens should support both viewing and editing.

When creating a book record, it would be great to allow a photo/video
of the ISBN bar code to automatically pre-populate the other properies
of the record.

It should also be possible to add front/back cover images to the book
record.

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

