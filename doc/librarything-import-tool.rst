LibraryThing Import Tool
========================

This tool imports a library from LibraryThing's JSON export, as a
book's four levels (see :doc:`book`).  It's not a Service: it's a
one-shot tool, that can be run again, and it does *not* export anything
from Darq to LibraryThing.

It's in ``tools/librarything-import``, started from the development
Terminal's Tools menu: choose the export, and press Import.  Its summary
says what was made, and what's to be checked.

What It Imports
---------------

================================================  ==============
Field                                             Goes on
================================================  ==============
``workcode``                                      Work, grouping
``title``, ``originaltitle``                      Work
``authors``, as authors                           Work
``series``, ``ddc``, ``lcc``, ``subject``         Work
``language``                                      Expression
``authors``, as anything else                     Expression
``publication``, ``date``, ``isbn``, ``pages``    Manifestation
``format``, ``physical_description``              Manifestation
``asin``, ``lccn``                                Manifestation
``books_id``, ``dateacquired``, ``fromwhere``     LRM Item
``price``, ``tags``, ``copies``                   LRM Item
================================================  ==============

* A title's split at its first colon, into title and subtitle, and
  ``originaltitle`` is an alias of the work.
* Records of one ``workcode`` are one Work, and of one language, one
  Expression.  Records of one ISBN are one Manifestation, so two copies
  are two records, and a record's ``copies`` makes that many LRM Items.
* Roles are in several languages, with HTML escapes: they're mapped to
  author, editor and translator, and any other's kept, with its text, on
  a contributor.  One with no role is an author.
* ``format`` only says *paperback* or *hardcover* for some: the others,
  such as *Paper Book*, are left out.
* HTML entities are resolved in everything.
* ``summary`` is left out, as it's a citation LibraryThing makes, and
  ``awards``, ``genre``, the dimensions and weight, which
  ``physical_description`` mostly covers.

``publication`` is free text, in two forms, ``Publisher (1999), Edition:
Reprint, 300 pages``, and a library's, ``London : Picador, 1982.``, or
``London, Picador, 1982.``  It's always kept as it is, as *publication
details*.  One with no punctuation, such as ``London Fontana 1974``,
can't be split into a place and a publisher, so it has none: they're
listed, to be added by hand.

The export says nothing of where a book is, as ``collections`` is only
*Your library*, so a copy has no location.

Running It Again
----------------

Identifiers come from what things are: a work's LibraryThing's, a text
its work and language, an edition its work and ISBN, and a copy its book
identifier.  So importing again finds the same ones, and changes nothing
unless the export has.

Every statement cites LibraryThing, and is restated, as an address
book's are.  A statement that's been changed by hand stays changed: the
source's is kept, deprecated, and isn't stated again.

Books Already Here
------------------

An edition with a record's ISBN is taken to be the record's, along with
what it's an edition of, so a book that was entered by hand isn't made
twice: it gains what's new.  Its labels aren't changed, and where the
record differs, both statements are there.  These are listed.  A book
entered by hand with no ISBN can't be found, so it's made again.

People are found by exact name, and listed to be checked, as two people
can share one.  A publisher, a language or a series is found by name
too, or made.  A name that an earlier import made is found first.

The tests are in ``tests/test_librarything_import.py``.
