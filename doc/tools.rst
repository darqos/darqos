Tools
=====

Aside from Services and Types, there will also need to be some other
categories of programs.

Examples are:
* A spell-checker
* A calculator

Spell Checker
-------------

A spell checker will check either words or larger blocks of text for
spelling errors.  It should be independent of the object from which it
gets the text, so it can be system-wide: like a NeXTstep Service.

I guess they'd be able to specify a filter over their understood types.
And then perhaps there should be a registry of tools, from which a type
implementation can acquire those that are compatible?

Calculator
----------

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

Clock
-----

A wall-time clock is a basic utility.  For a desktop system, it should
probably just occupy a small area of the HUD, along with any other
"systray"-style tools.

For a phone, however, it should be a large-format display on the home
screen.  It should show the local (or chosen) timezone in large font,
and have the ability to show additional timezones in smaller font
sizes.  Each time should include its local date too?

It could conceivably be the entry point for an alarm facility, and/or
a timer and/or stopwatch facilities.

The basic clock would support either 12 or 24 hour times, a set of
additional timezones, and conceivably things like Catholic day
periods, Roman hours, ships bells, etc.

I don't see any real need to theme or "complicate" the display.
Possibly a choice between a digital or analogue display?

It could possibly use side-swiping to switch between the timezones
displayed in large format?

The actual time service should use NTP to synchronise with upstream
clocks, which should probably not rely on an NTP pool, but instead use
a darqos name to enable it to be anycast, round-robined, etc, later.

Think about the styling: is it largely transparent?  How does a
FOS-style design look?
