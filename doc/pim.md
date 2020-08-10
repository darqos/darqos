There's an old term for a class of application called Personal 
Information Management: PIM.  This is contacts, addresses, calendars,
notes, etc.

In the Darq context, I want to have a base set of entity classes that
address this space, but are universal.

## Person
* Any reference to a person in the system should use a (optionally,
  derived) Person class.
* Attributes
  * Name
    * Try to deal usefully here with non-Western forename/surname
      patterns.  This would also take care of stuff like "Pele", or
      "Madonna".
  * Phone numbers
  * Emails
  * IMs
    * It'd be good to have a common abstraction for "endpoint
      identifiers" for a person.  Things that can be used to contact
      them, but without having the system locked into needing to
      understand the underlying protocol syntax.
  * Places
    * The usual home, work, etc, associated physical places.
    * Aside from the basic identity of the name, does there need to
      be any additional semantics for identifying these?
    * The actual mechanics of describing a place belong in the Place
      implementation, not in Person: this is just a reference.
  * Dates
    * Birth, death, marriage?, hiring?, graduation, first meeting, etc
  * Notes
    * Just plain text notes, but make sure they're indexed
  * Relationships
    * Links to other Persons
    * Spouse, child, parent, partner, friend, etc
  * Employment
    * A time-ranged association with an Organisation entity, but
      more specific than an abstract membership, since work is so
      fundamental.
    * Also has a role description.
  * Other organisations
    * Like work, time-ranged and with a role, but ... more general
      than work.  Could cover clubs, sporting teams, professional
      associations, etc.
    * It might be possible to merge this with employment?
    
## Place
 * A place is really just any identified geography.
 * It could have:
   * Latitude and longitude
   * Street address
   * Containment hierarchy
 
## Organisation
* Primarily for companies, since working at one is a very common
attribute for a Person, but should cover any organisation.
* Attributes:
* Name
* Legal identifiers: ACN, ABN, TFN, etc
* Formal name (vs. trading as / DBA name)

## Meeting
* A calendar entry
* Date & time range
* Place

# Implementation
It'll be necessary to have some sort of function for determining
when one of these entities is being referred to in external info,
and substituting a reference to the canonical object.

For example, translating a phone number or email address into a
reference to the owning Person.

Like with macOS, some sort of automagic detection and highlighting 
of these references might be good.

It'd also be good to have some sort of synchronisation or auto-
update function, where changes to an email signature or LinkedIn
entry prompt the user to update their "cached" information, thus
avoiding the issue of stale data as much as possible.  This could
happen as event triggered (like email signature checking) or even
polled (like a regular scan of LinkedIn to look for new info).

When displaying email, for instance, the addresses in the header
should be (hyper)linked to the relevant Person, to allow seamless
navigation from eg. email address, to person, to employment history,
to published books/papers, etc.

Speaking of, book catalog authors should be Persons; publishers
should be organisations; published locations should be Places; etc.

Similarly, for papers, both the authors and their institutions, and
the details of the references should be processed to allow them to
be (hyper)linked to the relevant entities when viewing a paper or
bibliography.  This should be more than just the built-in macOS-style
highlighting, UNLESS that works so well that nothing more is required.

All linkages here should be proper hypertext-style bi-directional
links, so that when you look at a person, you can see all their
publications, etc, as well as seeing all the Persons associated with
a paper.

You should also be able to see or browse to all the emails, all the
meetings, all the phone calls, you've had with a person from their
Person entity.  The idea here is pervasive linking, browsing,
indexing, history, etc, but all stored by reference rather than
relying on runtime parsing and lookup.

All of this information should be kept locally, and synced with
external sources: it should not rely on external databases, either
contacts (like CardDAV) or whatever.

These objects should all be implemented by the Entity Service.  The
Entity service could, and probably should, support two-way sync,
allowing it to eg. update your CardDAV provider (eg. Google).
