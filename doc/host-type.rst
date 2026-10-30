Host
====

There's some possibility that a host should be an Item, and that the
SSH Client should be part of its Type Implementation.  Does that mean
that a web URL host should also be an Item, and that an HTTP/HTTPS
client should be a part of the Host Type too?

So you could

* ping it
* ssh to it
* http/s to it
* finger it
* get NTP from it

A host could/would have:

* multiple IPs
* multiple DNS names
* location
* ownership

  * possibly implied by netblock?

* OS details
* hardware details
* type (hardware, VM, container)
* NIX / cfengine / whatever config
* Notes

This could make sense as a metadata item, or even a KB item, rather
than using storage backing?

I'm really on the fence with this one.  On the one hand, it'd kinda
dumb.  On the other, like ...

* Sys-space
* host alias, tag, whatever
* Use the host
* Tap "connect"

that's not entirely stupid?

That would mean that the "browser" would use (after maybe creating) a
Host, and then connect to it with HTTP/S, and that'd be run by the
*host*, not the browser.  Where would the caching be?  It'd kinda have
to be associated with the host, since anything else could use the HTTP
access too.  So, yeah, Host has to cache HTTP content (maybe via an
external provider).

Similarly, the SSH session logs would probably belong to the host too.

Host Data Model

Ultimately, the Internet consists of interface, not hosts.

* A DNS name maybe refer to multiple IPs (round-robin DNS, geographic DNS)
* One IP address can have (very) many DNS namees (multi-hosting)
* One machine can have multiple IP addresses
* All of these things can change over time.  Correctness now doesn't mean correctness in future.
* A DNS address may really be an alias for one or more other DNS addresses
* HTTP redirects might be considered a host alias too?

The input could be any of: a local hostname, a DNS name, an IPv4 or IPv6 address.

Let's leave HTTP aside for a moment, and just look at SSH:

* You invoke the factory, and choose "host"
* The type implementation creates a new item, as yet unidentified, and connects a lens
* You enter a local hostname, a DNS name, or an IP into the lens, and the type implementation does a lookup.
* The lookup returns an IP.  Either:

  * It's *unknown*, in which case this is a new host

    * The type implementation allocates an identifier, and records a "created" event
    * The type implementation populates the host by doing lookups

  * It's *known*, in which this is an existing host

    * The type implementation records a "used" event for an existing host item.
    * The type implementation populates the host from its existing databae.

* You see the lens showing information about the host
* You invoke the hotkey to open an SSH session
* A new window is created, showing the SSH session content

  * A session log item is created
  * A history event is recorded for the session
  * Periodically, the session log is indexed, and uploaded to the index server

* When the session is complete, the window is closed, and the session log finalized.
  * A history event is recorded for the session.
