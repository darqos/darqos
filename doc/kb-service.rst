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
this design (see Implementation, below, and the Second Edition plan,
https://claude.ai/artifact/3KSAPtyZ54Ngq9j2whyRQp): it has the data model, storage, and protocol, but not
yet queries.

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
                          This one isn't implemented yet: see
                          :doc:`entity-vs-item`.
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
  for partial names.  The full-text index reads its text from the
  labels table (an *external content* table), and triggers on that
  table keep it in step, so each label is stored once, and an entity's
  rows are found through an ordinary index when it changes;
* links, by property and target entity, from statements, qualifiers,
  and references, for following links backwards: everyone employed by
  an organisation, or living at an address, or the instances of a
  class, and of its subclasses;
* string values, by property and value, from statements, qualifiers,
  and references, for finding an entity by an email address, phone
  number, or external identifier.  Values are normalised: case is
  ignored, and a phone number is just its digits; and
* snaks, every one, whatever its value, for finding statements that
  have a qualifier, such as an end time, or not.

Each row has its statement's identifier, the snak's role in it (its
main snak, a qualifier, or in a reference), and the statement's rank,
for queries (see Queries, below).  Version 1 of the schema had no
snaks, ranks or roles.  Version 2 kept the full-text rows apart from
the labels, found by an unindexed column, so every write scanned the
whole full-text table: about 23 ms a write at 100,000 entities, and
growing with them.  An older database's index tables are rebuilt from
the documents when it's opened, which takes about a second for about
4,000 entities.

The store keeps SQLite's query planner supplied with statistics, with
``pragma optimize``, when it opens and after every 10,000 writes:
without them, a query can start from the wrong end of a join, and at a
million entities one that takes 0.2 ms took 6 seconds.  SQLite analyses
only what's changed a lot, reading at most 1,000 rows of each index, so
it's cheap: a second for a million entities with no statistics, and
under a millisecond with nothing to do.

The store's scale is measured by ``bench/kb_scale.py``, which grows a
database of synthetic entities, mostly emails, to millions, and times
writes and lookups at each step.

A change is written with the revision it was based on, and is refused,
as a conflict, if the stored entity has a later one, so one client's
change never silently overwrites another's.  A change that leaves an
entity's document as it was isn't written.

The database can hold personal data, such as an address book, so it's
created readable only by its owner.

A statement's references say where it's from: each is to a source,
by stated in (P248), such as an address book's item, Wikidata (Q2013)
for a vocabulary, or the *DarqOS* item for the type implementation's
own statements.  As in Wikibase, a statement can have several: when a
source states what another has already, the statement gains a
reference, rather than being repeated (see ``darq.kb.restate``).
Restating a source's statements replaces them, keeping the others; a
statement the source no longer makes loses its reference, and goes if
it has none left.  A statement with no references is never changed by
a source.

The operator's own statements, made by hand, cite the tool they were
made with: the entity lens's are stated in *Darq Entity Editor*.  Each
tool that edits entities is an *editor* (``darq.kb.EDITORS``), and a
statement whose references are all to editors, or that has none, is
the operator's: any editor changes it in place.  A source stating the
same as the operator adds its reference to the operator's statement,
as for any other, and when it no longer does, the editor's keeps it.
Editors say which tool made a statement, not who: when there are
several people, the person can be added to the editor's reference.

Statements are the same if they have the same value and qualifiers.  A
source can say which qualifiers its statements always say all of
(an address book's labels and contact types, say): then only those,
and any others its statement has, are compared.  So a statement can be
given qualifiers by hand, such as the start time (P580) and end time
(P582) of a residence, and still be the source's.  When the source no
longer makes one with qualifiers like that, or one that's deprecated,
it's kept, as the operator's, rather than removed: a past address
stays, with its dates.  Ranks aren't compared: a source's preferred
rank is given to a statement it matches, but a rank's never lowered,
since without knowing what the source said before, there's no telling
whether a rank was set by hand.

A statement can be changed by hand, by the entity lens (see Lenses,
below), and still be the source's, by annotating it: giving it start
and end times, or a point in time, or a reason for its rank, which no
source states (``darq.kb.ANNOTATIONS``), or by setting its rank.  Any
other change would only have a re-import state it again, as a new
statement, so a source's statement isn't changed: it's deprecated,
with a reason for deprecated rank (P2241) of *replaced by hand*, and
the changed statement added beside it, as the operator's, stated in
the editor.  Removing a
source's statement deprecates it, in the same way, with *removed by
hand*.  A re-import matches it, whatever its rank, so it isn't stated
again; and once the source no longer states it, it's kept, being
deprecated.

This is one-way.  Keeping the KB in step with its sources both ways
(vCard files, CardDAV, JSContact, JMAP) needs what was last synced, to
merge against, with conflicts kept for a person to resolve: see PIM
synchronisation in the Second Edition plan (https://claude.ai/artifact/3KSAPtyZ54Ngq9j2whyRQp).

An import must be repeatable, and share what's already in the KB: a
re-import updates the entities it created before, rather than adding
them again, and an import from another source uses the entities that
are already there, such as a locality, rather than making another (see
vCard Import, below).

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
* ``Merge(old_id, new_id)`` merges one entity into another, relinking
  what linked to it (see below), and
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
* ``FindLinks(target_id, property_id, role, max, after, reverse,
  type_id)`` finds the entities that link to one, by a property, or
  any, from a statement's main snak, a qualifier, or a reference: its
  employees, residents, or members.  With ``reverse``, they're in
  descending order, from the last, as a conversation's messages are
  read, newest first, and ``after`` is still the last one returned.
  With ``type_id``, only entities with that DarqOS type are found:
  ``darq.entity`` for those with none.
* ``LinkCounts(target_id)`` returns how many entities link to one, by
  each property, role and type.
* ``Instances(class_id, max, after)`` finds the instances of a class.

A class matches by conformance: *human* matches instances of its
subclasses too, by subclass of (P279), as types match by conformance
(see :doc:`types`).  Results are identifiers, sorted, in batches, like
History's ``GetEvents``: at most ``max`` (0 for the service's limit,
1000), after the identifier ``after``, so the next batch is asked for
with the last identifier returned.  Destroyed and merged entities are
left out.

Lookups are answered from the index tables' identifiers, without
reading the entities.  A batch of ``FindLinks``, or of ``Instances``,
is read in order from an index ending in the linking entity's
identifier, and stops when it's full, so it costs its own size,
however many entities link to the target, or are instances of the
class: a batch is read for each property and type of link, or for each
of a class's subclasses, and merged.

People create, send and use a great many things: tens of thousands of
messages, and as many emails, files and web pages.  So what links to
an entity isn't a list, but groups, by property, role, and the linking
entity's type, each with a count, which is what ``LinkCounts`` gives.
No kind is treated specially, so a new type gets its own group.  Each
link records its entity's DarqOS type, as a number, in
``links_by_target``, and a table of counts is kept in the same
transaction as the links.  It counts entities, rather than links, since
a message's every statement can cite the one source, and leaves out
those destroyed or merged.  For 66,137 messages it adds 0.2% to the
database, and about a quarter to the time a write takes; a person's
groups take 0.007 ms, and the newest 200 of their 34,277 messages
0.2 ms.

Kinds with many items should have identifiers that sort by time, as
messages' do (see :doc:`message-type`), so that their groups can be
read newest first, with no other index.  An entity's *type* here is
its recorded DarqOS type: one that has only a class, with no recorded
type, is in the ``darq.entity`` group, so the store needn't read
classes.

Queries
~~~~~~~

Anything else is a query: ``Query(patterns, select, deprecated,
languages, max, after)`` finds what matches all of some patterns.
Each pattern is an entity, its *subject*, with a statement that has a
snak of a property, in a role (main, qualifier, or reference), with a
value, its *object*.  A subject is a numbered variable, or an entity;
an object is a variable, an entity, a string value (matched
normalised, as ``FindByValue`` matches), or any value at all.  The
property is always given.  So

.. code-block:: text

    ?p  employer               ?o
    ?o  headquarters location  ?a
    ?a  located in+            Q36074

finds the people employed by an organisation in Queensland: *located
in* is repeated, from an address to its locality, and on to
Queensland.  As in SPARQL, a pattern's property can be followed once,
one or more times (``+``), or zero or more (``*``): a repeated
pattern needs a constant at one end, so the search is bounded, and
``?p instance of ?c, ?c subclass of* human`` finds every person, as
``Instances`` does.

Patterns can share a *statement variable*, to match the same
statement, for its qualifiers and references; and a pattern can be
*negated*, to match only if it doesn't.  So

.. code-block:: text

    ?p  residence  ?a      [statement ?s]
    not ?p  end time  any  [statement ?s, qualifier]

finds who lives where now: a residence with no end time.  A negated
pattern's variables that no other pattern binds match anything.
Deprecated statements match only with ``deprecated``, so statements
replaced or removed by hand are left out.

The result is rows of the selected variables' values (all of them, if
``select`` is empty): entity identifiers, strings, as written, or
statement identifiers.  They're sorted, without duplicates, and come
in batches, as lookups' do: at most ``max``, after the row ``after``.
With ``languages``, the reply has the labels of the rows' entities.
As in lookups, destroyed and merged entities are left out, and a
variable's entity is never one.

A query is a small subset of SPARQL, sent as DIDL structures, rather
than as text: the DIDL verifier checks every query, the service needs
no parser, nothing can be injected into it, and every language with a
DIDL generator can build queries.  ``darq.kb.Query`` builds them in
Python.  DIDL has no recursive types, so a query is a flat list of
patterns.  A text syntax, for the shell or the REPL,
can be a client library that builds them.

A query that can't be answered is ``INVALID_ARGUMENT``: one with no
pattern to match, more than 16 patterns or variables, a property
that doesn't exist, a variable for a value that isn't an entity or a
string, a variable that's both, a selected variable that no pattern to
match has, or a repeated pattern with no constant end.  One still
running after two seconds is stopped, and is ``TOO_COSTLY``.

Queries are answered on the KB service's own port, by tools and
search, and not on item ports: a lens with a person's port can't query
the whole graph.  Each is compiled into one SQL statement over the
index tables: a join per pattern, a recursive common table expression
per repeated one, and ``not exists`` for a negated one.  Later: label
filters, time and quantity values indexed for comparing, optional
patterns, and Wikidata's *best rank*, preferred statements only, if a
property has any.

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
either.  So the kinds have ``darq`` identifiers, and those that have
contact details also conform to ``public.contact``:

=============================  =======================================
Type                           Entities
=============================  =======================================
``darq.entity``                Any entity: the base type of every KB
                               item.  Those of no other kind, such as
                               properties, given names, classes, and
                               units, are of this type.  Conforms to
                               ``public.item``.
``darq.person``                People.  Also conforms to
                               ``public.contact``.
``darq.organisation``          Organisations, such as companies.  Also
                               conforms to ``public.contact``.
``darq.organisational-unit``   Parts of organisations, such as
                               departments: X.500's
                               organizationalUnit.  Also conforms to
                               ``public.contact``.
``darq.place``                 Countries, regions, localities, and
                               addresses.
``darq.group``                 Groups of people.
``darq.conversation``          Conversations: channels, direct
                               messages, and group chats.
``darq.message``               Instant messages (see
                               :doc:`message-type`).  Also conforms to
                               ``public.message``.
``public.url``                 Places on the web (see :doc:`url-type`),
                               with what's been fetched from them.
=============================  =======================================

The others conform to ``darq.entity``.  ``darq.entity`` doesn't
conform to ``public.data``, since an entity isn't a byte stream, nor
to ``public.content``, which is for documents.  A person's lenses are
found through both ``public.contact`` and ``darq.entity``, which are
the same distance away, so a contact lens and the property-sheet lens
(see Lenses, below) are both offered.

An item identifier is the type identifier and the entity's identifier,
as it is: ``darq.place:Q408`` is Australia, and ``darq.person:``
followed by a UUID is someone from an address book.

Content Items
~~~~~~~~~~~~~

Every item is an entity (see :doc:`entity-vs-item`), including those of
the types that keep their content in Storage: text, images, PDFs and
e-books.  ``darq.items`` makes, reads, names and destroys their
entities, for their type implementations:

* A content item's entity is identified by its blob's Storage key, in
  hex, which is its item specifier, so ``public.jpeg:03d1824d…`` is the
  image, and ``03d1824d…`` its entity, and each is found from the other.
* Its kind is its DarqOS type statement, stamped as the KB's kinds' are
  (``darq.kb.stamp``), with a class from Wikidata: text file (Q86920),
  image file (Q860625), or, for PDFs and e-books, computer file (Q82753).
  These kinds aren't the KB type implementation's: they're served by
  their own, and their entities are written to the KB service by it.
* It has a label, its name, where it has one, and two local
  properties: *created*, when it was made, in DarqOS, to the second, and
  *file name*, the file it was made from, the same property as a
  message's attachment's file name.
* An item made before there were entities is given its entity when it,
  or its entity, is opened, by its type implementation, which overrides
  ``EntityType.adopt``: so ``OpenEntity`` finds every content item that's
  there.  The rest are given theirs by Give Items Entities (see
  :doc:`entity-vs-item`).
* The kinds' names, such as JPEG Image, are in ``darq.items``, and so
  their items are listed by them on an entity's page.
* A new item's entity is made by its type implementation, after its
  blob, named by its content's title (a PDF's, an e-book's, or an
  image's), or else by the file it came from, without its extension, as
  ``CreateFrom`` is given it: an empty note has none.  If the KB won't
  write it, that's logged, and the item's made all the same: an item
  that has no entity is given one later (see :doc:`entity-vs-item`).
* Its names, its entity's labels and aliases, are published to the
  Index with what the type reads from its content, and again when its
  entity's changed through its port.  It's destroyed with the item.

Kinds
~~~~~

An entity's kind is recorded when it's created as an item, by the type
implementation, as a *DarqOS type* statement, a local property, with
its type identifier.  An entity without one, such as one imported from
an address book, is of the kind its classes give: each class can have
a *DarqOS instance type* statement, another local property, giving the
type of its instances, and of its subclasses'.  So the kind table is
data in the KB, rather than code.  An entity's kind is the first of

1. ``darq.person``,
2. ``darq.organisational-unit``,
3. ``darq.place``,
4. ``darq.organisation``,
5. ``darq.group``,
6. ``darq.conversation``,
7. ``darq.message``, and
8. ``public.url``

that any of its classes, or of their superclasses, by subclass of
(P279), has as its instance type, or else ``darq.entity``.  Place comes
before organisation because Wikidata has administrative territorial
entity, the class of regions, as a subclass of organization as well as
of geographic location.  Recording a kind keeps an item's identifier
the same when its entity's classes change, as when a stub person turns
out to be an organisation.

The defaults are DarqOS's, stated on the classes, with references to
the *DarqOS* source item (see Storage), as are the statements of the
items it creates, their kind and class: human (Q5)
is a person, organizational subdivision (Q9261468) an organisational
unit, geographic location (Q2221906) and address (Q319608) places,
organization (Q43229) an organisation, group of humans (Q16334295)
a group, a local *conversation* class a conversation, instant message
(Q30070565) a message, and URL (Q42253) a URL.  The type implementation states them each time it starts,
so a later version can change them, and a statement of the operator's,
with preferred rank, overrides them, and is kept.  Department
(Q2366457) and organizational unit (Q679206) are both subclasses of
organizational subdivision, rather than one of the other.  An address
isn't a geographic location on Wikidata.

A local KB has only a little of Wikidata's class hierarchy, so a
vocabulary adds the subclass links its classes need to reach these,
stated in its own source: the address book's adds Wikidata's, from
human settlement, country, and the states and provinces it uses.

The KB keeps all of the entity's classes: its kind is just one that's
among them.  A kind is changed deliberately, which should be rare, by
making a new entity, and merging the old one into it.  Two entities
merged into one, which isn't rare (it's how duplicate contacts are
tidied up), are handled by *redirects*, as in Wikidata: the old entity
redirects to the new one, so that opening its item opens the new
item, and what's outside the KB, such as History, still finds it.

