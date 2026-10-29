
Host
====

There's some possibility that a host should be an Item, and that the
SSH Client should be part of its Type Implementation.  Does that mean
that a web URL host should also be an Item, and that an HTTP/HTTPS
client should be a part of the Host Type too?

So you could
- ping it
- ssh to it
- http/s to it
- finger it
- get NTP from it


A host could/would have:
- multiple IPs
- multiple DNS names
- location
- ownership
  - possibly implied by netblock?
- OS details
- hardware details
- type (hardware, VM, container)
- NIX / cfengine / whatever config
- Notes

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
