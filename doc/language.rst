Languages
=========

Labels, descriptions and aliases are kept by language, as Wikidata
keeps them (see :doc:`kb-service`).  The operator's languages, best
first, are a *chain*: what's shown is in the first language of the
chain that has it, or else any; and a search by label, such as for an
entity to link to, or to merge into, looks in every language of the
chain.

``darq.languages()`` returns the chain (``darq.language``).  It's made
from the host's locale, as ``darq.host.locales()`` reads it:
``LANGUAGE``'s list, such as ``en_AU:de_CH``, or else ``LANG``'s one
locale, such as ``en_AU.UTF-8``.  On an installed system, they come
from ``/etc/default/locale``, through the p-kernel's login session (see
:doc:`terminal`); under ``devboot.sh``, from the desktop's session.

Each locale named is followed by its general form, and then come
English and ``mul``:

=====================  ==================================================
Locale                 Chain
=====================  ==================================================
``en_AU.UTF-8``        en-AU, en, mul
``en_AU:de_CH``        en-AU, en, de-CH, de, mul
``en_AU:gsw:de_CH``    en-AU, en, gsw, de-CH, de, mul
``fr_FR``              fr-FR, fr, en, mul
``C``, or none         en, mul
=====================  ==================================================

* A language's general form follows it, not the other languages, so
  plain English is preferred to Swiss German by someone who prefers
  English.
* A language that isn't a general form of another, such as Swiss German,
  ``gsw``, of Standard German, ``de``, is named as itself.
* English is always there, as imports label what they make in English:
  it's the language of the data, not of the operator.
* ``mul`` is Wikidata's code for a label in every language, which it
  keeps many people's names in, rather than repeating them in each.
  What's taken from Wikidata is labelled in English, or else ``mul``
  (see :doc:`wikidata`).

The entity, book and conversation lenses use it, and so the merge, and
the searches for what to link to.
