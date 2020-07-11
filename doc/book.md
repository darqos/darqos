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
  
  