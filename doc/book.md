# Book Type

There are several types of books supported by the book type, but they
generally fall into the categories of paper book or e-book.

Paper books are reflected essentially as a catalog record, and use the
metadata service to store their data.  This should include the usual
bibliographic data, plus eg. shelving data for the physical volume, and
a cover image.

e-Books, on the other hand, have an additional property: a reference for
the actual content.  I'd like to support PDF, epub, and perhaps mobi-
formatted content.  Perhaps using an existing library to parse those
formats?

* Lector is a PyQT5-based e-book reader.  It's not great, but it's
  likely to be a good start.
* PyMuPDF is a Python/C++ PDF renderer (which Lector uses).  It might
  be a better basis, given how flakey Lector is?

The metadata should include:
* Title
* Author(s)
* Editor
* Publisher
* Date published
* Date purchased
* Cover photograph
* ISBN
* URL (for ebooks)
* Location (for paper books)
  * Is this a good place to use an encapsulated volumes model for
    physical locations?
  * It'd be good to have some sort of structure here, not just free
    form text
* Notes
* Type
  * Is there a hierarchy here?  Book, with sub-types for electronic
    and physical?

Creating a new book should be a process that's launched in the usual
way from the factory menu, but the type implementation should support
lookup (eg. Amazon, LOC, NLA, BL, whatever; and the Dublin Core from
epub, etc) so you can populate all the metadata without needing to type
it all in manually.

The functioning of the book editor suggests to me that the ebook type
should be separate, because the _data_ is entirely different, and so
I think it makes sense to have it implemented separately, even if in
the same executable ultimately.

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

## Book-like objects

An eBook reader, ignoring for a moment the collection interface, is
really quite similar to a PDF viewer.  Books have structure (volumes,
chapters, and thus a table of contents, perhaps a table of
illustrations, index, etc) and then words and (possibly) images.
Which is basically identical to a PDF document.  Some eBooks even have
hyperlinks, just as some PDFs do.

And, for *some* PDF documents, the reading process is quite similar
too.  That's not necessarily the case for, eg. a semi-conductor
datasheet, or a glossy trade brochure, but for research papers, or
manuals, the reading process is quite similar.

I *think* it would make sense to combine these into a single viewer
application, with (consequent) consistency of interaction.

## Types of Book-like Objects

Well, in particular, book-like objects.

* Start with "document"?
  * From there, there could reasonably a tree of descendants, both
    "plain" and "rich", with PDF amongst them.
  * But .. no-one actually _writes_ PDF documents: they're an output
    format.  Ideally, I'd have the source form somehow associated with
    the PDF output, where that's possible.
  * Word docs, for instance, are edited and rendered in the same
    tool.  LaTeX documents, as an example, have a very different
    process.
    * Is this distinction to be reflected in the type system?
      * It's similar to an interpreter/compiler split in programming?

* Examples that I care about:
  * Plain text
  * Markdown
  * Restructured Text
  * ASCIIdoc
  * *roff
  * LaTeX / TeX
  * PDF
  * Word
    * .doc
    * .docx
  * _maybe_ ODF?
  * RTF
  * mobi
  * epub
  * azw (? Whatever the Kindle format is)
  * HTML (? eek ?)

* But there are different axes here:
  * PDF vs. DOC vs. ROFF vs epub
  * Brochure vs. datasheet vs. research paper vs. book
  * Can UTI deal with this?
    * I think so
  * But, given an object whose metadata says "It's a paper, in markdown
    format", can I chose a type implementation using the multiple axes?

## Implementation

I think books end up being a useful example of how objects work.

Take an e-book, let's say in ePub format.
* A sequence of bytes
* KB entities, for the paper, the authors, institutions, etc.
* Metadata describing it's type(s), and maybe other stuff.

How does this _actually_ work?
* Metadata has:
  * Object identifier
    * This is probably the primary key?
  * BLOB storage identifier
  * KB entity identifier

I _think_ this is probably a decent view of how _everything_ should
work?  The metadata service is basically the object name service,
linking object identity with its other properties, and doing away with
a name service on the side.

## Roadmap

The Book type is a useful example of a category of types where it's
necessary to have sub-type implementations.  On that basis, and given
the ready availability of a Python/Qt e-reader codebase, I think it
makes sense to advance the Book type in the roadmap to explore how
sub-typing and specialized collections can work.

* Implement the basic physical book type
  * A simple collection of (meta)data about a specific book
  * Presentation like a card catalog, but with a cover picture?
  * Type constraints in the selector UI

* Implement a specialized collection type for books.
  * Figure out how these will actually work
  * Come up with a nice UI
    * Incorporating cover art is probably the right approach here.
      Books can be quite distinctive, and it can be a searching
      strategy in itself.
    * Coverflow (Apple-style) really replicates albums more than
      books, but ... it would like work ok?
    * A 2D grid of covers, able to be zoomed in/out, might also be
      good?
      * And perhaps better than coverflow for rapid scanning?
      * Is it possible to sort images by colour (surely, yes)?  How
        about by shape or object?  Maybe some ML involved there?

* Add an e-book type
  * Basically the same data in type instance, but with the addition
    of a reference to the actual content in the storage server
  * Figure out how to model adding a new e-book to the system vs.
    actually creating the e-book content
    * Since this is a bunch of UI work I don't want to get into yet
    * And I don't have a good handle on how it should be modelled
      either.
      * It's possibly the same model as songs and videos, where
        you create a record for the physical object, but don't
        create the object itself?
      * So perhaps the type should be called BookCatalog, not Book?
        * BookRecord?  BookDetails?  CatalogedBook?  BookInfo?

## UI

Fields:
* Cover image
  * Spine image?
  * Rear cover image?
  * Whatever the internal fancy front page thing is called?
* Title
* Series Title
* Series Number
* Author(s)
* Publisher
* Place Published
* Date Published
* ISBN
* Format
* Edition
* Pages
* Dimensions
* Language
* Date Purchased
* Genre
* Dewey
* LCC
* Summary
* Location

Notes:
* Not every book will populate every field.
* Title and Author should use a larger font, especially title.
* It'd be good if authors, publishers, plaec published, etc, had
  completion based on previous entries.
* Should this be using the MARC21 (MARCXML, MARCJSON, ISO2709) standard
  for bibliographic data?  it's potentially a good idea ...
* Examples:
  * Books.app (macOS, was iBooks))
  * Calibre (macOS)
  * iTunes (macOS)
  * Bookpedia (macOS)
  * Clearview (macOS)
  * Kindle (macOS, iOS)
  * Lector (macOS, Windows, Linux)
