ReStructured Text Type
======================

At this point, Darq is using ReStructured Text markup format for its
internal documentation.  While reST is not the most intuitive format,
it's a little more capable than the alternatives.

There are three major internal uses:

* Online help.

  The content of the help panel in all Lens and Tools.  It provides a
  quick reference for the implemented hotkeys, followed by summary
  information for any other topics needed.

* Manual pages.

  Each Lens, Tool, and Type needs to have a reference manual.  This
  provides a more detailed description of the functionality of these
  items, and gives all the details needed to operate the thing.  These
  should be linked from online help, and available via Index
  searching.

* Papers and Guides.

  Longer documents describing the motivation, exploration, and
  approach of the system in different areas.  These don't give direct
  information about the usage of system elements, by describe *why*
  they're like they are and record the development process including
  references to related or contrasting approaches.  They might also
  include more tutorial-style documents.

  Again, these are Indexed, and should be surfaced by appropriate
  keywords, providing background context.

ReStructured Text is implemented in Python's ``docutils`` module,
which provides parsers and renders for HTML, LaTeX, nroff, and others.
Notably, this type does NOT suppor the Sphinx system's extensions,
which seem to be mostly aimed at rendering multi-file collections into
a single website or PDF.

The major issue with any markup format is that image or other
inclusions are in a separate "file".  In this case, such inclusions
should refernce other Items in the editing view, and launch their
lenses to edit them.  The rendered view will put them inline.

Because Darq will never *delete* an item, and because items are
uniquely identified by their Item Identifier, there's no need to embed
the eg. images within the document storage.  References should use the
file IID, and the renderer will need to intercept the Type ID (which
will look like a URL scheme) and look up the content.

Type
----

* Standard entity base wth storage blob to contain the text.
* Don't support ``.. include`` directives (?)


Lens
----

* Qt WebEngine-based viewer for reST items

  * Transform the raw markup to HTML5, then render it onscreen.

* Intercept images
* Intercept external references
* Likely adopt a UI pattern like PyCharm: the window will either show
  the raw text alone, the rendered HTML alone, or a synchronised
  side-by-side rendering of source text and rendered output.

  * Default to showing rendered output on start
  * A switch to edit mode might show both by default, but have a
    cycling view mode to switch between the three manually.

* Respect the ``:stylesheet:`` directive to specify the appropriate
  rendering for help, manpage, and guide formats.
