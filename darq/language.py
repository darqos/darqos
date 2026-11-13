# darqos
# Copyright (C) 2026 David Arnold

"""The operator's languages: the chain of them, best first, that labels
and descriptions are shown in, and searched in (see doc/language.rst).

It's made from the host's locale (darq.host.locales): each language
named, followed by its general form, so that en_AU:de_CH is en-AU, en,
de-CH, de.  Then English, as imports label what they make in English,
and Wikidata's mul, its multilingual code, which it keeps many people's
names in rather than in each language."""

from . import host


# Control exports
__all__ = ["ENGLISH", "MULTILINGUAL", "chain", "languages", "tag"]

# The language of what's imported, and Wikidata's code for a label in
# every language.
ENGLISH = "en"
MULTILINGUAL = "mul"


def tag(locale_name: str) -> str:
    """Return a locale's language tag: en_AU.UTF-8 is en-AU.  Empty for C
    and POSIX, which name no language."""
    name = locale_name.split(".")[0].split("@")[0].replace("_", "-")
    return "" if name in ("", "C", "POSIX") else name


def chain(locales: list[str]) -> list[str]:
    """Return the chain of languages for some locales, best first: each,
    then its general form, then English and mul."""
    found: list[str] = []
    for language in [*(tag(name) for name in locales), ENGLISH, MULTILINGUAL]:
        for each in (language, language.split("-")[0]):
            if each and each not in found:
                found.append(each)
    return found


def languages() -> list[str]:
    """Return the operator's languages, best first."""
    return chain(host.locales())
