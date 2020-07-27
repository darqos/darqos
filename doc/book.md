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
  likely to be a good start
  
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

## Roadmap

The Book type is a useful example of a category of types where it's
necessary to have sub-type implementations.  On that basis, and given
the ready availability of a Python/Qt e-reader codebase, I think it
makes sense to advance the Book type in the roadmap to explore how
sub-typing and specialized collections can work.

* Implement the basic physical book type
  * A simple collection of data about a specific book
  * Presentation like a card catalog, but with a cover picture
  * Type constraints in the selector UI

* Implement a specialized collection type for books.
  * Figure out how these will actually work
  * Come up with a nice UI
  
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
  