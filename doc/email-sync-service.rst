Email SMTP/IMAP4 Service
========================

An Email Service should:

* Support SMTP protocol

  * Support sending emails
  * Support configuration of multiple accounts
  * Standard encryption and authentication support
  * Used by the email lens/type to send, reply, or forward an email item

* Support IMAP4 protocol
  
  * Monitor one-or-more IMAP accounts
  * Detect newly-added emails
  
    * Fetch them
    * Ingest them as Darq items

      * Create and populate a KB entity
      * Create a matching Email item

    * Add a History element
    * Update the Index
    * Add a Notification Event
    
  * Detect newly-deleted emails
  
    * Flag as deleted in KB
    * Add a History element
    * Figure out what to do with the Index

The IMAP service must ensure that mobile clients which use the IMAP
protocol continue to work effectively.  While a DarqOS system can
participate in processing an operator's email, it must not prevent
their other clients from continuing to do so as well.

Python's standard library includes well-tested SMTP and IMAP
libraries, which should make the implementation fairly
straightforward.

