Ports
=====

TCP
---

* 11000 : IPC service

IPC
---

IPC ports have no fixed numbers: handles are allocated per process
(see :doc:`ipc`).  System services are found by name, using the
p-kernel's name registry:

* `darq.storage` : Storage
* `darq.history` : History
* `darq.security` : Security
* `darq.metadata` : Metadata
* `darq.index` : Index
* `darq.type` : Type
* `darq.terminal` : Terminal
