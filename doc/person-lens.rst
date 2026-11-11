Person Lens
===========

The Person type covers a wide range of uses of person entities, from
contacts for people you personally know, through relatives in a family
tree, authors, musicians, to famous people from history. Consequently,
the lens for a person type has to be quite flexible.

For people outside our immediate family and work circles,
synchronizing with Wikidata can also bring in a lot more data about an
individual.

Views
-----

The person card should have multiple views of the underlying data.

* It would be great to enable invoking apps to suggest an opening view
* The view should have a hotkey to cycle through the possible views

The following views are probably not a complete set, but representative:

* Detailed

  * Shows, in a nicely rendered format, all the expected properties of
    a Person type.  Properties that have been manually added with the
    Entity Browser and are not usual for Person types, won't be shown,
    although there'll be a small note that some exist and a link to
    the entity browser at the end of the card.
  * This should be the editing view too, I think.

* Contact

  * Shows summary info about the Person (names, employer, title,
    thumbnail image)
  * Shows list of linked Places
  * Shows email, phone, and IM identifiers
  * Show birthday
  * Show notes field

* Professional

  * Shows summary info (names, current employer, title)
  * Work Place
  * Work contacts (phone, email, IM)
  * Shows list of employment relationships
    
    * Title, company, start and end dates
      
  * Shows list of education events

    * Institution, degree/program, start and end dates

  * This is basically the "LinkedIn" view of someone

* Family

  * Names
  * Significant dates

    * And their event locations
    * Including source statements
      
  * Parents, spouse/partner(s), children

    * Including source statements

Properties
----------

*label*
  Standard entity property

*description*
  Standard entity property
  
*also known as*
  Standard entity property

*instance of*
  *human*


   
      
    
* Name

  * Given and Family names, if present

    * Q: how should we deal with ordering here, eg. for Japanese people?

  * A nickname, if present
  * Other names

    * Perhaps a former name, from marriage or transition
    * Titles or honourifics
    * Just general aliases, I suppose

* Image

  * A canonical photo or image

* Profession

  * For historical personages, this can be a general description of
    the thing they did, but for contacts, it's perhaps better that
    it's their current job title and company?


Thumbnails
----------

The person's image (*image*, P18) is shown on their card, which needs
a thumbnail of an image item.  That's deferred until this lens is
built, so the thumbnail work is designed once, for every lens that
wants one, rather than piecemeal: the entity lens could then show one
beside an image-valued statement, a web link's snapshot, or a related
person's card.

* **A Thumbnail call.**  Add ``Thumbnail(size) -> (png, version,
  status)`` to the Image protocol (``lib/didl/image.didl``).  The image
  type implementation already decodes every format, HEIC included, and
  applies the edits, so its thumbnail matches what the image lens
  shows; it returns PNG, which Qt shows without Pillow.  It can keep
  each thumbnail, by version, so it's made once.  Whether other types
  have thumbnails too, and so whether this belongs in a shared protocol
  rather than Image's, is part of the design.
* **Not blocking the card.**  Opening an image item can start its type
  implementation, so a thumbnail's fetched in the background, and
  shown when it comes; what can't be shown leaves a link.
* **Clicking.**  The thumbnail opens the image in its own lens; the
  link to it opens its page.
* **The rendered image, kept.**  An image is kept as its original and
  a list of edits, and rendered, with the edits, when it's opened (see
  ``types/image/imaging.py``), so the original's never re-encoded more
  than once.  If thumbnails, or the Index, ask for edited images often,
  the rendering could be kept in the item's blob too, replaced when the
  edits change, so opening one only reads it.  Edits can't be kept as
  reverse edits from the latest, instead: crops and scaling discard
  what's outside them, and a JPEG would lose quality with each edit.
