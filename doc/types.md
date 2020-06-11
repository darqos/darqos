# Types

One fundamental decision is to separate Text / Document and Code as 
different types.  Document should encompass everything from a README to 
FrameMaker, and the fundamental behaviour should be that it is displayed 
immediately as it is edited.

Code, on the other hand, undergoes a process of compilation or 
interpretation to generate some artefact.  The distinction is basically 
in the workflow, rather than the content (since a lot of the time, 
they’re both just an ordered collection of Unicode code points).

The interesting point is Markdown (and thus, TeX, roff, etc), where the 
Document is produced as an artefact of the Code.

One of the goals of M0 is to be able to create, show, and edit simple 
text documents.  That will require a simple text editor.  There’s some 
decisions to be made here:

* What, if any, boundaries are there between eg. a basic, unformatted text 
  document and a complex FrameMaker-style book?
* How does the system deal with:
  * ASCII/Unicode text files?
  * PDF documents?
  * Microsoft Word?
* In general, I think I’d like the approach to be one of importing via 
  translation to a native capability.  Which might, perhaps ideally, be 
  an implementation of an existing standard (de facto or de jure).

That said, I don’t really want to be writing a Word-compatible document 
editor: I’ll need to find a way to leverage eg. LibreOffice.

That aside, and for now, just considering a simple text editor, what is 
unique about this type implementation?

* Single tool that understands the type:
  * Creation
  * Editing
  * Diff and merge
* I’d like to have the GUI be a layer that simply drives the type API.
  * This implies that the API is available for programmatic access
  * Which should include scripting
  * So type implementations should be a library, with an API, and an 
  (optional?) GUI component that exposes the API to a terminal
* These APIs should be discoverable, self-documenting, and have decent 
  consistency between different types.
  * ie. something more like a Smalltalk class hierarchy than an existing 
    OS application.
* This is not entirely dissimilar to Windows’ COM, I guess
* This would mean that, eg.
  * Anything you can do in the GUI, you can do from a script, using the 
    same tool and the same commands
  * The GUI could reasonably have actions (menu items, etc) defined as 
    scripts/programs, and the user could easily alter or augment these 
    with their own action scripts/programs
  * Which means I’ll need to figure out how such programs should be 
    written
    * Which comes back to the unity of the ST80 experience
      * Albeit with awful performance and image-management issues
        * Both of which could probably be solved
          * Am I trying to talk myself into writing this in Smalltalk?
            Srsly?
* Smalltalk, Lillith, Oberon, LISP machines …
  * All had a unified language experience.
  * Unix immediately split that into shell and C: why?  Historical 
    accident?
