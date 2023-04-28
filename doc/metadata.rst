Metadata Service
================

While the storage service persists information as blobs, the metadata
service persists collections of name/type/value data.

There are many uses of this data, and the service fulfills many roles
in the system.

* User tags
* Screen view position data
* Internal cursor positioning / scroll positioning data
* Creation / modification dates

  * Should these be "owned" by the system, and not directly editable
    by an end user?

* Object name (like, if you want a "filename")
* Linkage between the object identifier and its storage, or its
  knowledge base, for instance

Implementation
--------------

It'd be good to have strongly typed metadata: strings, integers,
datetimes, etc, so that makes the use of a SQL database a little
fiddly.  Something like Mongo would work, but that's a big dependency
to introduce.

It could store Python pickles as a string in Sqlite3, I guess.   But
that eliminates any chance of using the database indexes to make
searching useful.

I need to be able to search metadata easily via the selector panel.
Both *ad hoc* searches of the values, and more structured searches of,
eg. data ranges.

I also want stuff like "2017" to work as a date search, not needing to
spell out 2017/01/01 00:00:00 to 2017/12/31 23:59:59.

In *ad hoc* mode, it'd be good if "2017" matched both textually _and_
as a smarter search of datetime metadata.

Sigh.  Complicated.  What can I do *simply*, first?

Can I store everything as text, but do something smart with dates and
numbers?

So, a table of object_id + tag + value, and maybe just text match?

API
---

* set(object_id, tag, value)

  * Set tag for object

* set_many(object_id, {tag: value})

  * Set multiple tags for object, overwriting any existing values

* get(object_id, tag) -> value

  * Return value for tag on object

* get_all(object_ib) -> {tag: value}

  * Return all metadata for object

* delete(object_id, tag)

  * Delete tag for object

* delete_all(object_id)

  * Delete all tags for object

* search(tag) -> \[object_id\]

  * Returns objects for which tag exists

* search(tag, \[values\]) -> \[object_id\]

  * Return objects for which tag exists, and has a value matching any
    of the supplied values


Notes
-----

* It's really not clear to me yet how this should work, or precisely
  what it should attempt to do.

  * eg. for a book catalog entry object, should the properties be
    stored marshalled into a storage blob?  Or should they just use
    metadata?
  * How should the namespace be managed?  Can end-users arbitrarily
    add new keys?
  * Does every "file" in the system have a metadata entry, which
    records its dates, user tags, storage id, etc?

* Schema:

  * id
  * key
  * typed-value

    * sucks for most SQL

      * although, ironically, Sqlite3 will likely be just fine