What both entities said is said once: a statement with the same value,
qualifiers and rank as one the new entity has is combined with it,
keeping the references of each, so that "instance of organisation",
from LibraryThing's import and from Wikidata, is one statement, cited
to both.

Within the KB, a merge *relinks* what linked to the old entity, as
Wikidata's bots do after one: in the same transaction, each statement
naming it, as its value, a qualifier, or a reference, comes to name the
new one, so the KB reads as if it had always been the new one's.  Each
entity relinked is a revision, and its watchers are told.  A busy entity's
merge is slower for it: a person in thousands of messages takes seconds.
One that comes to name the new entity twice says it once.  The KB
service relinks, when it starts, what still links to any merged entity,
which merges before relinking left, and combines what entities merged
into say twice, which merges before combining left.

A statement can still come to name a merged entity afterwards, as an
importer's might, by an identifier it made.  So, as a safety net, what an
item's entity refers to lists one by the identifier it names, with the
new one's label and item, and what links to an entity, and how many do,
include what links to those merged into it, which the store finds by an
index of redirects.  An entity that links to both is found once, but
counted twice.  The importers that find entities by identifiers they
made, LibraryThing's people and Slack's bots, follow a redirect.

Type Implementation
~~~~~~~~~~~~~~~~~~~

One type implementation, ``types/kb``, registered for all of these
types, is a client of the KB service.  It's separate from the KB
service: the Type service spawns implementations, from a directory,
while the KB is a system service that owns its database.  The Type
service runs one process for all the types a directory implements
(see :doc:`types`).  When it starts, it finds, or creates, the
properties and items it needs (see ``darq.kb.KB.ensure``), and states
the default instance types (see ``darq.kb.KB.state``).

