PIM Types
=========

There's an old term for a class of application called Personal
Information Management: PIM.  This is contacts, addresses, calendars,
notes, etc.

In the Darq context, I want to have a base set of entity classes that
address this space, but are universal.

Person
------

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
    * Some of these will be one-off, simple date/time data
    * Some will be more complex repeating data, ideally using or at
      least derived from the rrdate stuff in iCalendar.
    * Can we deal with different calendars?

      * Japanese dynastic
      * Islamic
      * Jewish
      * etc

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

Place
-----

 * A place is really just any identified geography.
 * It could have:

   * Latitude and longitude
   * Street address
   * Containment hierarchy

Organisation
------------

* Primarily for companies, since working at one is a very common
  attribute for a Person, but should cover any organisation.
* Attributes:
* Name
* Legal identifiers: ACN, ABN, TFN, etc
* Formal name (vs. trading as / DBA name)

Meeting
-------

* A calendar entry
* Date & time range
* Place

Implementation
==============

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

Lookup
------

These relationships can be browsed by following links presented in
the various type viewers for these types.  They should also populate
the history service with events, and the index service with textual
names, addresses, notes, etc.


Roadmap
=======

This is a very complex problem to resolve.

Let's assume first that the KB service is implemented, and provides a
data model along the lines of WikiData.  This leads to a scenario
where we have schemas defined for all the PIM-relevant entity types,
an ability to store instances of them, and some sort of search and
retrieval capability.

Assume too that we have a bunch of suitable Lenses defined for these
entity types, and (perhaps most challengingly) an ability to embed
those Lenses into other Lenses or Tools as required.

How then do we provide PIM-like functionality, given this?
* Universal search
* Defined entity types and instances
* Storage and retrieval
* Suitable entity Lenses

What are the use-contexts that constitute a "PIM"?  And what about
other uses?

So, some scenarios:

* I want to send an email to someone, whose name I know

  * Type their (optionally, partial) name into the search bar

    * This would likely give numerous hits: emails, paper authorage,
      source code commits, etc: how is it that the KB record gets
      priority in the search results?
    * What levels of indirection should be used, and (importantly)
      when?

      * If we search by name, and find an email header that matches:

        * Did that email create a KB record when it was delivered, so
          we don't *need* to search for the email itself?

          * If so, does the indexing record the entity identifiers, or
            the text name and address?

      * If, say, we've searched the index and hit upon a KB person
        entity that matches, but the user does not click on it
        straight away ... what do we do?

        * At what point do we decide to do an indirect search via the
          email address associated with the person entity record in
          KB?  And ditto phone number?
        * At what point do we decide to do a web search as well?
        * How are the results presented, and prioritised?

* A new DarqOS user wants to import their existing contacts

  * eg. a vObject file, or a link to Google, Apple, Microsoft, or
    CardDAV
  * Is this an isolated task?  Or is it best done in a broader context
    of importing email and contacts and calendar as a bundle?
  * How do we intgrate with social media accounts?  esp. LinkedIn and
    Facebook, but also stuff like WhatsApp?

    * I think ... we will need to have a daemon which pulls info from
      the websites and updates our view of the world.  Probably one
      per-site would make sense all-round.

So, ok.

* KB service and client library
* Schemas for the relevant entity types
* A basic single-person lens, looking like a business card-style view
  of a person's attributes, with a bunch of links to

  * Other people
  * Places (addresses, etc)
  * Companies / organisations

* A basic "place" card Lens
* A basic "company" card Lens
* A vCard importer
* A CardDAV synchroniser
* Linkage to email
* Linkage to Slack
* Linkage to SMS
* Linkage to Signal
