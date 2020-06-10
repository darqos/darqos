# Tools

Aside from Services and Types, there will also need to be some other
categories of programs.

Examples are:
* A spell-checker
* A calculator

## Spell Checker
A spell checker will check either words or larger blocks of text for
spelling errors.  It should be independent of the object from which it
gets the text, so it can be system-wide: like a NeXTstep Service.

I guess they'd be able to specify a filter over their understood types.
And then perhaps there should be a registry of tools, from which a type
implementation can acquire those that are compatible?

## Calculator
It's often useful to just be able to do simple calculations.  Ideally,
you'd be able to copy & paste values into the calculator, and copy & paste
the result back out again.  The results could reasonably just be Text,
unless there's some sort of numeric specialisation?

A calculator is different to a spell checker in that it doesn't have any
association to any _data_.  Other things, like a game, can still have 
somewhat artificial ties to data (eg. a save file), but a calculator
really has none.

So ... where does it fit in?  Maybe it just hangs off the HUD?  I'd kinda
like to minimise the members of this category, but I'm not really sure
how many there will be.