* ``Create(type_id)`` creates an entity of the type's kind, an
  instance of its class (a ``darq.person`` is an instance of human),
  with its DarqOS type.
* ``Open(item_id)`` opens an entity's item, if its kind is the item
  identifier's type: ``darq.entity:`` and a person's identifier is
  ``NOT_FOUND``, since conformance is for lenses, not identifiers.
  A merged entity's item opens the entity it redirects to, whatever
  its kind, and a destroyed one's is ``NOT_FOUND``.
* ``Destroy(item_id)`` marks an entity destroyed, keeping its
  statements and the links to it.
* ``OpenEntity(item_id)`` is the same as ``Open``, for these kinds,
  whose item is their entity.

It's the plain implementation of the entity type, which every type
implementation inherits (see :doc:`entity-vs-item`): ``darq.entity_type``'s
``EntityType`` is its base class, and serves the ``EntityItem``
protocol for each item, the entity lens's, below.  It calls a hook,
``entity_changed``, after each change made through an item's port,
which ``types/kb`` overrides to publish the entity to the Index.  The
types that keep their content in Storage inherit it too, and serve their
items' entities with ``OpenEntity``: they make an item's entity with it,
and override the hook to publish the item's names, with what's in it
(see Content Items, above).

Entities of its kinds are written only through it, not to the KB
service directly (see :doc:`types`), so it sees every change to its
items.  Its Implementation port serves the ``EntityWriter`` protocol
too, from 16, for clients that write many at once, such as an import:

