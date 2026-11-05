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

Relevent Wikidata ids:
* Q58199 Instant messaging
* Q2462003 Instant messaging client
* Q628523 = Message, which might be good for the entity super-class?
* Q30070565 = Instant message, which might be good the the entity class?

  * This doesn't appear very well integrated/populated?
  * It appear to be the correct type though

    
Properties:
* Service (P400 = OS platform, which isn't really right.  Q17130715 = Slack)
* Service Channel (Q733553 is comms channel, but a bit technical)
* Text (Storage blob) (P6553)
* Timestamp  (P577)
* Language (P407)

Entity Relationships
* Sender (Person) (P170)
* Receiver (Person | Group) (P710)
* Attachment (Item)
* InResponseTo (Message) (P8018506)


Design
------

*Decided for Second Edition, for importing Slack history: see*
:doc:`slack-import-tool`.  *Not yet built.  Sending, replying,
forwarding, tags, notifications, and constraining a search to a person
or conversation come later.*

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
Attachment               An item, of its own type, referred to by its
                         item identifier, as a string, until the Item
                         identifier datatype exists.  A file of a type
                         without an implementation is recorded by its
                         name, type and size only.
=======================  =============================================

A message's statements are its sender, conversation, time, body,
what it's in response to, attachments, mentions, reactions, and when
it was last edited.

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

A message's identifier is a UUID made from its time, followed by a
hash of its conversation and its identifier on the service.  So
importing it again gives the same entity, and since KB lookups are
sorted by identifier, a conversation's messages page in time order
through ``GetLinks``, which needs a way to page backwards: a lens
opens at the newest.

The ``types/kb`` implementation publishes a message's body to the
Index.  An import records one ``imported`` History event for each
conversation, rather than one for each message.

The Conversation lens, ``lenses/conversation``, is one read-only
timeline for both types: opening a message shows its conversation, at
that message.  Senders open their Person, and attachments open in
their own lenses.  The entity lens leaves messages out of the links it
lists, since a person may have tens of thousands.

To be checked before they're used: the Wikidata identifiers above.
``P8018506`` doesn't look like a real property, and anything without a
good match becomes a local property.
