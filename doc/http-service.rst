HTTP Service
============

The *HTTP Service*, registered as ``darq.http``, does DarqOS's fetching
from the web.  It's one place for what talking to the web needs:
redirects, caching headers, timeouts, and later, cookies and
credentials.

It does two things:

* ``Request`` is a plain HTTP request, of any method, that makes no
  items.  It's for importers, sync services, and a page's resources,
  most of which shouldn't become items.
* ``Snapshot`` keeps what's at a URL: it fetches it, makes it an item of
  its real type, and records that against the URL's item (see
  :doc:`url-type`).

A URL item has no actions of its own, so neither ``GET`` nor any other
method is something a URL does.  They're things asked of this service,
with the URL as an argument.

Protocol
--------

The protocol is ``lib/didl/http.didl``.  A ``max_size`` of 0 is the
default.  A client that may cancel, or wants to hear of progress, gives
an ``id`` of its own, and a ``listener``.

.. code-block:: none

   protocol Http {
       1: Request(string method, string url, sequence<Header> headers,
                  opaque body, uint64 max_size, uint64 id, port listener)
              -> (uint32 http_status, string final_url, string media_type,
                  sequence<Header> headers, opaque body, Status status);

       2: Snapshot(string url, uint64 max_size, uint64 id, port listener)
              -> (uint32 http_status, string url_item, string item,
                  bool changed, Status status);

       3: Cancel(uint64 id);
   }

   protocol Progress {
       1: Fetched(uint64 id, uint64 done, uint64 total);
   }

``status`` says whether the fetch itself worked: ``OK``,
``INVALID_URL``, ``UNSUPPORTED_SCHEME``, ``TOO_LARGE``, ``NETWORK`` (for
names, connections and TLS), ``TIMEOUT``, ``CANCELLED``, ``NO_TYPE``, or
``FAILED``.  ``http_status`` is the server's answer: a 404 is a fetch
that worked, so it's ``OK``, with ``http_status`` 404.

A fetch can take a long time, so a handler doesn't wait for it: each runs
on a thread of its own, and is answered through ``Deferred``, so the
service goes on answering others, and ``Cancel``.  At most 8 run at
once, and others wait.  A fetch carries on if its client stops
listening.

Snapshots
---------

A snapshot keeps what's at a URL as an ordinary item, of its real type,
and records it on the URL's item (see :doc:`url-type`).  ``Snapshot(url)``
returns the item, the URL's item, and whether anything new was kept, so
its caller can just open what comes back.

How It Works
~~~~~~~~~~~~

1. The URL's normalised (``serialize_url``), and what's known of it is
   looked up: its latest snapshot, or that of where it's moved to.
2. It's fetched with ``GET``, sending that snapshot's ETag and
   Last-Modified, if it has them.  Redirects are followed a hop at a
   time, and each is noted.
3. If the server didn't return it, nothing's kept.  The URL's item, if it
   has one, is recorded as ``fetched``, and its latest snapshot is
   returned.
4. If it's not modified, or its SHA-256 matches a snapshot of the URL or
   of where it moved, it's what's already kept, so that item is returned,
   with ``changed`` false.
5. Otherwise it's new.  Its type comes from the Content-Type, or else
   the extension of the URL it ended at, and the first type to take the
   data makes the item, with ``CreateFrom``, which publishes it to the
   Index, as any implementation does.  If none does, it's ``NO_TYPE``,
   and nothing's kept.
6. The snapshot's recorded on the URL it was found at, unless that's its
   latest already, and any moves are recorded.

The item's recorded in History as ``imported``, and the URL's item as
``created`` when it's made, and as ``fetched`` each time it's fetched
from the server, whether or not anything's changed, so it shows when it
was last checked.  All are from ``http``.

.. list-table::
   :header-rows: 1

   * - Fetch
     - On the URL item
     - On the snapshot
   * - First
     - ``created``, ``fetched``
     - ``imported``
   * - Unchanged
     - ``fetched``
     - Nothing
   * - Changed
     - ``fetched``
     - ``imported``, on the new snapshot
   * - Refused, such as a 404, or no type, with an item already
     - ``fetched``
     - Nothing
   * - Refused, with no item yet
     - Nothing: there's no item to record it against
     - Nothing
   * - Moved, with the same content
     - ``fetched`` on the old, and ``created``, ``fetched`` on the new
     - Nothing: the new URL shares the item
   * - Moved, with new content
     - ``fetched`` on the old, and ``created``, ``fetched`` on the new
     - ``imported``, on the new snapshot, on the new URL
   * - Moved already, and unchanged
     - ``fetched`` on both
     - Nothing

Every changed fetch is a new snapshot, for now: nothing is thrown away,
which is what makes it an archive, and what a page looked like can be
gone back to.  What that costs, for a page that changes often, wants
watching: a limit can come later.

Redirects
~~~~~~~~~

A permanent redirect (301 or 308) is a move: the resource has a new
address, which didn't exist when it was first fetched, so both are kept.
The new URL gets an item of its own, and the old one a *moved to*
statement, with when it was seen, and the status.  The old URL's page
shows *moved to*, and the new one's *moved from*, and ``url_item`` is
the new one.

