# Music Player
A music player is an interesting exploration of some modelling issues.

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

## Albums
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

I _think_ I lean towards making albums a distinct type: that kinda
models the reality most closely, I think.  And having a Type means that
there's an implementation that can cleanly implement any useful
behaviour (ie. "viewer" logic).

## Playlists
Similarly to albums, I think it makes sense to have playlists as a
Type within the system.  A basic playlist is just a list of tracks,
but it'd be relatively simple to have algorithmic generators provide
a similar API too.

## Searching for Music
Selecting tracks to play has a specific UX that isn't met by the 
standard object selector.  None of the usual indexing, history,
or metadata searching is directly applicable here.

But ... what if it were possible to have a plugin for the selector that
provided type(s)-specific searching UX?  It would become available if
you specified a "music" type in the selector, giving perhaps even a
CoverFlow-like UI with a tabular presentation of tracks and their
metadata?

It's not clear (yet?) how the selector could switch between search,
history, spatial and other plugins in a natural way.  Will need some
thought and probably some experimentation.  But I like the idea of
keeping the selector as the Only One Way To Do It for object selection
including for music.

It makes far more sense to choose "music" as a type and then browse
all your music files, than it does to do the same for Code or Document,
for example.  I *think* it's unique in that way?  Although perhaps 
images or video might be similar?

## System Sound Facilities
So, I've thus far been tacitly assuming that the system will have a
means of delivering sound to an operators ears.  Weather that's an
analog 3.5mm jack, a digital jack, Bluetooth, AirPlay, Chromecast, or 
whatever.

In an ideal situation, in fact there might be multiple such outputs
attached to the system.  They might be used individually, or in sets,
each with their own volume, equalisation, delay, etc.  Each set 
should support a queue of sound objects to be played.  And possibly
even a mixer that allows multiple simultaneous sounds to be mixed onto
the output.

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

## Open Issues
* Selection plugin?
* System hardware/device configuration: how are these objects
  accessed?
* Is there a distinction between sound and music objects?
* How does the audio system deal with multi-channel sound?
  * Stereo, 5.1, 7.1, Atmos, etc
* Can I leverage Jack for the audio system?
* Is there any commonality between the audio system model, as 
  described above, and how the system should handle displays and
  input devices?
