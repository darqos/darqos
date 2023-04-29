Calculation
===========

A colculaton is type that ranges from some calculator-style arithmetic,
through to complex equations, Jupyter/Mathematica-style notebooks.  The
intention is to have these calculations be accessible for both quick
arithmetic like that of the macOS Spotlight search bar, through basic
calculator, to bar-argument hypotheticals, to financial planning or full
scientific worksheets.

Of course, this development will proceed in phases.  The immediate goal
is to provide a calculator equivalent, and see how that fits into the UX,
given it doesn't have the Command-Space ease of Spotlight.

To perform a new calculation, it'd be Sys-n, and then complete "Calculation"
and hit enter.  It's a bit more cumbersome, for sure.  It'd certainly be
possible to put a button on the HUD for it, but that doesn't save a lot.

A new calculation will create a new object, stored in the storage service,
unnamed, untagged, unlikely to have any meaningful index entries, but
still accessible via the timeline, of course. It'd be great to allow
text entry into the calculation itself up front, so there can be notes,
and then useful indexing.

The storage format should be a combination of a high-level container,
with nested blocks/cells (like a notebook).  Each cell can be either
text (Markdown?) or an equation (the default).  At some point, I'd like
to add a Diagram cell too.

Equations should be stored as a serialized syntax tree structure.  In the
base case, this will be more-or-less just arithmetic expressions, so it'll
be simple, but as functionality is added, it should include fractions,
and the many and various equation structures (roots, limits, etc).


Roadmap
-------

* v1

  * Type implementation

    * Receive, and evaluate, a syntax tree
    * Return a syntax tree response
    * Implement basic arithmetic

      * Addition, subtraction
      * Multiplication, division
      * Modulus
      * Unary +/-
      * Brackets
      * Integers, floats
      * Scientific "e" notation
      * Percentage

  * GUI Lens

    * Qt
    * Allow entry of basic expression syntax
    * Render expression on a plain canvas, not a text widget
    * Shift-Enter to send request for evaluation
    * Syntax

      * Basic arithmetic: no special syntax required (yet)

    * Labels for expressions within block
    * Minimal error handling

      * Use tree-sitter?
      * Basically just syntax problems during writing
      * Something extra if you eval an incomplete/wrong expression

  * Issues:

    * How oblivious of the syntax can the UI be?  Can it all be in the type?

* v2

  * Add units

    * Use Python 'pint' module?
    * suffixes on immediate values

      * Completion of possible suffix values

    * "as" or "in" operator to convert between comparable units

  * Add constants

    * A bunch of pre-defined mathematical and physical constants, with units
    * Available via completion
    * Needs some syntax to mark a constant?
    * Is this backend functionality?
    * Can we do currencies here too?

      * Needs real-time (ish) data
      * Would be useful to have history as well
      * Not for v2?

    * User-defined constants to extend built-in set?

  * Add named variables

    * User-defined, names for results within the calculation
    * Syntax to refer to variable values in expressions

* v3

  * Add maths functions

    * Trigonometry, basically: tan, sin, cos, etc.
    * Include in completions (where appropriate)
    * Figure out UI syntax for these (\, like LaTeX?)

  * Add fractions

    * Use built-in fractions module on Python for evaluation?
    * Extend syntax tree to support fractions
    * Extend GUI to display and allow entry of fractions

* v4

  * Add vectors and matrices

    * UI support for manual entry
    * Syntax tree support
    * Include standard vector and matrix operations as functions

  * Add tables

    * Ability to load from file

      * CSV, TSV, fixed columns

    * Functions to access tabular data

      * Searching, filtering, etc
      * Try to avoid a for loop, while that makes sense

  * Add graphs

    * A new block/cell type
    * Support line, bar, pie, and scatter charts
    * Simple stuff

      * Use matplotlib?

        * Even though its API is a bit frustrating?

* v5

  * Add symbolic math

    * Using Python's 'sympy' ?
    * Results can now be symbolic, not just numeric

  * Add template expressions

    * Lookup known formulae by name to initialise your calculation

Useful Prior Art
----------------

* Notebooks

  * Jupyter
  * Mathematica
  * SageMath
  * etc

* macOS calculators

  * A bit https://numi.app/
  * A lot https://soulver.app/

* Equation Editors

  * https://github.com/Qt-Widgets/YAWYSIWYGEE-Qt-Equation-Editor-Widget
  * https://github.com/uwerat/qwt-mml-dev
  * https://github.com/asciimath/asciimathml
  * Mathcha Notebook

* Parsers

  * https://tree-sitter.github.io/tree-sitter/

Syntax Tree Format
------------------

This is basically a grammar spec for the input language, made into a
datastructure.

For v1:

* expression: bracketed_expression | expression operator expression |
  value | unary_sign value
* unary_sign: '+' | '-'
* value: integer | float | sci_float
* bracketed_expression: '(' expression ')'
* operator: '+' | '-' | '*' | '/' | '%'

* Numbers:

  * base_number: digit+ | digit+ '.' | '.' digit+ | digit+ '.' digit+
  * number: [unary_sign] base_number [ ('e' | 'E') [unary_sign] digit+ ]

Issues
------

* how to do percentage?

  * 'x * y%' -- only valid for multiply?
  * 'y% of x' -- wordy, not very mathy, but ...
  * percent(x, y) -- awful

* Unicode operators

  * Extend the parser (lexer) to accept Unicode codepoints for maths
    operators: multiply and divide, at least.
  * What's the mathematical symbol for modulus?

    * Turns out that's pretty complicated
    * 'mod' is pretty common though