* ``WriteEntities(entities)`` writes entities, as ``PutEntities``
  does, all or none, keeping an existing entity's DarqOS type; a new
  one can't have one, since an item of a kind is made by ``Create``;
* ``MergeEntity(old_id, new_id)`` and ``DestroyEntity(entity_id)``
  are the KB's ``Merge`` and ``Destroy``; and
* ``CreateEntities(type_id, entities)`` creates new entities of a
  kind, all or none, with the identifiers they have, each with the
  class and DarqOS type ``Create`` gives.  It's for an import that
  chooses its identifiers, as messages' are chosen, and too many to
  create one at a time.  One that exists is a conflict.

Given an ``EntityWriter`` client, ``darq.kb.KB`` writes through it:
``put``, ``ensure``, ``state``, ``merge`` and ``destroy`` go to the
type implementation, while lookups still go to the KB service.
Properties, classes, and other entities that aren't of a kind with its
own implementation can still be written to the KB service directly.

Each entity of a kind other than ``darq.entity`` that it creates or
writes, with ``Create``, ``PutEntity`` or ``WriteEntities``, is
published to the Index (see :doc:`search-service`): its labels and
aliases, in every language, as names, and its descriptions as its
body, with a message's text, so the Terminal's Search finds people,
organisations and places by name, and messages by what they say.  One that's destroyed, or merged into another, is withdrawn,
as is one whose kind has changed, under the kind it was; the one it's
merged into is published again.  ``IndexEntry`` gives the same entry,
for a reindex, and is ``UNSUPPORTED`` for ``darq.entity``.

