Inbox
=====

The system will present a unified inbox: a single mechanism and
location for all asynchronous events to be collected and made
available to the user.

This will include all events like delivered emails, instant messages,
or timer alarms, as well as any other notifications of interesting
conditions, etc, from the system.

The inbox should be fundamentally a time-ordered sequence of objects,
with the ability to re-sort and filter it on various bases.  The
presentation should allow a summary view, and perhaps a
QuickLook-style preview of each object.

The user must have the ability to quickly process the inbox.  That
should include:

- a flag indicating that the object has been already "seen"
- a quick removal from the inbox, without destroying the object
- a combined removal from the inbox and destruction of the object

  - This should probably involve a "trash can" function to allow a
    limited time window in which to undo the operation
  - But aside from that, destroyed items would not "enter the system":
    no indexing, no history entry, etc.

- open, but retain
- open, and remove
- add metadata, and remove

  - ie. tagging

There's some inherent contradiction here that will likely give rise
to some implementation complexity: it'd be good for stuff in the inbox
to have indexing and history exposure, so they show up when searching
or browsing, but ... they need to be removed from index/history/etc if
they're discarded from the inbox.

There's also the issue of the relationship between the inbox and
history.  Things in the inbox are arguably just history events that
are not initiated by the user.  Does that mean it's just a slightly
different filter over the history timeline?

Likewise, the "unseen" flag could reasonably just be the absence of an
access timestamp (ie. a history event): this is something the user has
never accessed.

Which really circles back to the UX of history: should "inbox"-style
items share the history timeline with stuff that the user has
explicitly done?  Perhaps with an easy-to-use "show not-me items" view
switch in the timeline view?
