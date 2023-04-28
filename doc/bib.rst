Bibliography Type
=================

This is a type that, in fact, can overlap with the book catalog.

Basically, it should replicate the BibTeX types, perhaps with some
modernisation (if BibTeX hasn't caught up with the web yet).

And there should be a Paper type.  It should have the bibliographic
data, plus the PDF content.  But the web of citations should probably
be done via the bibliography, not the paper itself, I think?

So the paper viewer should really just display the PDF, but with its
source data.

The bibliography viewer should show the web of citations.  Ideally,
it'd hook into CiteSeerX / Google Scholar / etc, and pull data from
there to augment its local store.

In some ways it might be good to have them unified: a UI with the
citations beside the paper, and the bib data at the top, or whatever.

But .., really, that just suggests that the model is wrong.  Perhaps
they're not _separate_, but rather polymorphic, inherited attributes?

Perhaps papers, like books, should be derived from a fundamental type
that is a Citation (or Cite-able?)?  It seems kinda weird, but as an
interface-style secondary type it doesn't seem too bad?
