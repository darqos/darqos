# Document Type

There are really multiple types of document I'd like to deal with, and I
think they're actually different types:
* Plain text
* Rich media notes
* Word processor / desktop publishing documents

The exact boundaries between these types are a little fuzzy.  It's
possible that there ends up being only two actual file formats, but
that the Lens of choice might vary.

## Plain Text

* UTF-8 format seems like the best thing for this, but ...
  * That might imply bidirectional text support, and possibly
    top-down support too, which is a bit of an extension over the
    traditional ASCII-only text doc.
  * I think the key factor here is that the content must be able to
    be rendered using a single, standard font face.  So no bold,
    italic, underlines, strike-through, larger or smaller sizes,
    etc.

## Rich-media Notes

* A combination of rich-text (with different sizes and styles) with
  embedded vector graphics and bitmap images.
* Quick and simple to format, with a bunch of default behaviour,
  aimed at note-taking.
  * So stuff like:
    * numbered lists
    * bulleted points
    * HTML-style definition lists
    * Headings
    * Links (and anchors?)
    * Footnotes
    * Tables, with good captions and labels
* Support for sketches and diagrams
  * Lines
  * Shapes
  * Arrows
    * And attachment to endpoints
  * Maybe a grid-style background for diagramming?
    * Or even the whole thing?
* Self-review
  * Highlighting
    * Including highlighting within diagrams, not just text
  * Notes
    * Maybe PostIt-style layered boxes?  Even with a background colour?
* It might be good to have Jupyter-style embedded code and output
  support for these docs, but not necessarily on day 1.
* The UX here is the critical thing: it must be *easy* to edit both
  text and diagrams, together, without all the usual messing about
  that you have to do with Visio or Illustrator, etc.
* Maybe two basic modes:
  * Infinite scroll, with no pages boundaries
  * Paged
* Use DTP-like boxes for content, one of which has the focus while
  editing
  * Text boxes are chained to flow text between them
  * Boxes are extended in a default direction as the content expands
    * When a box hits the end of its page, a new box (and page) should
      be added with flow-linkage automatically established between
      them
* Image import
  * Show as a new floating layer
  * Support resize
  * Support crop
  * Support rotate
  * Support transparency
  * Pop out to image editor for actual modification
* Text boxes
  * Basic text direction (left-to-right, right-to-left, top-to-bottom
  * Follow path
    * For round labels, wavy lines, etc
  * Needn't be rectangular
    * Other shapes (circle, oval, parallelogram, whatever
  * Can add or subtract other shapes to form a new boundary path
    * Useful for flowing around images, etc
* It must be possible to end text as if it were a plain text document
  in exactly the same way without running into any of the rich-text or
  media functionality.
  * A Single, page-sized, text box should exist by default
  * As pages are added, boxes are linked to the previous page's box
    automatically
  * Maybe it's even possible to use a single Lens, just with
    functionality restricted depending on the underlying type?
* It must be trivial to import and export text
* Export/Save as
  * PDF
  * SVG
  * HTML

## Word Processor / DTP

* Adds to the rich-media document type
* Styles
  * Character styles
    * Font
    * Weight
    * Decorations
  * Paragraph
    * Indents
      * Including hanging
    * Margins
    * Tab-stops
    * Text direction
  * Page
    * Margins
    * Orientation
    * Header / footer
    * Watermark
    * Even / odd
    * Columns
  * Sections
    * Style boundaries, esp for pages
* Templates
  * Style collections for multiple documents
* Books
  * Collections of individual documents
  * Override of page numbers, style definitions, etc
