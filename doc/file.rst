Filesystem
==========

The *Filesystem* type represents a mounted filesystem from a foreign
operating system.  It might be an NFS or SMB network filesystem, or a
USB mass storage device such as a flash drive.

Either way, it's a critical interoperability mechanism, useful for
exchanging data with other devices.

Workflow
--------

A filesystem can be made available either automatically or manually.

In the case of a USB device, its connection to the system will be
detected by the Linux host's *udev* system.  The Configuration Service
will detect the additional device (using *pyudev*,
https://github.com/pyudev/pyudev) and automatically create a
filesystem instance for it.  This instance will be reported via the
History Service, and typically the user would be notified, as for
other asynchronous events.

In the case of a network filesystem, the user must initiate the mount
manually: perhaps a *new* action on the filesystem type can be
parameterised with an *smb:* or *nfs:* URL?

Once mounted, the question becomes: what to do with the files?  The
basic history/metadata/indexing model is one of locally stored,
persistent objects.  How does this hold up if the storage can be
removed?

Additionally, Darq objects have a richer context than a typical
filesystem member: a USB drive with a *report.doc* file on it is
lacking the type association, metadata, indexing and history that a
Darq *Document* object would have.

So what *should* happen?

In one model, which possibly makes most sense for USB drives, the
file's data is copied to the internal storage, and used to create a
Darq Object with its associated context.  The original file is not
indexed, and not directly recorded in history.

A different model might be appropriate for a NAS volume.  In that
case, it makes little sense to attempt to copy the data to internal
storage (and likely it wouldn't fit anyway).  It would possibly make
sense to create a "shadow volume" of context for the external volume,
absorbing it into the system's indexing, etc, facilities, whilst
leaving the bulk data storage external.  Should those "shadow" objects
need to be accessed, they'd need to request the re-attachment of their
data volume.

As a variation of that, the context could be created and stored on the
external volume itself, and would only become visible to the rest of
the system when it is mounted.

One downside of this is that searching for any of the
externally-stored objects would return no results unless it happened
to be mounted at the time of the search, which seems unfortunate.

A final scenario to consider is an external Darq storage system: what
is the right model for a NAS or a cloud storage service for Darq
instances?

I *think* the right solution would include constant availability of
the external content to all history, indexing, etc, of the "end user"
Darq system. If one were to search for something, it'd be much more
useful to get a response of "It's on external drive with label 'foo
2018'" than it would to come up with no results because the drive
isn't currently mounted?

For Darq systems, it might be possible to perform federated queries,
such that we don't need to keep the shadow volume locally: just
forward to query and result to and from the remote system, and
integrate them with local results?

Returning for now to non-Darq systems, the initial mount would need to
trigger a bunch of work to examine the available files, determine what
Types they should become, create the shadow volume, and then
progressively index content and integrate the results into the global
databases.

I *think* it makes sense to have *New* and *Import* be different
actions for a Type implementation: while the end result is the same
thing, the process is quite different.

When inserting a foreign volume that hasn't been mounted before, we
should probably scan it, and propose a plan for integration.  The user
might tweak that: overriding heuristic results for types of objects
that need semantic knowledge to get the right mapping, for instance.
But then there'd be a period of time where a bunch of background
indexing was done on the raw data before the objects actually became
available.  I guess that's ok: the history timeline is the right place
to look.

We'll need a filesystem lens: a fairly standard multi-pane
desktop/Finder-like solution, I guess, which we should be able then
use to interact with the individual files (at least).

Our UTI types' *mailcap*-like data should include common suffixes, and
enable automatic ingestion.  Other items would need to be queued for
human analysis.
