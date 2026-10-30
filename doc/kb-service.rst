Knowledge Base Service
======================

The *Knowledge Base Service* (KB) holds a graph of facts about things:
people, organisations, places, and anything else that's an item of
knowledge rather than a document.  Its data model follows Wikidata's,
and it uses Wikidata's identifiers where they exist, so that a fact
about Australia is about Wikidata's Australia, Q408.

It's the store behind the PIM types (see :doc:`pim`), and for anything
else that's better described as assertions and relationships than as
a blob (see :doc:`item`).

The service, in ``services/kb``, is the start of an implementation of
this design (see Implementation, below, and the Second Edition in
``TODO.org``): it has the data model, storage, and protocol, but not
yet queries, or the type implementation.

Data Model
----------

The KB holds *entities*: *items*, which are things, and *properties*,
which are the kinds of things that can be said about them.  Each has a
string identifier: a Wikidata identifier, such as ``Q42`` or ``P31``,
where there is one, or otherwise a UUID.

An entity has *labels*, *descriptions* and *aliases*, each kept per
language, by language tag, such as ``en`` or ``en-AU``.  Labels aren't
unique.

Facts are *statements* about an entity.  A statement has

* a main *snak*: a property, and its value;
* *qualifiers*: more snaks, which refine the claim, such as an
  employer's start time;
* *references*: sets of snaks giving the claim's sources; and
* a *rank*: preferred, normal, or deprecated.

Properties have statements too, and each property has a *datatype*,
which its values must have:

========================  ============================================
Datatype                  Value
========================  ============================================
Item, Property            A reference to an entity, by identifier.
String                    A string that isn't in a language, such as a
                          phone number.
Text                      Text, per language.
Quantity                  A decimal amount, which keeps its significant
                          digits, with an optional unit (an item), and
                          optional lower and upper bounds.
Time                      A date and time, to a precision from a
                          billion years to a nanosecond, with bounds
                          counted in units of the precision, and a
                          calendar.  The year is any integer.
URL                       A URL, such as a website or a ``mailto:``
                          address.
External identifier       An identifier in another system, such as a
                          vCard UID.
Item identifier           A DarqOS item identifier (see :doc:`item`),
                          linking the KB to the rest of the system.
                          This one isn't implemented yet.
========================  ============================================

An item's classes are given by *instance of* (P31) statements, and
classes form a hierarchy by *subclass of* (P279), as in Wikidata.  An
entity can be an instance of several classes, and they can change.

The base vocabulary of properties and items is Wikidata's, with their
identifiers, labels and datatypes.  Things Wikidata has no clean match
for, such as an address book entry's label, are *local properties*,
with fixed UUIDs, so they're the same on every system.

Storage
-------

The KB is kept in a SQLite database, ``kb.sqlite``, in the run
directory, using SQLite's write-ahead log, like the History service's
(see :doc:`history`), and for the same reason: Storage doesn't yet
have what an SQLite VFS would need.

Each entity is stored whole, as a document in a row of its own, with
its revision number: that's the canonical form, as it is in Wikidata's
own software, Wikibase.  Reading or writing an entity is a single row,
an edit is atomic, and every earlier revision is kept, in a table of
its own.  The data model can change without a schema change for each
property or datatype.  The schema's version is the database's
``user_version``, so it can be migrated.

Documents are JSON, after Wikibase's: an entity's labels,
descriptions, aliases, statements, and any redirect, or its being
destroyed.  Decimal amounts are strings, so they keep their
significant digits.  JSON, rather than DIDL structures, keeps the
documents readable with SQLite's own tools, and its JSON functions,
while the data model's still changing, and doesn't need the tagged
structs DIDL needs for values; the protocol's encoding needn't be the
store's.

Each statement has an identifier, a UUID, so a change can name the
statement it changes.

Index tables are derived from the documents, and updated in the same
transaction as each change:

* labels and aliases, by language, with an SQLite FTS5 full-text index
  for partial names;
* links, by property and target entity, from statements, qualifiers,
  and references, for following links backwards: everyone employed by
  an organisation, or living at an address, or the instances of a
  class, and of its subclasses; and
* string values, by property and value, for finding an entity by an
  email address, phone number, or external identifier.  Values are
  normalised: case is ignored, and a phone number is just its digits.

A change is written with the revision it was based on, and is refused,
as a conflict, if the stored entity has a later one, so one client's
change never silently overwrites another's.  A change that leaves an
entity's document as it was isn't written.

The database can hold personal data, such as an address book, so it's
created readable only by its owner.

