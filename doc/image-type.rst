Image Type
==========

The image type encompasses any form of still image item: GIF, PNG,
JPEG, RAW, etc.  It does not include Postscript or PDF, which will
have their own type.

The image lens should display the image in its native pixel size, with
controls to scale the presentation (but not the data) up or down.

Editing Images
--------------

For now, let's keep it simple:

* Scale
* Crop
* Rotate

  * Easy ways to rotate 90 degrees would be good

* Flip up/down, left/right

Future editions might add more complex editing, working up to the
capabilities of something like GIMP.

Future editions should also figure out what to do with animated image
collections (like an animated GIF).

Metadata
--------

In addition to the image itself, the view should also have a way to
display and edit any associated metadata encoded in the image.

Future editions should determine how that metadata should be
integrated with Darq's metadata service.

Types
-----

Each image format is a type of its own, with Apple's Uniform Type
Identifier, conforming to the abstract `public.image`, which has no
implementation.  An item's type is part of its identifier, so an
item's format is fixed: converting a JPEG to a PNG makes another
item.

=======================  =====================  ======================
Type                     MIME type              Extensions
=======================  =====================  ======================
`public.png`             `image/png`            png
`public.jpeg`            `image/jpeg`           jpeg, jpg, jpe
`com.compuserve.gif`     `image/gif`            gif
`org.webmproject.webp`   `image/webp`           webp
`public.tiff`            `image/tiff`           tiff, tif
`com.microsoft.bmp`      `image/bmp`            bmp, dib
`public.heif`            `image/heif`           heif, hif
`public.heic`            `image/heic`           heic
=======================  =====================  ======================

`public.heic`, a HEIF image coded with HEVC, as phones take photos,
conforms to `public.heif`.  One implementation, `types/image`,
implements them all, and one lens, `lenses/image`, is registered for
`public.image`, so it's found for each of them by conformance: a new
format needs only an entry in `type.json`.  Camera RAW formats are for
much later.

Items
-----

An item is its *original*, the data it was created from, which is
never changed, and a list of *edits*.  The image is the original with
its edits made, in order, after the original's EXIF orientation, and
saved in its type's format.  So:

* a JPEG is re-encoded once for each rendering, not once for each
  edit, so its quality doesn't wear away, and it's re-encoded with its
  own quantization tables, so it keeps the quality it had;
* undo, redo, and revert to the original are a shorter list of edits;
* with no edits, the image is the original, unchanged.

An item is created from data with `CreateFrom` (see :doc:`types`),
which checks that it's an image of the type, or empty, with `Create`,
until it's replaced by one created from data.  Its identifier is its
type and its Storage blob's key, as a text item's is.  The blob is a
header (`DIMG`, a format version, flags, including *destroyed*, and the
edits' length), the edits, as JSON, and then the original.  Changes are
saved a little after they're made, and when the last client closes the
item, as text items' are.  `Export` returns the image, rendered.

The edits are:

* crop, to a rectangle;
* scale, to a width and height;
* rotate, by quarter turns, clockwise or anticlockwise;
* flip, left to right, or top to bottom;
* set a metadata field, or remove it; and
* remove the location.

Each is checked against the image as the edits before it leave it: a
crop must be within it, say.  Images are decoded with Pillow, with its
limit on pixels, against decompression bombs, and `pillow-heif`, for
HEIF and HEIC.

Metadata Fields
~~~~~~~~~~~~~~~

All of an image's metadata is shown: its size and format, EXIF, GPS
(with its location in degrees), PNG text, a GIF's comment, its ICC
profile, and XMP.  A few fields can be edited, each kept where its
format keeps it:

==============  ================================  =================
Field           EXIF (JPEG, WebP, TIFF, HEIF)     PNG text
==============  ================================  =================
Title           ImageDescription                  Title
Artist          Artist                            Author
Copyright       Copyright                         Copyright
Date taken      DateTimeOriginal                  Creation Time
Comment         UserComment                       Comment
==============  ================================  =================

A GIF has only a comment, and a BMP none.  EXIF's text tags are
ASCII: they're written as UTF-8, as exiftool writes them.  A TIFF
keeps them all in its first IFD.  *Remove location* removes EXIF's GPS
tags, and XMP, if it has a location, since XMP can't be edited:
Pillow reads it, but doesn't write it.  An image's other metadata, and
its ICC profile, are kept as they were.

Protocol
--------

An image item's port serves the `Image` protocol, in
`lib/didl/image.didl`:

* `Get()` returns the image, with its size, frames, edits and version.
* `Stat()` returns its size, frames, data size and version.
* `SetEdits(edits, expected)` replaces the list of edits, if the
  image's version is the one expected: `CONFLICT` if it isn't, and
  `INVALID_EDIT` if one doesn't fit.
* `GetMetadata()` returns the image's metadata, with its edits made, as
  text entries, marking the fields that can be edited.

An empty item is `EMPTY`, and an animated image, with several frames,
is `ANIMATED` to any edit: for now, it's shown at its first frame, and
can't be edited.

The Lens
--------

The image lens shows the image at its native pixel size: one image
pixel to one device pixel, on a high-DPI screen too.  Its window has
no borders, as the other lenses' haven't, and no status line: the
image fills it, and dragging the image moves it.  Zooming (+ and -, 0
for actual pixels, F to fit the window, or Ctrl and the wheel) changes
the view, never the image: zoomed out, it's smoothed, and zoomed in,
its pixels are kept square.  A new zoom is shown in the middle of the
image for a moment, and messages, such as why an edit was refused, at
its foot.  The information panel has the current zoom and the number
of edits, above the fields.

Its keys edit: [ and ] turn it left and right, H and V flip it, C crops
(drag a rectangle, then Enter, or Esc), and S scales, keeping the
aspect ratio, unless it's told not to.  Ctrl+Z and Ctrl+Shift+Z undo
and redo, and its menu has *Revert to Original*.  Each change is sent
as it's made, so nothing's unsaved.  I shows the information panel:
the fields, to edit, *Remove Location*, if it has one, and every
entry, as plain text.  The lens records `opened`, and `edited` once,
when it's first edited.

Searching
---------

Each time an item's saved, and when it's created, it's published to
the Index (see :doc:`search-service`): its title, artist and
copyright as names, and its comment, date taken, and format (*JPEG
image*) as its body.  They're read from the original's metadata, with
the field edits made, without decoding its pixels, so publishing
doesn't render the image.  An empty item is published with nothing,
and a destroyed one is withdrawn.  `IndexEntry` gives the same, for a
reindex.

Implementation
--------------

* `types/image/imaging.py` has the formats, and checks, renders, and
  reads the metadata of images, with Pillow: it knows nothing of items,
  so it's tested alone.
* `types/image/main.py` is the implementation: the items, their blobs,
  and the protocols.
* `lenses/image/main.py` is the lens, which decodes images with Pillow
  too, since Qt can't decode HEIC.

The tests are in `tests/test_image_type.py` and
`tests/test_image_lens.py`.