An item's port serves the ``EntityItem`` protocol, in
``lib/didl/kb.didl``: the ``Item`` protocol's ``Close``, and, from 16,

* ``GetEntity(languages)``, with the entities it refers to, as
  ``Linked``: their item identifiers, labels and descriptions in the
  chain of languages, and properties' datatypes;
* ``PutEntity(entity)``, which writes it, as ``PutEntities`` does,
  keeping its DarqOS type;
* ``GetLinks(property_id, role, languages, max, after, reverse,
  type_id)``, for the entities that link to it, as ``Linked``, with
  ``reverse`` and ``type_id`` as for ``FindLinks``;
* ``GetLinkSummary(languages)``, for how many link to it, in groups,
  as ``LinkCounts``, each with its property's label and inverse label;
* ``GetLinkedEntities(property_id, role, type_id, languages, max,
  after, reverse)``, for the entities that link to it, whole and
  read-only, with those they refer to as ``Linked``, for a lens that
  shows a conversation's messages, say; and
* ``WatchEntity(listener)``, which has the KB report the entity's
  changes to the listener directly;
* ``FindEntities(text, languages, kind, class_id, max, after)``, for
  items or properties by the words of their labels and aliases, as
  ``FindByLabel`` finds them, in any of the chain of languages, as
  ``Linked``; and