An import must be repeatable: a re-import updates the entities it
created before, rather than adding them again.  Imported entities'
identifiers are UUIDs derived (as version 5 UUIDs) from a key, such as
a vCard's UID, or a locality's name, region, and country, so a
re-import arrives at the same entities without looking them up.  Each
statement an import makes has a reference stating it's from the
import's source (stated in, P248), such as the address book's item,
or Wikidata (Q2013) for the base vocabulary.  A re-import replaces the
statements from its source, and keeps any others, such as those added
by hand; a statement that's unchanged keeps its identifier and place.
An entity with statements from the source, which a re-import no longer
has, loses them, and is destroyed if it has none left.

Other ways of storing the KB were considered:

* a single JSON file: simple, but every change rewrites all of it, and
  every query reads all of it;
* blobs in the Storage service: these can't be queried, and Storage
  can't yet back SQLite; and
* an RDF triple store, queried with SPARQL, as the Wikidata Query
  Service is: powerful, but a large dependency, and qualifiers and
  references need statements about statements.  It might still be
  useful later, as a query layer over the KB, rather than as its
  store.

Protocol
--------

The KB is a service, registered as ``darq.kb``, with a DIDL protocol
in ``lib/didl/kb.didl``.  It's started at boot, and keeps
``kb.sqlite``: while it runs, it's the database's only writer, so what
it's read stays current.  It starts with whatever's in the database,
and loads no base vocabulary: each type implementation finds, or
creates, the entities it needs, which have the same identifiers
whichever creates them, as Wikidata identifiers, or ones derived from
keys.

Entities
~~~~~~~~

* ``GetEntities(ids, follow, languages)`` returns the entities with
  some identifiers, that exist, with their revisions.  With
  ``follow``, a merged entity's redirect is followed.  If
  ``languages``, a fallback chain such as ``["en-AU", "en"]``, isn't
  empty, it also returns the labels of the entities the results refer
  to (their statements' properties, values, and units), each in the
  first language of the chain it has one in, so a client can show a
  person's employer, address, and spouse by name, without a request
  for each.
* ``PutEntities(entities)`` writes entities, whole, all or none.  Each
  has the revision it was read at, or 0 if it's new; a new one without
  an identifier is given a UUID.  An entity that's changed since its
  revision, or that's new but exists, is a conflict, rather than
  overwriting someone else's change.  Every statement is checked
  against its property: that the property exists, and the value has
  its datatype, refers to an entity that exists, and is within its
  bounds.  The entities in a batch can refer to each other.  The reply
  has the entities as written, with their new revisions, or the
  identifier of the one that failed, and why.  An entity's redirect,
  and whether it's destroyed, are kept as they were: only these set
  them:
* ``Merge(old_id, new_id)`` merges one entity into another, and
* ``Destroy(entity_id)`` marks one destroyed.

Writing whole entities, rather than changes to their statements, keeps
the protocol small, and is what the store does; it's how Wikibase's own
``wbeditentity`` works, too.  Finer-grained changes can be added as
other methods, if they're needed.

Lookups
~~~~~~~

Most questions are answered by a lookup, each backed by an index:

* ``FindByLabel(text, language, match, class_id, max, after)`` finds
  entities by label, exactly, or by words: a label or alias with words
  starting with each of the text's words, ignoring case.
* ``FindByValue(property_id, value, max, after)`` finds entities by a
  property's string value: an email address, a phone number, or a
  vCard UID, normalised, so a phone number matches however it's
  written.
* ``FindLinks(target_id, property_id, role, max, after)`` finds the
  entities that link to one, by a property, or any, from a statement's
  main snak, a qualifier, or a reference: its employees, residents, or
  members.
* ``Instances(class_id, max, after)`` finds the instances of a class.

A class matches by conformance: *human* matches instances of its
subclasses too, by subclass of (P279), as types match by conformance
(see :doc:`types`).  Results are identifiers, sorted, in batches, like
History's ``GetEvents``: at most ``max`` (0 for the service's limit,
1000), after the identifier ``after``, so the next batch is asked for
with the last identifier returned.  Destroyed and merged entities are
left out.

Queries
~~~~~~~

Anything else would be a ``Query(patterns, filters, max, cursor)``,
which isn't in the protocol yet: a conjunction of graph patterns, each
a subject, property, and object, any of which may be a numbered
variable rather than a constant, such as

.. code-block:: text

    ?p employer ?o
    ?o headquarters location ?a
    ?a located in Q36074

for the people employed by an organisation in Queensland.  The result
is a table of the variables' bindings.  It's a small subset of SPARQL,
sent as DIDL structures, rather than as text: the DIDL verifier checks
every query, the service needs no parser, nothing can be injected into
it, and every language with a DIDL generator can build queries.  DIDL
has no recursive types, so a query is flat lists of patterns and
filters.  A text syntax, for the shell or the development Terminal,
can be a client library that builds these structures.

