# HTML

Viewing a HTML file, once downloaded, requires fetching all the required
resources: images, CSS, and JavaScript files, prior to their use in
rendering the page.

Ideally, viewed pages should be saved: there's no guarantee that they'll
be there if we go looking in future, or that they'll have the same content.
So ... we'd like to save what we see.

For a static page or a page that is generated server-side and then sent,
this is fine -- the HTML, CSS, and JS will be sufficient to re-render
the page.

But, for pages that use either WebSockets or XMLHTTPRequest or whatever,
the content is _expected_ to change.  The page isn't really a page, it's
really just a shell for live data.  There's no meaningful way to saved it,
other than as a snapshot at a moment in time.  JSON content can be used
to regenerate DOM branches, HTML can be downloaded an patched in, the
whole thing is dynamic.

As an example, consider the GMail inbox page.  It makes basically no sense
to try to even save it, other than as a screenshot.

How can automatically determine what to try to do here?  What are the
options?
- Do we more-or-less give up on any pages that do WebSockets or
  XMLHTTPRequest?
- Leave it up to the user?

None of this is good.

There's a whole philosophy here.  The web is a huge part of everything,
and it doesn't really fit into the model.  The basic idea of a type server
that has a backing store with _local_ data doesn't simply extend to one
where the data is out of our control.

Or does it?  Do we just accept that and move on?  Take snapshots sometimes,
on some sort of trigger and leave it at that?  Fall back to the last
content we have if it goes away?  It's perhaps a small improvement on
what we do now, but it's hardly great.

So.

There will need to be a HTTP / HTTPS fetcher.  It will need to cope with
cookies, and probably a bunch of authentication stuff?

I guess most of the work will be driven by CEF.  I don't know how easy
it will be to intercept its attempts to download stuff.  And I'm not
sure about clicking on links: but somewhere, there must be a hook point.
The UI should be minimal.  There's no need for:
- Tabs
- Bookmarks
- History (in the browser)
- Standard help, about, etc.
- URL/search bar
- Plugins
- Downloads menu
- Forward / back (should be in history)

To keep:
- Reload (???)
- View zoom +/-

## Further Issues

### Structure

Also TBD is the split between the HTML (with implied CSS & JS) type
implementation, and the GUI lens.  What is the interface?  The DOM?

And how does the web accessibility stuff play into how eg. an audio lens
might work?  Not that that is a high priority, but perhaps it has some
bearing on how the split might work?

### Embedded Objects

How would embedded micro-formats work from a types point of view?  There's
a HTML object, controlled by the HTML type implementation.  But, within
that object, there might be an embedded Person object, described using
some sort of micro-formats tagging.

Or, even simpler, there's an embedded image object.  It is rendered by
the HTML lens, but it could also be extracted and become a standalone
image object, rendered by the image GUI lens.

How does that work?  Right-click on the image, and choose "open"?  Which
would extract the data into a distinct backing store, and build up an
object with all the associated metadata, history, indexing, etc?

Does that happen for all embedded resources?  Or just ones that the user
might choose to extract?  Ideally ... I think it would happen for all?
Their data downloaded via the fetcher, the type implementation started,
an object created with the backing data, but ... no lens?  The type
instance is just used by the HTML service and/or lens?  Or, in the case
of a microformat person record, not really used in the browser unless
we choose to reveal in some UI feature that there's a hidden object 
behind the text?

Linked objects ("A" records, rather than say "IMG" records) would lend
themselves to being distinct objects more cleanly, perhaps.  I'm thinking
of a linked MP3 or PDF, where there's not a built-in rendering in the 
HTML lens.


