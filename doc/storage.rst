Storage Service
===============

In place of a Unix-like hierarchical file system, ubiquitous in
contemporary operating systems, DarqOS provides a persistent key-value
storage service, with support for value sizes from a few bytes, to
many terabytes.

The keys are members of a flat namespace: there is no concept of
directories, or indeed meaningful key names.  This is purely a means
of ensuring the persistence of an identified sequence of bytes.
