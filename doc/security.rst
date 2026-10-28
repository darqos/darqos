Security Service
================

First Edition
-------------

1st Edition has only a password, with no user names: it's set on the
first boot, and checked by the Terminal's lock screen (see
:doc:`terminal`).  The Security service (``services/security``) is
started at boot, and registers ``darq.security``.  Its protocol is in
``lib/didl/security.didl``:

- ``IsPasswordSet``: whether there's a password yet.
- ``SetPassword(old_password, new_password)``: the first time,
  ``old_password`` is empty; afterwards, it must be the password.  A
  new password must be non-empty, and at most 1024 bytes, as UTF-8.
- ``CheckPassword(password)``.

The password is kept as an scrypt hash (N=2\ :sup:`15`, r=8, p=1, with
a random 16-byte salt), in ``security.json`` in the run directory,
readable only by its owner, and replaced atomically.  The parameters
are stored with the hash, so they can be raised later.  A file that
can't be read fails closed: a password is taken to be set, but none
matches.

DIPC doesn't tell a service who its client is, so any process could
check passwords, or set the first one, before the Terminal does.  So
guessing is slowed: checks (and changes, which check the old password)
are made one at a time, and after three wrong ones in a row, each
waits longer before it's made: 1, 2, 4, ... seconds, up to 30, until
one's right.  The first password can only be set at the first boot,
when only system processes are running.  That's enough for a
single-user 1st Edition.

There's no way to change the password from the user interface yet.
A forgotten one is reset by deleting ``security.json``, from the host:
the next boot asks for a new one.

Later
-----

The security system really only needs to authenticate users.  Once
authenticated, a user will have some sort of token that can then be
supplied to services that will grant access.

It's possible that detailed permission bits should be manged by the
security service as well.  I'm not sure about this: perhaps it's best
left to individual applications to manage their own?  Or perhaps just
a standard library that means they're managed in a consistent way, but
without recourse to a central server?

Let's consider the storage service.

* It has blobs that are created by a particular user
* That user might want to share access to an object with other users.

  * That could be done as an individual, or as a member of a group.

* Different levels of access might be shared:

  * eg. read, write, delete, etc.

So the storage service should maintain a list of permitted users and a
bitmap of rights per user.

And the security service should maintain a set of users, and a set of
groups.  The runtime should probably cache group membership, and maybe
have a means of unsolicited invalidation or updating of the cache.

One benefit of centralising the rights maps is that it's easy to
determine what rights a user has, and to revoke or augment them.  If
they're inside the various services, they're much less transparent.

OTOH, keeping permission bits for every stored object in the security
service sounds like a mess.

So, both users and groups are principals.  A principal has a unique
id.  A group has a set of principal members, including possibly nested
groups.

When an access right is granted, a record is created for a principal
and a rights map.

When a user makes a request to a service, it can request all the
groups for that user.  It can then check both the individual user and
any of their groups for appropriate rights before performing any
action.

The groups list(s) should be cached, and if updated, the security
service should sent an unsolicited updated list to the services who
have requested that user's groups.

Similarly, if the user is disabled or deleted, an update should be
sent.

Within the storage system, each object should have a set of principal
identifiers and a rights map.  An access decision should check the
active user and their groups to determine whether their access is
permitted.

The initial authentication process should ultimately use SASL or
something, but in the short term, just user/password will do.
Similarly, the returned token should be signed somehow, but in the
short term, it can be just plaintext.