* ``SuggestProperties(languages, max)``, for the properties to offer
  first for a new statement or qualifier: the annotations (start
  time, end time, and the like), then those the entity has.

So an item port is a capability for a single entity: a lens given a
person's port can read and change that person, see the entities it
refers to, read those that link to it, and find others to refer to,
but no more.  To follow a link, it asks for the other
item to be used, by its item identifier, as with any item.  Queries
across the whole graph are made on the KB service's own port, by
tools and search.

The implementation keeps nothing of its own: each request is answered
from the KB.  It caches the classes it reads to find kinds, and
watches the KB to drop those that change.

Lenses
~~~~~~

The entity lens, ``lenses/entity``, a property sheet for
``darq.entity``, shows and edits any entity.  ``FindLenses`` falls
back through conformance, so every kind has this lens from the start.

* It shows the entity's label, kind, and item identifier; its
  description and aliases; its statements, by property, with their
  qualifiers and references, preferred ones starred; and what links to
  it, in groups (see below).  A property's statements are in order by
  series ordinal (P1545), where they have one, as names are
  (``darq.kb.in_order``), and then as they are.  Deprecated statements
  are counted, and shown, struck through, with D.  Labels and text are in the locale's
  languages, falling back to English.
* A link to another entity's item follows it in the same window,
  like a web browser, with Back (Alt+Left, or Backspace) to return.
  Ctrl+click, or a link's context menu, opens it in a new window, with
  whatever lens its type has.
* Each item shown is watched (``WatchEntity``), so the view keeps up
  with changes, and recorded in History as ``opened``, from
  ``entity-lens``.

It's HTML in a ``QTextBrowser``, built by ``render.py``, which uses no
Qt.  A link is an anchor numbered in the page's list of item
identifiers, so nothing is parsed out of URLs.  Back opens the earlier
item again, rather than keeping its port, so it shows it as it is
now.

E, or the Edit button, switches to edit mode, and back: the page is
replaced by a form (``edit.py``), with the label, description and
aliases, in the language the label's in, and the statements, by
property, each with its value, rank, qualifiers and sources, and
buttons to add a qualifier, add start and end times ("dates"), or
remove it.  "+ statement", at the end, adds one.  The kind's
statements, DarqOS type, aren't shown, as they can't be changed.

* Each change is saved as it's made, as the Text lens saves, when a
  field's finished with (Enter, or Tab, or leaving it), a rank's
  chosen, or an item's chosen from a list.  Esc puts back a field
  that's being typed in, or else leaves edit mode.  Ctrl+Z and
  Ctrl+Shift+Z undo and redo the changes saved.
* Items and properties are chosen with a picker, which searches as
  it's typed in (``FindEntities``), listing the best matches first,
  and the suggested properties before anything's typed.  Up, Down and
  Enter choose from the list.
* Times are typed as they're said: 2024, June 2024, 30 June 2024,
  June 30, 2024, 30/6/2024 (day first), 1970s, or an ISO 8601 time,
  with "about" for ± 1 of the unit, and BCE.  The precision is what's
  given, and how it's read is shown as it's typed (``times.py``).
* A change is made as edits (``darq.kb``, below), applied to the
  entity as last read, and written whole with ``PutEntity``.  If the
  entity's been changed since, it's read again and the edits made
  again, as a rebase is, unless what they change has been changed too.
  Then that's shown, marked, with Keep mine, to make them anyway, and
  Take theirs, to drop them.
* A source's statements are kept its own, as described in Storage,
  above: changing one's value replaces it, removing it deprecates it,
  and a note says so.
* Changes from elsewhere are shown as they're made, once a field being
  typed in is finished with.
* The item's recorded in History as ``edited``, once each time edit
  mode's entered.

M, or Merge into ... on the context menu, merges the entity shown into
another, as two spellings of a publisher, from two imports, are one
publisher (``darq.merging``).  W, for one on Wikidata, is described in
:doc:`wikidata`.

* It asks for the other's name, starting with this one's label, to be
  shortened or changed, and lists the entities whose labels or aliases
  have words starting with it: those of the same DarqOS type, or of
  none, as one made minimally from Wikidata's has none.  Each has its
  description, or else how many link to it, and its identifier, to look
  it up, marked if it's Wikidata's.
