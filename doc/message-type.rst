Message Type
============

The message type is an abstraction for the many different types of
instant messaging that need to be supported.  This should include:

- SMS
  - MMS
  - RCS?
- iMessage
  - via a macOS-hosted gateway?
  - via a web-driver for iCloud?
- WhatsApp
  - (Which looks like either web-driving or a business account/app)
- Slack
  - https://slack.dev/python-slack-sdk/index.html
- Discord
- Signal

Messages should:
- Display individually, with an option to expand to full context
- Support a reply action
  - And possibly a separate reply-all?
- Support forwarding
- Support tagging
  - Come with an `#unread` tag, which is removed once read?
- Link to the associated people
- Update history
  - Arriving messages will need to optionally notify the user.
  - Will drive a bunch of requirements for notification configuration
- Update index
- Use metadata
  - It's possible that a message should *be* metadata?  Although a small
    block store element (512B?  1k?) is not unreasonable, I suppose.

Conversation
------------

Messages exist in the context of a conversation: an ongoing sequence
of messages between parties.  There can be one or more parties to a
conversation (eg. Slack's DM channel for the user is a one-party
conversation).

Generally, they're just temporally ordered, although in some cases an
optional explicit reply-to ordering is also possible.

The Message Lens needs to be able to change to display the
Conversation context for a Message, searching should allow
constraining results to items from a conversation including a person,
etc.

This will have some commonality with email, and possibly other things?
