Person Type
===========

* Any reference to a person in the system should use a (optionally,
  derived) Person class.
* Attributes

  * Name

    * Try to deal usefully here with non-Western forename/surname
      patterns.  This would also take care of stuff like "Pele", or
      "Madonna", or Indonesians (who apparently often have a single
      name).

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

      * Gregorian
      * Japanese dynastic
      * Islamic
      * Jewish
      * etc

  * Notes

    * Just plain text notes, but make sure they're indexed.
    * Or even Markdown might be nice?

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