* It says what it'll do, with both identifiers, and does it only if
  that's confirmed, as a merge can't be undone.  This one is merged into
  the one chosen, unless only this one's Wikidata's: then it's the other
  way round, so that Wikidata's identifier stays, and it says so.
* Entities of different DarqOS types, two of Wikidata's, which it says
  are different, and one already merged, aren't merged, and it says why.
* Both items are recorded in History as ``merged``, and the page shows
  the one that's left, with a note that the other was merged into it.

Edits are operations: setting a label, description, or aliases, in a
language; adding or removing a statement; and setting a statement's
value, rank, or a qualifier.  Each knows what it changes as it was,
and as it's to be: it's made if that's as it was, does nothing if it's
already as it's to be, and otherwise conflicts.  So the same edits can
be made to a later revision, and are undone by their inverses.
``change_value``, ``change_qualifier``, ``change_rank`` and
``remove_statement`` make an operator's changes as edits, keeping a
source's statements its own.  They're in ``darq.kb``, not the lens,
for synchronisation to use too.

What links to an entity is shown in groups (see Lookups, above), under
a heading for each property.  A group of up to 12 is shown in place,
and a larger one by its count, which opens to a paged list of its
entities, 100 at a time, newest first, within the lens.  A group isn't
an item, so it has no lens of its own, can't be opened in another
window, and isn't recorded in History.  Back leaves a group, from
whichever page of it, and a place to go back to holds the group and
page as well as the item, so coming back from an entity chosen in a
group returns to that page.  A group's paged by cursors, so there's no
jumping to a page.  A group is read-only, so Edit is disabled there.

An entity chosen from a group opens in its own type's lens, if it has
one, in a new window: it's been chosen to be used, rather than looked
up.  Otherwise it's shown in the same window.  An entity with no label,
such as a message, is listed by its ``Linked`` summary, a line from the
type implementation: for a message, its sender, date, and how it
starts.

``darq.ui.links`` has this, without Qt, for other lenses to share.  A
lens gives it a way to make anchors, and does what's chosen.  The
counts come as they're read: a new link is a change to the entity it's
from, not the one shown, so they aren't kept up to date as they change,
until the item's read again.

A group is headed by its property's inverse label.  A property reads
from an entity to what it refers to: *creator* reads from a message to
its author, and from the author, it's *created*.  Statements are only
stored one way, so that's only for showing them.  As on Wikidata, a
property has an *inverse label item* (P7087), an item with the label,
so it's in any language, like any other.  One without is headed *With
this as employer*, as is a link from a qualifier or a reference, which
says so.  Inverse properties (P1696) would store every link twice, and
the link index already reads them backwards.

Then, each kind can have its own lens:

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
service (see :doc:`metadata`).  That service is deferred to Third
Edition, and may not be needed: tags, too, could be KB statements
(*has tag*), leaving it only what isn't knowledge, such as view
positions.

Importing a vCard file is something done with a ``public.vcard`` item,
by a tool or lens for it, which creates KB items.

Open Questions
--------------

None, for now.  These were settled:

* Person, organisation, organisational unit, place, and group are the
  first kinds, with ``darq.entity`` as their base type, and the type
  of everything else.  Properties aren't a kind: they're the vocabulary, rather than things
  the operator works with, and are changed by tools, on the KB
  service's port, so they're ``darq.entity`` items.  Events
  might be next, as Apple's ``public.calendar-event``, the base type
  for scheduled events, rather than a ``darq`` type.  Documents'
  bibliographic records (see :doc:`bib`) can wait.
* An entity's kind is recorded when it's created, and found from its
  classes otherwise, by a table kept in the KB.  It's changed by a new
  entity and a redirect.
* The type implementation is separate from the KB service, and one
  process serves all the kinds.
* Opening an item of another kind than its entity's is ``NOT_FOUND``.
* Values are a tagged struct, until DIDL has unions.
* People and organisations have ``darq`` types of their own, which
  conform to ``public.contact``, rather than both being
  ``public.contact``.

vCard Import
------------

``tools/vcard-import`` is a tool, started from the Terminal's Tools
panel (*Sys-t*), with a window: choose a vCard file, and the
address book's name, and import it.  The address book is an item, the
source its statements are stated in, so it can be imported again,
updating what it imported before; other address books are imported
under other names, and share what they have in common.

* ``vcard.py`` reads vCard 3.0 files, such as Apple Contacts' and
  Fastmail's.
* ``vocabulary.py`` has the base vocabulary from Wikidata: the
  properties and classes an address book needs, the countries and
  regions in one, and the subclass links that give their instances
  kinds, with local properties, with fixed UUIDs, for the rest.
