Message Type
============

The message type is an abstraction for the many different types of
instant messaging that need to be supported.  For me, currently, this
should include:

* SMS

  * Also MMS
  * Also RCS
  * Using a macOS device as an iMessage bridge host also bridges to
    SMS, MMS, and RCS.  This is the easiest solution.

* iMessage

  * via a macOS-hosted gateway
    
    * There's no iCloud support for iMessage
    * A new Mac Mini is $1500; a Macbook Neo is $1050

      * Second-hand models seem pretty over-priced; a new Neo (or
        Mini) is cheaper than most second-hand much older models, and
        will have a longer support life.

    * Matrix has an iMessage bridge that will do this; otherwise there
      are a bunch of things that automate sending with AppleScript and
      receiving by scanning the database file.

* WhatsApp

  * (Which looks like either web-driving or a business account/app)
  * Matrix

* Discord

  * Supposedly you can use a Developer Bot account on your private
    server and route the messages somehow?
  * Matrix
    
* Signal

  * Via signal-cli?
  * Matrix

* Facebook Messenger

  * fca-project ?
  * Matrix


Messages should:

* Display individually

  * Maybe with their optional message-being-replied-to?
  * With an option to expand to full conversation context
    
* Support a reply action
* Support forwarding
* Support tagging

  * Come with an `#unread` tag, which is removed once read?

* Link to the associated person(s) or group
* Update history

  * Arriving messages will need to notify the operator using an Event
  * Will drive a bunch of requirements for notification configuration

* Update index
* Use KB


Entities
--------

* Person

  * This should be the standard Person entity, used by vCard,
    LinkedIn, email, etc.
    
* Group or Channel

  * Are these the same thing?

    * Channels often have many people, most of whom you don't really
      know
    * Person entities created from a channel would be just a nick,
      maybe a location or timezone.  Is there really any value there?
    * With a group, you usually have more information about the
      participants (like, their phone number).

* Message

  * Messages are usually just text, often with emoji, sometimes custom
    images (ie. not in Unicode).
  * iMessage supports text effects.  I'm not sure how these are
    represented, but older clients seem to be able to receive the
    messages ok (and ignore them).
  * Sometimes you can also message yourself: Signal, eg, treats this
    specially.
    
* Conversation

  * Messages exist in the context of a *conversation*: an ongoing
    sequence of messages between either two parties, or a group.

  * Generally, they're just temporally ordered, although in services
    which allow replying to a specific message, there's usually a
    display of the prior message as well.

  * The Message Lens needs to be able to change to display the
    Conversation context for a Message, searching should allow
    constraining results to items from a conversation including a
    person, etc.

Workflows
---------

* Unsolicited sending

  * I decide that I want to message Person X
  * Different possible paths:

    * via the person

      * S-space, type enough of person's name to find them
      * Hit enter / double click on the person
      * choose "new message" action from one of their identifiers
      * Type the message
      * Hit send

    * via the message type

      * S-n to create a new object
      * Search for "message"
      * Hit enter/double click to create
      * Maybe search for their name, nick, number in the destination  field?
      * Type the message
      * Hit send

    * via your past conversation

      * S-space, type enough of their name to find the conversation
      * Hit enter/double click to open the conversation
      * type the message
      * Hit send

  * I'm not sure how practical the searches are: if you gave a long
    history with this person, they'll have a lot of index results.
    You might need to constrain the type first?

    * This is what the Message type implementation would do: it would
      constrain the search to person or group first

* Receive unexpected message
* Reply to a received message and watch the conversation
* Find an old message
* Forward a message
* Add an image to a message to  be sent


Messages
--------

Message items are stored across the KB and Storage services.

The Wikidata identifiers are in the vocabulary, under Design, below.


Design
------

*Decided for Second Edition, for importing Slack history: see*
:doc:`slack-import-tool`.  *The type is built, in* ``types/kb`` *and*
``darq.message``.  *Sending, replying, forwarding, tags, notifications,
and constraining a search to a person or conversation come later.*

Each message is a Knowledge Base entity, of a new kind.  This is a
trial: it's the first real load of content items as entities, and
gives the entities-and-items decision (see :doc:`entity-vs-item`)
numbers from real data.  If it doesn't work well, it can be changed.

=======================  =============================================
Thing                    Representation
=======================  =============================================
Message                  An entity of type ``darq.message``, which
                         conforms to ``public.message`` and
                         ``darq.entity``.  It has no label, so
                         messages stay out of searches for names.
Conversation             An entity of type ``darq.conversation``: a
                         channel, direct message, or group chat.
Service account          An item for the workspace, or the like, which
                         its statements are stated in, as an address
                         book's are.
Person                   The standard ``darq.person``, with their
                         account on the service as a statement,
                         qualified by ``service``.
Bot                      A ``darq.entity``, not a person, which can
                         still be a message's sender.
Attachment               An item, of its own type, linked to: a file
                         of a type without an implementation is kept as
                         data (see :doc:`data-type`).  A file that
                         isn't an item, as one too big to keep isn't,
                         is recorded by its name, type and size only,
                         as an *attached file*.
