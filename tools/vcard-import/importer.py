# darqos
# Copyright (C) 2026 David Arnold

"""Importing an address book's drafts (see cards.py) into the KB.

It's a client of the Knowledge Base service, which it reads, and writes
the vocabulary to; and of the KB type implementation, which creates its
items, and through which they're written (see doc/types.rst):

1. The vocabulary's entities are found, or created, with their ids, and
   their statements stated in Wikidata, as is the address book's own
   item, which the import's statements are stated in.
2. Each draft's entity is found: by its cards' UIDs; or by where an
   earlier import of the address book's statements led; or by its
   natural key, which is logged, to be checked; or else it's created, as
   an item of its kind, with Create, and recorded in History.
3. Each entity's statements from the address book are replaced with the
   draft's, keeping any others, and written in batches.  Qualifiers and
   ranks added by hand are kept, as are statements the address book no
   longer makes that have them (see darq.kb.restate).
4. Entities the address book's statements were about, that it no longer
   has, lose them, and are destroyed if they have none left; unless
   something still links to them, when their statements become the
   operator's.

An address is found by where the earlier import led only if it's the
same address: one that's changed is another address, so the old one can
be kept, with the dates it was lived at.

A draft that fails is skipped, and its cards' UIDs logged; the import
goes on.  Nothing is logged of a card but its UID, except for what's to
be reviewed: matches by natural key, logged with their keys, and
addresses that changed."""

import logging
import re
import typing

import darq
import darq.history
import darq.kb as model
from darq.protocols.kb import EntityItem
from darq.protocols.type import Implementation
from darq.protocols.type import Status as TypeStatus

import cards
import vocabulary


log = logging.getLogger("vcard-import")

# History source for the created events.
SOURCE = "vcard-import"

# Entities read and written in each request.
BATCH = 200

# The order drafts are matched by natural key: a department's parent,
# and an address's locality, before them.
ORDER = ["name", "organisation", "locality", "stub", "department", "address"]


# Kinds of review.
MATCH = "match"
ADDRESS_CHANGED = "address changed"


class Review(typing.NamedTuple):
    """Something an import did, for a person to check."""

    # MATCH: a draft matched by natural key; or ADDRESS_CHANGED: an
    # entity's address replaced with another, which can be dated.
    kind: str

    # One of the cards concerned, by UID.
    card: str

    # The item concerned.
    item_id: str

    # For a match, the draft's category; for an address, its property's
    # label, with the address book's label.
    what: str

    # For a match, its key; for an address, the old and new addresses' items.
    detail: str

    # For a match, the other items it might have been.
    others: list[str] = []


class Result:
    """What an import did."""

    def __init__(self):
        self.found = 0
        self.created = 0
        self.updated = 0
        self.unchanged = 0
        self.removed = 0
        self.destroyed = 0
        self.kept = 0
        self.reviews: list[Review] = []
        self.failed: set[str] = set()

    @property
    def matches(self) -> list[Review]:
        return [r for r in self.reviews if r.kind == MATCH]

    @property
    def address_changes(self) -> list[Review]:
        return [r for r in self.reviews if r.kind == ADDRESS_CHANGED]

    def lines(self) -> list[str]:
        matches = self.matches
        ambiguous = sum(1 for m in matches if m.others)
        return [f"{self.created} created, {self.found} found ({len(matches)} by natural key, "
                f"{ambiguous} of them ambiguous)",
                f"{self.updated} updated, {self.unchanged} unchanged, "
                f"{len(self.address_changes)} with addresses changed",
                f"{self.removed} no longer in the address book: {self.destroyed} destroyed, "
                f"{self.kept} kept, still linked",
                f"{len(self.failed)} cards failed"]


class History(typing.Protocol):
    def add_event(self, item_id: str, event: str, source: str) -> typing.Any: ...


def squash(text: str) -> str:
    """Return text folded, with its spaces squashed, for comparing."""
    return re.sub(r"\s+", " ", model.fold(text)).strip()


def targets(entity: model.Entity, property_id: str) -> set[str]:
    """Return the entities a property's (not deprecated) values refer to."""
    values = [s.get_value() for s in entity.get_statements(property_id) if s.rank != model.Rank.Deprecated]
    return {v.get_id() for v in values if isinstance(v, model.EntityValue)}


