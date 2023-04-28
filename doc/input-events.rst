Input Events
============

The system will use a focus-follows-mouse model, with an option to raise
windows.  The window with focus will have a nicely composited fade to a
translucent matte which de-emphasises the other windows.

Keyboard and pointer (mouse) events will be delivered to the object with
focus.  Events will be delivered as IPC messages.

Objects will have their standard API exposed (via IPC messages), and
keyboard and pointer actions should be bound to their API.  I think that
means that we need a shim of some sort?

So, eg. a TextWindow would be shim that wraps a Text object, and implements
the GUI, displaying the Text (as ... err ... text) and handling received
input events from the GUI: keypresses, pointer movements, pointer clicks,
etc.

It should provide a bunch of built-in mappings to the wrapped object's
API: some more-or-less 1:1 mappings, others with some built-in behaviour,
eg. a script triggered by a menu.

The question is: why have this in a wrapper, rather then the basic object
implementation?

I think, perhaps in the first step, I'll just make it all one program.
And then, if that doesn't work as well as it should, then maybe split it
so that the object is separate from the GUI.

One thing that might influence this is the use of alternative UIs: the
one that interests me most would be an Alexa/Siri-style voice interface.