=======================  =============================================

A message's statements are its sender, conversation, time, body,
what it replies to, attachments, mentions, reactions, and when it was
last edited.  Each has a reference to its source, as a source's
statements do, though that's the largest of the shapes measured,
below.  It can be changed later, if the size matters more.

Vocabulary
~~~~~~~~~~

``darq.message`` has the vocabulary, which ``types/kb`` makes sure of
when it starts.  Wikidata's identifiers are used where it has a clean
match, which was checked against Wikidata.  Its *conversation*
(Q52943) is about spoken ones, so that class is local.

================  ===============================  ===================
Meaning           Identifier                       Value
================  ===============================  ===================
A message         instant message (Q30070565)      Class
A conversation    local                            Class
Sender            creator (P170)                   Item
Conversation      part of (P361)                   Item
Time              publication date (P577)          Time
Replies to        reply to (P2675)                 Item
Participant       participant (P710)               Item
Service           platform (P400)                  Item
Body              local                            Text
Mentions          local                            Item
Reaction          local, with *reacted by*         String
Last edited       local                            Time
Attachment        local, with *file name*,         Item
                  *media type*, and *file size*
Attached file     local, as *attachment*, for one  String: empty, or,
                  that isn't an item               from before links,
                                                   its item's
                                                   identifier
================  ===============================  ===================

The point-in-time property (P585) isn't used for a message's time,
since the KB keeps that for annotations made by hand (see
:doc:`kb-service`).  A body's language is ``und``, undetermined, as a
service doesn't say.

So, to the question above: a channel is a Conversation, not a Group.
A Group is a set of people; a Conversation is a sequence of messages,
and has participants.

A reply is a message in its conversation, linked to the message it's
in response to, and shown with that message quoted.  Slack's threads
are flattened to this, since it's how WhatsApp and Signal replies work
too.

The body is kept in the entity, rather than in a Storage blob: most
messages are a few dozen characters, and a page of messages would
otherwise take a request for each.  It's a small subset of Markdown,
so that each service's markup is converted once, on the way in.

A message's identifier is a UUID made from its time, to the
microsecond, followed by a hash of its conversation and its identifier
on the service.  It has the form of a version 7 UUID, as RFC 9562
allows.  So importing it again gives the same entity, and since KB
lookups are sorted by identifier, a conversation's messages page in
time order through ``GetLinkedEntities``, newest first with
``reverse``, as a lens opens at the newest.  Milliseconds aren't enough: Slack numbers
messages within one.  These identifiers are chosen by the importer,
which creates messages with ``CreateEntities`` (see :doc:`kb-service`).

A reply also sent to its channel is listed by Slack in both, and is
one message, as its identifier shows.

The ``types/kb`` implementation publishes a message's body to the
Index.  An import records one ``imported`` History event for each
conversation, rather than one for each message.

The Conversation lens, ``lenses/conversation``, is one read-only
timeline for both types.  A conversation opens at its newest message;
a message opens in its conversation, highlighted, with those around
it, or alone if it isn't in one.

* Messages are read 100 at a time, through the conversation's item
  port (``GetLinkedEntities``), as they're scrolled to, and at most 400
  are held, so a conversation of any length can be read.
* A sender's messages are shown together, without their name again,
  while they're within five minutes, under headings for each day.
  Times are in the local time zone.
* A reply shows what it replies to, quoted: choosing it goes to that
  message.
* Senders and attachments open in their own lenses, in new windows.  A
  file that isn't an item is shown by its name, type and size.
* Home and End go to the oldest and newest.  What's shown doesn't change
  as messages arrive: F5 reads the newest.

Since this is the lens for both types, neither opens in the entity
lens any more, so a conversation's statements can't be seen or edited.
A way to choose among an item's lenses would allow that.

A person's messages are shown in the entity lens as a group, like
anything else that links to them (see :doc:`kb-service`).

Measurements
~~~~~~~~~~~~

``bench/message_scale.py`` makes these entities from a Slack export,
with the service's own store, and reads them as the lens would.  For
66,137 real messages, with 5.7 MB of text:

=============  ===========  ==========  ==========  ============
Statements     Database     A message   Writes/s    Page of 50
=============  ===========  ==========  ==========  ============
Each with a    813 MB       12.3 KB     2,900       1.7 ms
reference
One source     536 MB       8.1 KB      4,000       1.3 ms
statement
Also without   436 MB       6.6 KB      4,500       1.2 ms
instance of
=============  ===========  ==========  ==========  ============

The first shape is the one used, for now.  Reading is no concern at
this size: the largest conversation, of
34,097 messages, is walked in about 40 ms, and a sender's latest 200
take 0.2 ms.  Size is the cost, at 76 to 142 times the text.  A
message's document is only 1.2 to 1.8 KB: most of the rest is indexes,
and each document stored twice, in ``entities`` and ``revisions``.
Keeping a first revision only once would save a further 18 to 23%.
The Index adds 13 MB.