def strings(entity: model.Entity, property_id: str) -> set[str]:
    """Return a property's string and text values, squashed."""
    values: set[str] = set()
    for s in entity.get_statements(property_id):
        value = s.get_value()
        if isinstance(value, model.StringValue):
            values.add(squash(value.value))
        elif isinstance(value, model.TextValue):
            values.update(squash(t) for t in value.text.values())
    return values


class Import:
    """Imports an address book's drafts into the KB."""

    def __init__(self,
                 kb: model.KB,
                 items: model.KB,
                 implementation: Implementation.Client,
                 history: History | None,
                 wd: vocabulary.Vocabulary,
                 name: str,
                 progress: typing.Callable[[str, int, int], None] = lambda step, done, total: None):
        """Constructor.

        :param items: The KB, written through the KB type
                      implementation, for the drafts' entities.
        :param implementation: The KB type implementation.
        :param name: The address book's name.
        :param progress: Told of each step, and how far it's got."""
        self.kb = kb
        self.items = items
        self.implementation = implementation
        self.history = history
        self.wd = wd
        self.name = name
        self.progress = progress
        self.source = model.key_id(f"address book:{name}")
        self.result = Result()

        # Entities read, by identifier.
        self.cache: dict[str, model.Entity | None] = {}

        # The drafts, and the real identifiers of their entities.
        self.drafts: dict[str, cards.Draft] = {}
        self.real: dict[str, str] = {}

    # Reading

    def get_many(self, ids: typing.Iterable[str]) -> list[model.Entity]:
        """Return the entities with some identifiers, that exist."""
        ids = list(dict.fromkeys(ids))
        missing = [i for i in ids if i not in self.cache]
        for n in range(0, len(missing), BATCH):
            batch = missing[n:n + BATCH]
            found, _ = self.kb.get_entities(batch, follow=False)
            have = {e.id: e for e in found}
            for i in batch:
                self.cache[i] = have.get(i)
        return [e for e in (self.cache[i] for i in ids) if e is not None]

    def get(self, entity_id: str) -> model.Entity | None:
        found = self.get_many([entity_id])
        return found[0] if found else None

    def kind(self, entity: model.Entity) -> str:
        return model.kind_of(entity, self.get)

    def item_id(self, entity: model.Entity) -> str:
        return model.item_id(entity.id, self.kind(entity))

    # The import

    def run(self, drafts: dict[str, cards.Draft]) -> Result:
        self.drafts = drafts
        self.vocabulary()
        self.resolve(drafts)
        self.fill(drafts)
        self.clean_up()
        return self.result

    def vocabulary(self):
        """Find, or create, the vocabulary and the address book's item,
        and state the vocabulary's statements from Wikidata."""
        entities = list(self.wd.entities.values())
        book = model.Item(self.source)
        book.set_label(self.name)
        book.set_description("address book, imported from vCards")
        bare: list[model.Entity] = [book]
        for e in entities:
            copy: model.Entity = model.Property(e.datatype, e.id) if isinstance(e, model.Property) \
                else model.Item(e.id)
            copy.labels = dict(e.labels)
            copy.descriptions = dict(e.descriptions)
            bare.append(copy)
        self.kb.ensure(*bare)
        for n, e in enumerate(entities):
            self.progress("Stating the vocabulary", n, len(entities))
            self.kb.state(e.id, vocabulary.WIKIDATA, e.statements)
        self.cache.clear()

    def resolve(self, drafts: dict[str, cards.Draft]):
        """Find each draft's entity, or create it."""
        # By the cards' UIDs.
        for n, (temp_id, draft) in enumerate(drafts.items()):
            self.progress("Finding cards", n, len(drafts))
            uids = [s.value for s in (st.get_value() for st in draft.entity.get_statements(self.wd.vcard_uid))
                    if isinstance(s, model.StringValue)]
            for uid in uids:
                found = list(self.kb.find_by_value(self.wd.vcard_uid, uid))
                if found:
                    self.real[temp_id] = min(found)
                    break

        # By where the earlier import's statements led.
        queue = list(self.real)
        while queue:
            temp_id = queue.pop()
            for target, real in self.previous(drafts[temp_id], self.real[temp_id], drafts):
                if target not in self.real:
                    self.real[target] = real
                    queue.append(target)
        self.result.found = len(self.real)

        # By natural key.
        pending = [d for d in drafts.values() if d.entity.id not in self.real and d.category in ORDER]
        pending.sort(key=lambda d: ORDER.index(d.category))
        for n, draft in enumerate(pending):
            self.progress("Matching", n, len(pending))
            try:
                found = self.natural(draft)
            except (darq.DarqError, TimeoutError, LookupError, ValueError) as e:
                self.fail(draft, f"matching: {e}")
                continue
            if found:
                chosen = min(found, key=lambda e: e.id)
                self.real[draft.entity.id] = chosen.id
                self.result.found += 1
                match = Review(MATCH, min(draft.cards) if draft.cards else "", self.item_id(chosen),
                               draft.category, draft.key(),
                               sorted(self.item_id(e) for e in found if e is not chosen))
                self.result.reviews.append(match)
                more = f" (+{len(draft.cards) - 1} more cards)" if len(draft.cards) > 1 else ""
                log.info(f"Matched {match.what} {match.detail!r} for card {match.card}{more} to "
                         f"{match.item_id}" + (f"; also {', '.join(match.others)}" if match.others else ""))

        # Otherwise, created.
        new = [d for d in drafts.values() if d.entity.id not in self.real]
        for n, draft in enumerate(new):
            self.progress("Creating items", n, len(new))
            self.create(draft)

    def previous(self, draft: cards.Draft, real: str,
                 drafts: dict[str, cards.Draft]) -> list[tuple[str, str]]:
        """Return the drafts a draft refers to, with the entities the
        earlier import's statements about its entity lead to, by property
        (and label), or qualifier of it: if each has one value, before and
        now, or else by the values' labels, where those are unique.  An
        address leads only to the same address."""
        entity = self.get(real)
        if entity is None:
            return []

        def groups(statements: typing.Iterable[model.Statement]) -> dict[tuple[str, str, str], list[str]]:
            result: dict[tuple[str, str, str], list[str]] = {}
            for s in statements:
                label = next((q.value.value for q in s.qualifiers
                              if q.property_id == self.wd.label and isinstance(q.value, model.StringValue)), "")
                for snak, role in [(s.main_snak, ""), *((q, q.property_id) for q in s.qualifiers)]:
                    if isinstance(snak.value, model.EntityValue):
                        key = (s.get_property_id(), label, role)
                        result.setdefault(key, []).append(snak.value.get_id())
            return result

        before = groups(s for s in entity.statements if model.is_sourced(s, self.source))
        now = groups(draft.entity.statements)
        pairs = []
        for key, ids in now.items():
            ids = [i for i in ids if i in drafts]
            old = [e for e in self.get_many(before.get(key, [])) if not e.destroyed and e.redirect is None]
            if any(drafts[i].category == "address" for i in ids):
                for i in ids:
                    same = [e for e in old if self.same_address(drafts[i], e)]
                    if len(same) == 1:
                        pairs.append((i, same[0].id))
                continue
            if len(ids) == 1 and len(old) == 1:
                pairs.append((ids[0], old[0].id))
                continue
            by_label: dict[str, list[str]] = {}
            for e in old:
                for label in {squash(t) for t in e.labels.values()}:
                    by_label.setdefault(label, []).append(e.id)
            labels = [squash(drafts[i].label) for i in ids]
            for i, label in zip(ids, labels):
                matches = by_label.get(label, [])
                if label and len(matches) == 1 and labels.count(label) == 1:
                    pairs.append((i, matches[0]))
        return pairs

    def natural(self, draft: cards.Draft) -> list[model.Entity]:
        """Return the entities matching a draft's natural key."""
        wd = self.wd
        label = squash(draft.label)
        if not label:
            return []

        def labelled(class_id: str) -> list[model.Entity]:
            ids = list(self.kb.find_by_label(draft.label, words=True, class_id=class_id))
            return [e for e in self.get_many(ids)
                    if label in {squash(t) for t in [*e.labels.values(), *(a for v in e.aliases.values() for a in v)]}]

        def same(entity: model.Entity, property_id: str, value: str | None) -> bool:
            return targets(entity, property_id) == ({value} if value else set())

        if draft.category == "name":
            assert draft.cls is not None
            return labelled(draft.cls)
        if draft.category == "organisation":
            return labelled(wd.organization)
        if draft.category == "stub":
            return labelled(wd.human)
        if draft.category == "locality":
            return [e for e in labelled(wd.human_settlement)
                    if same(e, wd.located_in, draft.region) and same(e, wd.country, draft.country)]
        if draft.category == "department":
            parent = self.real.get(draft.parent or "")
            if parent is None:
                return []
            ids = list(self.kb.find_links(parent, wd.parent_organization))
            return [e for e in self.get_many(ids) if label in {squash(t) for t in e.labels.values()}]
        if draft.category == "address":
            within = self.real.get(draft.parent) if draft.parent else draft.region
            if draft.parent and within is None:
                return []
            return [e for e in labelled(wd.address)
                    if self.same_address(draft, e) and same(e, wd.located_in, within)]
        return []

    def same_address(self, draft: cards.Draft, entity: model.Entity) -> bool:
        """Return whether an entity has an address draft's lines, postcode
        and country (its locality may not be found yet)."""
        wd = self.wd
        lines = {squash(draft.lines)} if draft.lines else set()
        postcode = {squash(draft.postcode)} if draft.postcode else set()
        country = {draft.country} if draft.country else set()
        return (strings(entity, wd.street_address) == lines and strings(entity, wd.postal_code) == postcode
                and targets(entity, wd.country) == country)

    def create(self, draft: cards.Draft):
        """Create a draft's entity, as an item of its kind, and record it."""
        kind = self.kind(draft.entity)
        try:
            response = self.implementation.create(type_id=kind)
            if response.status != TypeStatus.OK or response.item is None:
                raise darq.DarqError(response.status.name)
            try:
                EntityItem.Client(response.item, timeout=self.implementation.timeout).close()
            finally:
                darq.close_port(response.item)
        except (darq.DarqError, TimeoutError) as e:
            self.fail(draft, f"creating a {kind}: {e}")
            return

        _, entity_id = darq.split_item_id(response.item_id)
        self.real[draft.entity.id] = entity_id
        self.result.created += 1
        if kind != model.ENTITY_TYPE and self.history is not None:
            try:
                self.history.add_event(item_id=response.item_id, event=darq.history.CREATED, source=SOURCE)
            except darq.DarqError as e:
                log.warning(f"Can't record created for {response.item_id}: {e!r}")

    def fail(self, draft: cards.Draft, why: str):
        """Note that a draft failed, and log its cards' UIDs."""
        new = sorted(draft.cards - self.result.failed)
        self.result.failed |= draft.cards
        for uid in new:
            log.warning(f"Card {uid} failed: {draft.category}: {why}")

    def rewrite(self, statements: list[model.Statement]) -> list[model.Statement]:
        """Return statements with drafts' identifiers replaced by their
        entities', leaving out those referring to drafts that failed."""
        def snak(s: model.Snak) -> model.Snak | None:
            value = s.value
            if isinstance(value, model.EntityValue) and value.entity_id in self.drafts:
                real = self.real.get(value.entity_id)
                return None if real is None else model.Snak(s.property_id, model.EntityValue(real))
            return s

        result = []
        for s in statements:
            main = snak(s.main_snak)
            qualifiers = [snak(q) for q in s.qualifiers]
            if main is None or None in qualifiers:
                continue
            result.append(model.Statement(main, [q for q in qualifiers if q is not None], s.references, s.rank))
        return result

    def fill(self, drafts: dict[str, cards.Draft]):
        """Replace each entity's statements from the address book with its
        draft's, and write those that changed."""
        resolved = [d for d in drafts.values() if d.entity.id in self.real]
        self.cache.clear()
        for n in range(0, len(resolved), BATCH):
            self.progress("Writing", n, len(resolved))
            batch = resolved[n:n + BATCH]
            current = {e.id: e for e in self.get_many(self.real[d.entity.id] for d in batch)}
            changed = []
            for draft in batch:
                entity = current.get(self.real[draft.entity.id])
                if entity is None:
                    self.fail(draft, "its entity has gone")
                    continue
                touched = False
                for language, label in draft.entity.labels.items():
                    if language not in entity.labels:
                        entity.set_label(label, language)
                        touched = True
                for language, description in draft.entity.descriptions.items():
                    if language not in entity.descriptions:
                        entity.set_description(description, language)
                        touched = True
                restated = model.restate(entity, self.source, self.rewrite(draft.entity.statements),
                                         self.wd.qualifiers)
                self.review_addresses(draft, entity, restated)
                if restated or touched:
                    changed.append((draft, entity))
                else:
                    self.result.unchanged += 1
            self.write(changed)

    def review_addresses(self, draft: cards.Draft, entity: model.Entity, restated: model.Restated):
        """Note an entity's addresses replaced with others, by property and
        label, for their dates to be added."""
        wd = self.wd

        def label(s: model.Statement) -> str:
            return next((q.value.value for q in s.qualifiers
                         if q.property_id == wd.label and isinstance(q.value, model.StringValue)), "")

        def items(statements: list[model.Statement]) -> str:
            values = [s.get_value() for s in statements]
            found = self.get_many(v.get_id() for v in values if isinstance(v, model.EntityValue))
            return ", ".join(self.item_id(e) for e in found)

        for property_id in wd.addresses:
            dropped = [s for s in restated.dropped if s.get_property_id() == property_id]
            added = [s for s in restated.added if s.get_property_id() == property_id]
            for name in sorted({label(s) for s in dropped} & {label(s) for s in added}):
                old = [s for s in dropped if label(s) == name]
                new = [s for s in added if label(s) == name]
                what = wd.entities[property_id].labels.get("en", property_id)
                review = Review(ADDRESS_CHANGED, min(draft.cards) if draft.cards else "", self.item_id(entity),
                                f"{what} ({name})" if name else what, f"{items(old)} -> {items(new)}")
                self.result.reviews.append(review)
                log.info(f"Address changed for card {review.card}: {review.item_id} {review.what}: "
                         f"{review.detail}")

    def write(self, changed: list[tuple[cards.Draft, model.Entity]]):
        """Write entities, all at once, or else one at a time, to find
        those that fail."""
        if not changed:
            return
        try:
            self.items.put(*(e for _, e in changed))
            self.result.updated += len(changed)
            return
        except (darq.DarqError, TimeoutError, LookupError, ValueError, model.ConflictError):
            pass
        for draft, entity in changed:
            try:
                self.items.put(entity)
                self.result.updated += 1
            except (darq.DarqError, TimeoutError, LookupError, ValueError, model.ConflictError) as e:
                self.fail(draft, f"writing: {e}")

    def clean_up(self):
        """Remove the address book's statements from entities it no longer
        has, and destroy those left with none, but their kind and classes,
        which Create gave them.  An entity something else still links to
        is kept, with the address book's statements made the operator's."""
        self.progress("Cleaning up", 0, 1)
        current = set(self.real.values()) | {self.source}
        ids = [i for i in self.kb.find_links(self.source, model.STATED_IN, "reference") if i not in current]
        self.cache.clear()
        gone = {e.id: e for e in self.get_many(ids)}
        linked = self.linked(gone)
        for entity in gone.values():
            for s in list(entity.statements):
                if model.is_sourced(s, self.source):
                    model.unsource(s, self.source)
                    if not s.references and entity.id not in linked:
                        entity.remove_statement(s)
            self.result.removed += 1
            if entity.id in linked:
                self.result.kept += 1
                self.items.put(entity)
                continue
            if any(s.get_property_id() not in (model.DARQOS_TYPE, model.INSTANCE_OF) for s in entity.statements):
                self.items.put(entity)
                continue
            item_id = self.item_id(entity)
            response = self.implementation.destroy(item_id=item_id)
            if response.status == TypeStatus.OK:
                self.result.destroyed += 1
            else:
                log.warning(f"Can't destroy {item_id}: {response.status.name}")

    def linked(self, gone: dict[str, model.Entity]) -> set[str]:
        """Return the identifiers of the entities the address book no
        longer has that something will still link to: an entity it does
        have, or another's, or a statement that isn't the address book's,
        or an entity that's kept, such as an address its locality."""
        kept: set[str] = set()
        linkers: dict[str, set[str]] = {}
        for target in gone:
            for role in ("main", "qualifier"):
                for linker in self.kb.find_links(target, None, role):
                    entity = gone.get(linker)
                    if linker == target:
                        continue
                    if entity is None:
                        kept.add(target)
                        continue
                    statements = {s.id: s for s in entity.statements}
                    if any(t == target and not model.is_sourced(statements[i], self.source)
                           for i, _, t, r in model.entity_links(entity) if r == role):
                        kept.add(target)
                    else:
                        linkers.setdefault(target, set()).add(linker)

        changed = True
        while changed:
            changed = False
            for target, by in linkers.items():
                if target not in kept and by & kept:
                    kept.add(target)
                    changed = True
        return kept
