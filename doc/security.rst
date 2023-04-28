Security Service
================

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
