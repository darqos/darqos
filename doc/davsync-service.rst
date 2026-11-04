CardDAV Sync Service
====================

Address books, formerly isolated within their host applications, have
moved in recent operating systems to become system services, and to
synchronise with cloud services using the IETF standard CardDAV
protocol.  Based on vCard, itself now an IETF standard, CardDAV allows
multi-master synchronisation of "card" data across many participants.

The data model is rooted in the vCard standard from the Versit
Consortium in the 1990s.  It has evolved, not always with good
backward compatibility, from the version 2.1, through v3.0, and now
v4.0.  It's not a completely useless standard, as its many users
attest.

vCard's attributes map to properties on Darq's Person and Organisation
types.  Two-way synchronisation of vCard's attributes is provided by
the CardDAV Sync Service.

In this way, a new person or phone number saved on your phone can
propagate to your Darq desktop.

The CardDAV Sync Service should create new Person and Organisation
items, update the properties of existing items, and record the
deletion of items via the Person and Organisation Type implementation
(these are combined in the "kb" type implementation in first edition).


CalDAV Sync Service
===================

Like address books, calendars have moved from application-specific
data, through the vCalendar and iCalendar standards, now accessed via
the CalDAV standard protocol.  Like CardDAV, CalDAV provides
multi-master, many participant calendar synchronisation.  The major
complexity with iCalendar is its repeating events functionality, which
will need to be modelled in Darq (possibly as a direct feature port,
possibly not).

Like the CardDAV Sync Service, the CalDAV Sync Service should create,
modify, and record the deletion of calendar items via their respective
type implementations.