* ``cards.py`` reads cards as *drafts*: people, organisations, groups,
  departments, given and family names, stub people for related names,
  localities, and addresses, each with its natural key.  A person's
  given names (P735), then additional names, and family names (P734),
  are numbered in the card's order, with series ordinals (P1545), as
  Wikidata orders names.
* ``importer.py`` imports them, as a client of the KB service, which
  it reads, and writes the vocabulary to, and of the KB type
  implementation, which creates the drafts' entities, and through
  whose ``EntityWriter`` they're written.

The import is:

1. The vocabulary, and the address book's item, which have fixed
   identifiers, are found or created through the KB, with ``ensure``,
   and the vocabulary's statements stated in Wikidata.
2. Each draft's entity is found, or else created:

   * a person, group, or organisation with a card, by its vCard UID;
   * by where the address book's statements led before, on a
     re-import: by property, and label, and qualifier, and where a
     property has several values, by their labels; this finds what's
     been renamed since, too.  An address is found this way only if
     it's the same address, with the same lines, postcode and country:
     one that's changed is another address, so the old one isn't
     rewritten, for whoever else lives there, and can be kept, dated;
   * by its natural key, in the KB as a whole: an organisation, given or
     family name, or full-name stub person by its label and class; a
     department by its label and parent organisation; a locality by its
     label, region, and country; an address by its street lines,
     postcode, locality, region, and country.  A field the card hasn't
     matches only an entity without it.  If several match, the one with
     the lowest identifier is used, and the match is *ambiguous*.
     People with cards are never matched by name.  Each match by
     natural key is logged, and listed in the window, with its key and
     the item it matched, to be checked;
   * otherwise, it's created, as an item of its kind, with the type
     implementation's ``Create``, and recorded in History as
     ``created``, from ``vcard-import``, if it's of a kind other than
     ``darq.entity``.

3. Each entity's statements from the address book are restated from
   its draft, keeping any others, in batches written through the type
   implementation's ``WriteEntities``, with its label and description,
   if it hasn't one.  Its labels, contact types, services,
   departments, job titles and series ordinals are the qualifiers it
   always says all of (``Vocabulary.qualifiers``), so dates added by
   hand are kept, and a name moved on the card is restated.  An
   entity whose address, by property and label, was replaced with
   another is noted, as an *address changed*, for its old address's end
   time and the new one's start time to be added.
4. Entities with statements from the address book, that it no longer
   has, lose them, and are destroyed if they've nothing left but their
   kind and classes.  An entity something else will still link to is
   kept, with the address book's statements made the operator's: an
   old address a dated statement still links to, and so its locality.

A draft that fails is left out, and its cards' UIDs logged, and the
import goes on.  The window shows counts, and what's to be reviewed:
the matches to check and the addresses changed, which are logged too,
and nothing else of the address book.  Review items are kept only
there, for now: a sync framework would keep them, as conflicts are.  An address book of about 1200
cards becomes about 4100 entities, and takes about 18 seconds to
import; importing it again takes about 6, and changes nothing.

Implementation
--------------

The data model was prototyped, in Python, in ``experiments/kb``, and
copied into ``services/kb``, to become the service:

* ``darq/kb.py`` has the data model, its JSON documents, its
  conversion to and from the protocol's structs, and the ``KB``
  client.
* ``services/kb/main.py`` is the service.
* ``types/kb`` is the type implementation, of the kinds.
* ``lenses/entity`` is the entity lens.
* ``services/kb/kb.py`` has a ``KnowledgeBase``, kept in memory, or in
  a store, with transactions, lookups, redirects, and checks that
  statements fit their properties.
* ``services/kb/store.py`` keeps a ``KnowledgeBase`` in SQLite, as
  described in Storage, above.
* ``services/kb/query.py`` checks queries, and answers them, with SQL
  for a store, or by matching patterns one by one in memory; the
  tests check that the two agree.
* ``tools/vcard-import`` imports an address book (see vCard Import,
  below).

The tests are in ``tests/test_kb_store.py``,
``tests/test_kb_service.py``, ``tests/test_kb_type.py``,
``tests/test_kb_edits.py``, ``tests/test_kb_query.py``,
``tests/test_entity_lens.py``, and
``tests/test_vcard_import.py``.

The service pages its lookups' results itself, from complete lists of
the matching entities: the store's queries could page instead, when a
KB is large enough for it to matter.