If the content's what was there before, the new URL's snapshot is the
same item, so both URLs lead to one item, and nothing new is made.
Otherwise it's a new snapshot, on the new URL.  Either way, earlier
snapshots stay on the old URL, which is where they were.

* A moved URL is still fetched each time, since sites change their
  minds, with what's known of where it went, so an unchanged resource
  costs a redirect and a 304.
* A URL that answers for itself again, having moved, has come back: its
  move gets an end time, and it carries on from there.  A URL that moves
  somewhere else ends its earlier move the same way.
* A redirect on a first fetch is the same, except that the old URL's item
  has only *moved to*, and no snapshots: ``http`` to ``https`` is the
  usual one.
* A temporary redirect (302, 303 or 307) isn't a move: there's no new
  item, and the URL asked for keeps the snapshot, with where it came
  from as a *final URL* qualifier.
* In a chain, each permanent redirect counts, up to the first temporary
  one, and each URL along it gets an item.
* A redirect that ends in a failure, or in something no type will take,
  records no move, and makes no items.

The hash is compared only along a URL's moves, not with everything:
finding the same bytes anywhere needs a lookup by a qualifier's value,
which the KB hasn't.

Failures
~~~~~~~~

A failure leaves things as they were: earlier snapshots are kept, and
no half-made items are left.  The Terminal opens the last copy kept, if
there is one, and says so.

Until there's an HTML type (Third Edition: see ``types/html``), web
pages are ``NO_TYPE``: only PDFs, images and text can be kept.

Size
----

``max_size`` is a parameter, with a default of 64 MiB.  A fetch larger
than that stops, as ``TOO_LARGE``: from the Content-Length if there's
one, and otherwise as it passes the limit, so an unbounded response
can't exhaust memory.

A much larger limit can be asked for, but what can be done with the
result is limited by more than this service.  Values are sent whole, in
a message (see :doc:`storage`), so ``Request`` returns its body in its
reply, and ``CreateFrom`` takes it the same way.  A Linux ISO needs a
fetch spooled to a file, and then one of the plan's *ranged access, or a
Storage key for data too large for a message*, so that it never passes
through a message at all.  It also needs a type to be an item of.  This
service spools every body to a file, in the ``http`` directory of the
run directory, so that only its last step changes when that comes.

Implementation
--------------

It's built on libcurl, with ``pycurl``, which is licensed under the
LGPL or an MIT-style licence.  The ``pycurl`` wheel for x86-64 Linux
bundles its own libcurl, built with HTTP/2 and with HTTP/3, through
``ngtcp2``, which isn't experimental, and with FTP, FTPS and SFTP, which
the plan wants later.  HTTP/3 is asked for wherever libcurl has it, and
curl falls back to HTTP/2 or HTTP/1.1 by itself.  A stock libcurl
mightn't have it, so it depends on the build: ``pycurl.version_info()``
says.

``httpx`` was considered: it's pure Python, and simpler, but has only
HTTP/1.1 and HTTP/2.  A web lens using CEF would do its own networking
for what it shows, so this choice doesn't commit it.

* The ``User-Agent`` is ``DarqOS/`` and the version
  (``darq.get_version()``), and doesn't identify the operator.
* TLS certificates are verified, and there's no way to turn that off.
* Redirects are followed here, a hop at a time, up to 10 of them, as
  what's said at each one matters: ``final_url`` is where it ended.
  A request's ``Authorization`` and ``Cookie`` headers aren't sent on to
  another host.
* Schemes other than ``http`` and ``https`` are ``UNSUPPORTED_SCHEME``:
  ``file`` especially, which isn't the network.
* What comes back is untrusted: it's held as data, and it's for a type's
  ``CreateFrom`` to refuse.
* It's started at boot with the other services, and uses the Type
  service, the KB, and History as a client.  It keeps nothing of its
  own: what's been fetched is on URL items.  A connection that makes no
  progress for a minute is a ``TIMEOUT``.
* ``services/http/fetcher.py`` is the fetch, with libcurl, and knows
  nothing of Darq, so it's tested alone.  ``snapshots.py`` keeps a
  snapshot, and ``main.py`` is the service.  ``darq.url`` has the
  vocabulary, and ``darq.launch.import_data`` makes an item of data, as
  the Slack import does too.

The tests are in ``tests/test_http_service.py``, with a web server of
their own.

The first version
-----------------

``GET``, without cookies or credentials, with redirects, ETags, and
``max_size``.  ``Request`` has its other methods in its protocol from the
start, but they wait for something to need them.  Cookies and
credentials wait for the Credentials service (Third Edition): until
then, nothing behind a login can be fetched.

Open Questions
--------------

* Fetching a page's resources, its images and style sheets, so that a
  snapshot of a page looks as it did.  It needs the HTML type.
* A PDF that's a document, and one that's a book: fetching can't tell
  them apart (see :doc:`book`).
* Rate limits, for a service that fetches on a schedule.
* Whether old snapshots are ever thrown away, and when.
* Whether FTP and SFTP belong to a service called HTTP.
