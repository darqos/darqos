Audio
=====

An audio player is an interesting exploration of some modelling issues.

* A simple player application (play, pause, ffw, rew, etc) for a single
  mp3 or similar format object is relatively easy to conceive.  This fits
  will with the model of Type and an implementation of viewer/editor for
  that type.
* Sound files often have built-in metadata in the form of ID3 tags,
  which describe the track's genre, creation date, length, and other
  properties.
* Music, however, often exists within the cnntext of an album: a sequence
  of individual sound objects with eg. shared cover art.  It's reasonable
  to play an album, in which case the individual tracks should be played
  sequentially.
* Selecting music if often done by searching a collection of albums,
  ordered either by artist, by album title, by genre, or other scheme.
* Playlists are objects that represent a sequence of tracks independent
  of their original album and/or artist.
* "Dynamic playlists" are automatically generated sequences of tracks,
  chosen from a collection on an algorithmic basis.  They might have a
  fixed length, or continue to generate tracks indefinitely as more
  are requested.

So ... given a system, with a bunch of stored sound objects, how should
this music player functionality be implemented?

Albums
------

An album could be derived completely from metadata, with each track
simply duplicating the album's properties, and relying on the metadata
system to record and find the tracks.  Album name, year, publisher,
cover art, etc, could all be properties, plus a index property giving
the sequential number of the track within the album.

Alternatively, an album could be a distinct object type, with its own
implementation and metadata, and links to the comprising tracks.  In
this mddel, it would be somewhat like a Project type, in that it is
basically a collection type within minimal behaviour beyond grouping
its constituents.  Singles, EPs, LPs, etc, could all be variants of the
basic "track collection" type.

I *think* I lean towards making albums a distinct type: that kinda
models the reality most closely, I think.  And having a Type means that
there's an implementation that can cleanly implement any useful
behaviour (ie. "viewer" logic).

Playlists
---------

Similarly to albums, I think it makes sense to have playlists as a
Type within the system.  A basic playlist is just a list of tracks,
but it'd be relatively simple to have algorithmic generators provide
a similar API too.

Its possible that albums and playlists are the same type: they're both
a collection of audio objects, together with some metadata (fairly
minimal for playlists to be fair).

In practice, queuing up a bunch of tracks to be played creates a
transient playlist.

Searching for Music
-------------------

With existing music player applications, the UX of browsing for tracks
to play is fairly well established: you can typically browse by
artist, album, or track, with a particular visual experience tied to
each variant of the process.

In Darq, the standard system search and timeline UX could be used for
music: you could filter by type and/or metadata attributes in a way
that might be a reasonable equivakent?

Alternatively, it might be that a specific UI is needed.  This could
perhaps be selected if a music type filter were chosen, and would look
much like a contemporary music player application.

This might apply also to searching, eg. video or book types?

Some epxerimentation will be required here.  In first edition, no
special behaviour will be implemented.

System Sound Facilities
-----------------------

The system architecture for sound is somewhat complicated.  The
components are:

Sinks
    Typically, speakers or headphones.  But can also be a file, or a
    device connected by a different protocol, eg. AirPlay, Chromecast, or
    Bluetooth.

    An output accepts a stream of input data in a negotiated audio
    format.  It should probably use a simple IPC interface for this,
    perhaps with each packet including format info, rather than having
    stateful negotiation of the format up front?

Mixers
    A mixer has a single output stream, but multiple input streams.
    The input streams are mixed together, with each input stream
    having its own volume control.

    Stuff that also could reasonably be built into the mixer might be
    equalization and synchronisation delays, a global mute, etc.

Queues
    A *queue* converts an ordered collection of sound data objects
    into a stream.

Source
    Anything that can produce a stream of audio data.  This might be a
    streamer for stored objects, a translator for a network stream, or
    a physical input device, eg. a line-in, or a microphone.

In general, a terminal device will have an input-side mixer, and an
output side sink.  The sink might be a local analog jack, USB
soundcard, or nearby AirPlay device (for example): it doesn't matter
what it is.  The mixer is always configured; the output device might
not always be available.

This raises the need for configuration.

A DarqOS system can be configured with any number of:

* Sinks, local or networked.
* Mixers, in addition to the default mixer for a terminal.
* Sources, and
* Queues.

Audio Player
------------

The *Audio* type implementation has a simple lens that supports:

* Play/pause/skip back/skip forward/restart
* Selection of an output mixer

* Control of the mixer channel should probably be a different lens
    for the mixer type implementation?

Music Player
------------

The *Music*, *Album*, and *Playlist* type implementations have a
richly functional lens that supports:

* Basic audio controls
* Metadata display
* Support for album and playlist types

In fact, each output should have an attached mixer.  A mixer can have
any number of inputs, and a queue can have any number of outputs.  The
attachment of a queue to a mixer might have volume, equalisation, and
delay associated with the pairing.


It might make sense to have sources other than queues too: live inputs
for example.  And sinks other than those mentioned above, especially
a file (for recording).

All of these could reasonably be represented as objects of their
respective types, backed by the APIs into the physical devices.  The
UX of the configuration and the use of the controls will need some
work: I think ... I'm not sure you want to search of your volume knob
like you do any other object?  Perhaps?  Does this mean it just lives
on the "shelf" of docked objects in the selector?

Open Issues
-----------

* System hardware/device configuration: how are these objects
  accessed?
* Is there a distinction between sound and music objects?
* How does the audio system deal with multi-channel sound?

  * Stereo, 5.1, 7.1, Atmos, etc

* Can I leverage Pipewire or Jack for the audio system?
* Is there any commonality between the audio system model, as
  described above, and how the system should handle displays and
  input devices?
* How is the default mixer identified by the player application?

  * Some sort of context from the terminal?

    * How is that context located?
    * What else is in it?
