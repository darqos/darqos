# darqos
# Copyright (C) 2026 David Arnold

"""Merging one entity into another, as the entity lens's M does (see
doc/kb-service.rst): what it might be merged into, and which of two
survives, or why they can't be merged."""

import dataclasses
import typing

import darq.kb as model
import darq.wikidata


# Control exports
__all__ = ["Candidate", "MergeError", "Plan", "candidates", "plan"]


# Most candidates offered: a name in many is too vague to choose from.
MOST = 50

# The chain of languages, if none's given.
ENGLISH = ("en",)


class MergeError(Exception):
    """Two entities can't be merged: it says why."""


@dataclasses.dataclass
class Candidate:
    """An entity something might be merged into."""
    id: str
    label: str
    description: str
    links: int

    def describe(self) -> str:
        """Return a line to choose it by, with its identifier, to look up."""
        about = self.description or f"{self.links} link{'' if self.links == 1 else 's'}"
        source = ", Wikidata" if darq.wikidata.is_wikidata(self.id) else ""
        return f"{self.label} — {about} ({self.id}{source})"


@dataclasses.dataclass
class Plan:
    """A merge: what goes, what it goes into, and whether that's the other
    way round from what was asked, and why."""
    old: model.Entity
    new: model.Entity
    reversed: bool

    def question(self, chain: typing.Sequence[str] = ENGLISH) -> str:
        """Return what's asked, to confirm it, with labels in a chain of
        languages."""
        old, new = label_of(self.old, chain), label_of(self.new, chain)
        text = (f"Merge {old} ({self.old.id}) into {new} ({self.new.id})?\n\n"
                f"Its names and statements move there, and what links to it is changed to link there.  "
                f"This can't be undone.")
        if self.reversed:
            text += f"\n\nIt's this way round so that Wikidata's identifier, {self.new.id}, stays."
        return text


def best(texts: dict[str, str], chain: typing.Sequence[str]) -> str:
    """Return the text in the first language of a chain that has one, or
    else any, or else empty."""
    return next((texts[language] for language in chain if language in texts), next(iter(texts.values()), ""))


def label_of(entity: model.Entity, chain: typing.Sequence[str] = ENGLISH) -> str:
    return best(entity.labels, chain) or entity.id


def alike(this: model.Entity, other: model.Entity) -> bool:
    """Return whether two entities are of a kind to be merged: the same
    DarqOS type, or one has none, as one made of Wikidata's minimally
    hasn't, and gains the other's by the merge."""
    kinds = {model.recorded_type(this), model.recorded_type(other)}
    return len(kinds) == 1 or model.THING_TYPE in kinds


def candidates(kb: model.KB, entity: model.Entity, text: str,
               chain: typing.Sequence[str] = ENGLISH) -> list[Candidate]:
    """Return the entities, of a kind to be merged with one, with labels or aliases
    with words starting with a text's, in any language of a chain, that it
    might be merged into."""
    found: list[model.Entity] = []
    matched = {i for language in chain for i in kb.find_by_label(text, language, words=True)}
    ids = sorted(matched - {entity.id})
    for start in range(0, len(ids), 200):
        got, _ = kb.get_entities(ids[start:start + 200], follow=False)
        found += [e for e in got if e.redirect is None and not e.destroyed and alike(entity, e)]
        if len(found) >= MOST:
            break
    return [Candidate(e.id, label_of(e, chain), best(e.descriptions, chain),
                      sum(count for *_, count in kb.link_counts(e.id)))
            for e in found[:MOST]]


def plan(this: model.Entity, other: model.Entity) -> Plan:
    """Return how one entity's merged into another: into it, unless only
    the first is Wikidata's, when the other's merged into it.

    :raises MergeError: if they can't be merged."""
    if this.id == other.id:
        raise MergeError("It can't be merged into itself")
    if this.redirect is not None or other.redirect is not None:
        raise MergeError("One of them has already been merged")
    if not alike(this, other):
        raise MergeError("They're different kinds of thing, so they can't be merged")
    ours, theirs = darq.wikidata.is_wikidata(this.id), darq.wikidata.is_wikidata(other.id)
    if ours and theirs:
        raise MergeError("Wikidata says they're different: if they're the same, that's to be fixed there first")
    if ours:
        return Plan(other, this, True)
    return Plan(this, other, False)