Notifications
~~~~~~~~~~~~~

``Watch(ids, listener)`` asks for changes to some entities, or all of
them, if ``ids`` is empty, to be reported to a port, which serves the
``Listener`` protocol: once each change is committed, the KB sends a
one-way ``Changed(entity_id, revision)`` for each entity it changed.
``Unwatch(watch_id)`` ends a watch, and a watch ends when its
listener's port is destroyed.  This keeps an open lens up to date, and
would keep the Index service (see :doc:`search-service`) up to date
with entities' labels, aliases, email addresses, and phone numbers,
under their item identifiers, so the system-wide search finds a person
as a person.

Values
~~~~~~

A statement's value can be of any of the datatypes, and DIDL has no
unions or optional fields (see :manpage:`didl(5)`), so a value is a
struct with an enum giving its type, and fields for each type of
value, where an empty string is no value.  A time's fields are those
its precision covers, and the others are 0.  Every value carries every
field, and nothing stops a client setting the wrong ones.  That will
do for now: the KB would be the first good use for unions, and
optional fields, in DIDL, and the values can move to them when DIDL
has them.

Access
~~~~~~

Any client with the ``darq.kb`` port can read and change everything,
as with the other services, for now.  Ports are capabilities (see
:doc:`ports`), so if a lens should only read the KB, the service can
hand out a read-only port, rather than keeping access control lists.

Client Library
~~~~~~~~~~~~~~

``darq.kb`` has the data model, as Python objects: ``Item`` and
``Property``, their statements, snaks and values, and their documents;
and ``KB``, a client of the service, which converts them to and from
the protocol's structs::

    kb = darq.kb.KB.connect()
    person = darq.kb.Item()
    person.set_label("Ada Lovelace")
    person.claim("P31", darq.kb.EntityValue("Q5"))
    kb.put(person)
    for entity_id in kb.find_by_label("ada", words=True):
        ...

A conflict raises ``ConflictError``, and a lookup returns every
batch's identifiers.

Items, Types, and Lenses
------------------------

Each KB entity is a DarqOS item, so the rest of the system can refer
to it, record History events about it, index it, and use it with a
lens, as it can any other item.

Types
~~~~~

DarqOS types and KB classes are different things:

* An item has one type, which is part of its item identifier, and so
  can't change: History, Index, and Metadata all refer to items by
  their identifiers.  An entity can be an instance of several classes
  (Hong Kong is a city, and a special administrative region of China),
  and its classes can change, as when a stub person turns out to be an
  organisation.
* Types are a curated catalogue (see :doc:`types`), used to find an
  item's implementation and lenses.  Classes are open-ended: Wikidata
  has hundreds of thousands of them, and users add more.

So each KB class isn't a type: an entity's item identifier would
change whenever its classes did.  Nor are all entities one type,
which would lose the choice of lenses by type.  Instead, there are a
few types, for the *kinds* of entities, which each conform to a
generic one.

DarqOS types are derived from Apple's Uniform Type Identifiers (see
:doc:`types`), so a kind that's the same as one of Apple's
system-declared types uses that type's identifier, as the text type
uses ``public.utf8-plain-text``.  Apple has none for these kinds,
though: its nearest is ``public.contact``, the base type for contact
information, about a person or an organisation, which is broader than
either.  So the kinds have ``darq`` identifiers, and those two conform
to ``public.contact``:

======================  ==============================================
Type                    Entities
======================  ==============================================
``darq.entity``         Any entity.  Those of no other kind, such as
                        properties, given names, classes, and units,
                        are of this type.
``darq.person``         People.  Conforms to ``public.contact``.
``darq.organisation``   Organisations, including departments.
                        Conforms to ``public.contact``.
``darq.place``          Countries, regions, localities, and addresses.
``darq.group``          Groups of people.
======================  ==============================================

The others conform to ``darq.entity``, too.  An item identifier is the
type identifier and the entity's identifier, as it is:
``darq.place:Q408`` is Australia, and ``darq.person:`` followed by a
UUID is someone from an address book.

An entity's kind is chosen from its classes when it's created, using a
table kept in the KB, as data: an instance of human (Q5) is a person,
an instance of organization (Q43229), or any of its subclasses, is an
organisation, and so on.  The KB still keeps all of the entity's
classes: its kind is just one that's among them.

An entity's kind can change.  One whose kind changes, which should be
rare, and two entities
merged into one, which isn't (it's how duplicate contacts are tidied
up), are both handled by *redirects*, as in Wikidata: the old item is
marked destroyed, as items are (see Item Destruction, in
:doc:`types`), and redirects to the new one, so that opening its
identifier opens the new item, and links to it still lead somewhere.

