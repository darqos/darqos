# Metadata Service

While the storage service persists information as blobs, the metadata
service persists collections of name/type/value data.

There are many uses of this data, and the service fulfills many roles
in the system.

* Association of type information with objects
  * All objects have a MIME-like type, stored in metadata
* User tags
* Screen view position data