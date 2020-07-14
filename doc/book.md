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
  