Type Implementation
~~~~~~~~~~~~~~~~~~~

One type implementation, registered for all of these types,
is a client of the KB service.  It's separate from the KB service: the
Type service spawns implementations, from a directory, while the KB
is a system service that owns its database.

* ``Create()`` creates an empty entity of the type's kind: a
  ``darq.person`` is an instance of human.
* ``Destroy()`` marks an entity destroyed, keeping its statements and
  the links to it.  Queries leave out destroyed entities, unless asked
  for them.
* An item's port serves the ``Item`` protocol's methods, and, from 16,
  the entity's part of the KB protocol: ``Get(languages, parts)``,
  with the labels of the entities it refers to; ``Apply(changes)``;
  ``Links(property)``, for links to the entity; and ``Watch(port)``.

So an item port is a capability for a single entity: a lens given a
person's port can read and change that person, and see the labels of
the entities it refers to, but no more.  To follow a link, it asks for
the other item to be used, as with any item.  Queries across the
whole graph are made on the KB service's own port, by tools and
search.

Lenses
~~~~~~

A property-sheet lens for ``darq.entity`` shows any entity, with
its labels and statements, and links to the entities it refers to.
``FindLenses`` falls back through conformance, so every kind has this
lens from the start.  Then, each kind can have its own:

* a business-card lens for people, as described in :doc:`pim`, and
  sketched, for the old runtime, in ``types/person``;
* a lens for organisations, showing their people and places, using
  ``Links``; and
* a lens for places, eventually with a map.

A lens can also vary what it shows by an entity's classes, such as a
person who's an author, without the Type service needing to know
about them.

Other Items
~~~~~~~~~~~

KB statements refer to other DarqOS items with Item identifier values:
a person's photo is an image item, and their emails, papers, and
commits can be linked to them.  Other items refer to KB entities by
their item identifiers.

An entity's name is its label, in the KB, rather than in the Metadata
service (see :doc:`metadata`), which holds only what isn't knowledge
about the entity, such as the operator's tags.

Importing a vCard file is something done with a ``public.vcard`` item,
by a tool or lens for it, which creates KB items.

Open Questions
--------------

None, for now.  These were settled:

* Person, organisation, place, and group are the first kinds.
  Properties aren't a kind: they're the vocabulary, rather than things
  the operator works with, and are changed by tools, on the KB
  service's port, so they're ``darq.entity`` items.  Events
  might be next, as Apple's ``public.calendar-event``, the base type
  for scheduled events, rather than a ``darq`` type.  Documents'
  bibliographic records (see :doc:`bib`) can wait.
* An entity's kind can change, with a redirect.
* The type implementation is separate from the KB service.
* Values are a tagged struct, until DIDL has unions.
* People and organisations have ``darq`` types of their own, which
  conform to ``public.contact``, rather than both being
  ``public.contact``.

Implementation
--------------

The data model was prototyped, in Python, in ``experiments/kb``, and
copied into ``services/kb``, to become the service:

* ``darq/kb.py`` has the data model, its JSON documents, its
  conversion to and from the protocol's structs, and the ``KB``
  client.
* ``services/kb/main.py`` is the service.
* ``kb.py`` has a ``KnowledgeBase``, kept in memory, or in a store,
  with transactions, lookups, redirects, and checks that statements
  fit their properties; and ``Import``, for repeatable imports.
* ``store.py`` keeps a ``KnowledgeBase`` in SQLite, as described in
  Storage, above.
* ``wikidata.py`` has a base vocabulary from Wikidata: the properties
  and classes an address book needs, and the countries and regions in
  one, with local properties, with fixed UUIDs, for the rest.
* ``vcard.py`` reads vCard 3.0 files.
* ``addressbook.py`` imports an address book, exported as a vCard
  file, into a ``KnowledgeBase``, in memory or in a database, and
  prints what it read::

      PYTHONPATH=../.. python addressbook.py [--db PATH] FILE [NAME ...]

  It works on the database directly, so it's for when the service
  isn't running: it's not yet a client of it.

  An address book of about 1200 cards becomes about 4100 items, with
  about 16,000 statements: people, organisations, and their
  departments; given and family names; and countries, regions,
  localities, and addresses.  Related names (spouse, child, and so on)
  are linked to the card with that name, if there's just one, and to a
  stub person otherwise.

Importing the same address book into a database a second time writes
nothing.  The tests are in ``tests/test_kb_store.py`` and
``tests/test_kb_service.py``.

The service pages its lookups' results itself, from complete lists of
the matching entities: the store's queries could page instead, when a
KB is large enough for it to matter.
