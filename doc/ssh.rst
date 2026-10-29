
SSH
===

It is often required to access a remote machine using a protocol such
as SSH, VNC, or RDP.  These sessions can be one-off events, or to
frequently used machines.  In some cases, it is necessary (or at
least, very helpful) to have a local SSH agent forwarded through the
connection to authenticate further downstream connections, or an X11
session forwards for remote graphical interfaces.

By their nature, these connections are unconstrained: you can do
pretty-much anything possible via a Unix shell or Windows desktop.
The local machine has no semantic interactions with the remote one,
just passing through KVM events, essentially.

So, what's the right model for these clients in Darq, and how should
they integrate with the system?


Remote machines are identified by a hostname and port, and
authentication requires either a username and password or a public key
identity.  SSH, specifically, tracks the identity of the machine
behind the IP address via its ``known_hosts`` file.

My first thought is that an SSH client is a *Tool*.  It is something
directly invoked (not via an Item), and it performs its task, and is
then terminated.  It could record a session start/end event in
history, with details of the hostname, port, user identity, etc.

A deeper level of integration would be to process the data stream(s),
specifically, the terminal stream, and create either/or history and
index records for their content.  There might even be two levels of
detail there: recording your requests would often be less detailed
than the remote machine's responses.

That would end up, essentially, with a shell history file.  It might
be possible to go deeper, but I'm not sure it's useful to record an
Emacs or vi editor session as a series of keystrokes without the
context of the subject file.

That doesn't always work: I'm not sure how easy it would be to extract
a useful session log from a network switch session, for instance.

It's also true that *sometimes* the response traffic would be useful
to index.  Raw shell, as opposed to ``less``, ``vi``, or ``emacs``:
``cat`` and ``ls`` would be fine.  It's probably possible to parse the
terminal stream to strip out fancy ANSI codes and cursor movement?

I suppose, in fact, you could keep *session logs*, probably
pre-processed to remove the useless stuff.  They could then be indexed
fairly easily.  And the index could point back to the log, which would
likely be useful context.  Some metadata in the session logs could
identify the machine, identity, etc.  It'd probably be good to parse
out passwords and hide them too.

Ok.  So.

Types & Lenses
- Shell session log
- SSH Public Key
- SSH Private Key
- SSH Known Hosts List

Services
- SSH Agent

The SSH client itself should probably be a part of the Host Type.

