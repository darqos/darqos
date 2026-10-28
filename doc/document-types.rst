Document Type
=============

There are really multiple types of document I'd like to deal with, and I
think they're likely different types:

* Plain text
* Markdown / CommonMark / GFMD
* ReStructured Text
* AsciiDoc
* org-mode
* roff
* TeX / LaTeX
* RTF
* Microsoft Word 97 / XML
* ODF
* Postscript
* PDF

There's probably some more that I've forgotten.

This ignores eBook formats, which tend to be books rather than
documents.  And it ignores HTML, because while it's sometimes just a
document, it's often a lot more.

There's one key difference in these formats: some of them are WYSIWYG,
and some are not.  In other words, some have a building process, and
sources that are transformed into the final rendered item.  Others are
just rendered as is.  In some cases, like roff, they can be both.
Where the source format is visually different to the output, an
edit/render split is required.  Often, for built documents, more than
one component is required to generate the output.

Rendering
---------

In a demonstration of pointless perfectionism, I'd *like* the document
lens to correctly render and edit LTR, RTL, bidi, and vertical layout
documents.  Chinese, Japanese, and Korean can be written vertically,
then RTL.  Mongolian must be written vertically, and then LTR.  When
looking at a plain text UTF-8 document, it's possible to guess with
some accuracy which should be used (although an override for
horizontal layout is probably necessary).

In investigating paths to implementation, the following summary was
developed:

* Pango/Cairo is capable of doing LTR, RTL, and vertical layout, but
  does not lend itself to editing: it just does layout to a pixmap.
* GtkTextView and QtTextView both support LTR, RTL, and bidi Unicode
  text.  Neither of them support vertical text.
* Abiword's layout does not support vertical text
* LibreOffice Writer's layout *does* support all directions, but it's
  a monster to integrate
* CEF and WebKit (and I assume FireFox) support all directions.

In short, the only reasonable way to display and edit any directional
text is to embed a web browser.  This is a big dependency, with
significant security and storage & memory use issues.

WYSIWYG
-------

WYSIWYG formats can be edited in place, and have no need to treat the
source document separately.  The Lens can be a single paned window,
but there's substantial complexity in such a Lens, and it probably
makes no sense to use anything but LibreOffice for Word/ODF, for
example.  The only other WYSIWYG format is, ironically, plain text.

* Word97, OOXML, and ODF should all use LibreOffice Writer, possibly
  reskinned.  There's no point trying to do a web-based editor for
  such a large amount of functionality when Writer exists.
* Plain text should use WebKit or CEF, in order to get text direction
  support.

  * This will need some thinking about.  In early editions, the
    QtTextArea is almost certainly sufficient.
    
Single-source Rendered
----------------------

These documents have a singe source but the rendered output is quite
visually distinct.  I *think* the best thing here is a two-pane
window, source on left, and rendered view on the right.  The rendered
window is scroll-locked with the source, but cannot be altered.
Clicking on the rendered view warps the cursor to the matching
location in the source.  A view toggle (source-only, source &
rendered, rendered-only) might be nice.

Example of this class of document include Markdown, most ReST, and RTF.

Multi-source Rendered
---------------------

These documents have multiple source components that are transformed
into the output format.  This might include things like images,
biblographies, styles, or multi-part sources.

Often there are one or more tools required to create the output
format: ``make``, ``sphinx``, ``latex``, etc.  This build process is
likely to be quite complex, and to rely on a toolchain and a
traditional filesystem structure to manage the elements that comprise
the ultimate document.

I don't know how best to manage the toolchains here, but the document
sources should probably use a Project Type.  It might make sense to
extend the two-pane window to a third, left-size, tree-view panel of
the project, and to load the various components into the now-middle
pane for editing.  The right-most pane should be the rendered view.

This will likely be quite complex, and should be deferred to a future
edition.


Read-only Documents
-------------------

I'm not sure this is a good category, but Postscript and PDF are
largely "read only" documents.  And, at least in the short term,
that's certainly sufficient.

This class needs only a single pane window rendering the document.


Native Document Formats
=======================

During some later editions, I'd like to add some native document
formats that provide needed capabilities without the overhead and
disfunction of existing solutions.


Rich-media Notes
----------------

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

    * Maybe PostIt-style layered boxes?  Even with a background
      colour?

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

Word Processor / DTP
--------------------

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

