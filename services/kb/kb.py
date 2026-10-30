# darqos
# Copyright (C) 2022-2026 David Arnold

"""A knowledge base: entities, kept in memory, or in a Store.

This is the Knowledge Base service's own (see main.py), and that of
tools working on its database directly, such as addressbook.py.  The
data model is darq.kb's."""

import contextlib
import json
import re

from typing import (TYPE_CHECKING, Dict, Iterable, Iterator, List, Optional,
                    Set, Tuple)

from darq.kb import (INSTANCE_OF, SUBCLASS_OF, VALUE_TYPES, ConflictError,
                     Datatype, Entity, EntityValue, Item, Property,
                     QuantityValue, Rank, Reference, Snak, Statement,
                     StringValue, TextValue, TimeValue, document,
                     encode_statement, entity_links, entity_strings, fold,
                     normalise)

if TYPE_CHECKING:
    from store import Store


def _live(entity: Optional[Entity]) -> bool:
    """Return whether an entity exists, and isn't destroyed or merged."""
    return (entity is not None and not entity.destroyed
            and entity.redirect is None)


class KnowledgeBase:
    """A knowledge base.

    Without a store, entities are kept in memory.  With one, they're
    read from it as they're needed, and kept; changes are written to it
    by commit(), or at the end of a transaction(), and a lookup sees
    changes not yet committed."""

    @staticmethod
    def api() -> "KnowledgeBase":
        return KnowledgeBase()

    def __init__(self, store: Optional["Store"] = None):
        self.store = store

        # Entities, by identifier: all of them, or those read from the
        # store.  Identifiers are unique across both.
        self.items: Dict[str, Item] = {}
        self.properties: Dict[str, Property] = {}

        # Entity identifiers, by language tag and label.
        self.labels: Dict[Tuple[str, str], Set[str]] = {}

        # Entities changed since they were written to the store, and
        # those written in its current transaction.
        self.dirty: Dict[str, Entity] = {}
        self.written: Dict[str, Entity] = {}

    def register(self, entity: Entity) -> Entity:
        """Register an Item or Property, and index its labels."""
        if entity.kb is not None:
            raise ValueError(f"{entity.id} is already registered")
        if not isinstance(entity, (Item, Property)):
            raise TypeError(f"not an Item or Property: {entity!r}")
        if self.get(entity.id, follow=False) is not None:
            raise ValueError(f"{entity.id} is already in use")

        self._cache(entity)
        self.changed(entity)
        return entity

    def _cache(self, entity: Entity):
        """Keep an entity, and index its labels."""
        if isinstance(entity, Property):
            self.properties[entity.id] = entity
        elif isinstance(entity, Item):
            self.items[entity.id] = entity
        entity.kb = self
        for language, label in entity.labels.items():
            self.label_changed(entity, language, None, label)

    def _evict(self, entity: Entity):
        """Forget an entity, so it's read from the store again."""
        self.items.pop(entity.id, None)
        self.properties.pop(entity.id, None)
        for language, label in entity.labels.items():
            ids = self.labels.get((language, label))
            if ids is not None:
                ids.discard(entity.id)
                if not ids:
                    del self.labels[(language, label)]
        entity.kb = None

    def label_changed(self,
                      entity: Entity,
                      language: str,
                      old: Optional[str],
                      new: str):
        """Update the label index for an entity's changed label."""
        if old is not None:
            ids = self.labels.get((language, old))
            if ids is not None:
                ids.discard(entity.id)
                if not ids:
                    del self.labels[(language, old)]
        self.labels.setdefault((language, new), set()).add(entity.id)

    def changed(self, entity: Entity):
        """Note that an entity's changed, to be written to the store."""
        if self.store is not None:
            self.dirty[entity.id] = entity

    def flush(self):
        """Write changed entities to the store, in its transaction.

        An entity whose document is the same as it was isn't written."""
        if self.store is None:
            return
        for entity in list(self.dirty.values()):
            text = document(entity)
            if text != entity.stored:
                self.store.put(entity, text)
                entity.stored = text
                self.written[entity.id] = entity
            del self.dirty[entity.id]

    def commit(self):
        """Write changed entities to the store, and commit them.

        Raises ConflictError, and rolls back, if an entity was changed
        in the store since it was read."""
        if self.store is None:
            return
        try:
            self.flush()
        except BaseException:
            self.rollback()
            raise
        self.store.commit()
        self.written.clear()

    def rollback(self):
        """Discard uncommitted changes.

        Changed entities are forgotten, and read from the store again
        when they're next needed."""
        if self.store is None:
            return
        self.store.rollback()
        for entity in [*self.dirty.values(), *self.written.values()]:
            if entity.kb is self:
                self._evict(entity)
        self.dirty.clear()
        self.written.clear()

    @contextlib.contextmanager
    def transaction(self) -> Iterator["KnowledgeBase"]:
        """Commit the changes made in a with block, or roll them back."""
        try:
            yield self
        except BaseException:
            self.rollback()
            raise
        self.commit()

    def add_item(self,
                 label: Optional[str] = None,
                 item_id: Optional[str] = None,
                 desc: Optional[str] = None,
                 language: str = "en") -> Item:
        """Create and register an item."""
        item = Item(item_id)
        if label is not None:
            item.set_label(label, language)
        if desc is not None:
            item.set_description(desc, language)

        self.register(item)
        return item

    def add_property(self,
                     label: Optional[str],
                     datatype: Datatype,
                     prop_id: Optional[str] = None,
                     desc: Optional[str] = None,
                     language: str = "en") -> Property:
        """Create and register a property."""
        p = Property(datatype, prop_id)
        if label is not None:
            p.set_label(label, language)
        if desc is not None:
            p.set_description(desc, language)

        self.register(p)
        return p

    def replace(self, entity: Entity):
        """Replace a registered entity with a new version of it.

        The new version must have been read at the entity's revision, or
        ConflictError is raised.  It keeps the entity's redirect, and
        whether it's destroyed, which only merge() and destroy() set."""
        current = self.get(entity.id, follow=False)
        if current is None:
            raise LookupError(f"unknown entity: {entity.id}")
        if current.revision != entity.revision:
            raise ConflictError(f"{entity.id} has changed since revision "
                                f"{entity.revision}", entity.id)
        if type(current) is not type(entity):
            raise ValueError(f"{entity.id} is a {type(current).__name__}")
        if entity.kb is not None:
            raise ValueError(f"{entity.id} is already registered")

        entity.redirect = current.redirect
        entity.destroyed = current.destroyed
        entity.stored = current.stored
        self._evict(current)
        self._cache(entity)
        self.changed(entity)

    def get(self, entity_id: str, follow: bool = True) -> Optional[Entity]:
        """Look up an item or property by identifier.

        :param follow: Return the entity a merged one redirects to,
        rather than the merged one itself."""
        entity: Optional[Entity] = \
            self.items.get(entity_id) or self.properties.get(entity_id)
        if entity is None and self.store is not None:
            entity = self.store.get(entity_id)
            if entity is not None:
                self._cache(entity)

        seen: Set[str] = set()
        while follow and entity is not None and entity.redirect is not None:
            if entity.id in seen:
                raise LookupError(f"redirect loop at {entity.id}")
            seen.add(entity.id)
            entity = self.get(entity.redirect, follow=False)
        return entity

    def get_item(self, item_id: str) -> Optional[Item]:
        """Look up an item by identifier."""
        entity = self.get(item_id)
        return entity if isinstance(entity, Item) else None

    def get_property(self, prop_id: str) -> Optional[Property]:
        """Look up a property by identifier."""
        entity = self.get(prop_id)
        return entity if isinstance(entity, Property) else None

    def destroy(self, entity_id: str):
        """Mark an entity destroyed.

        It keeps its statements, and the links to it, but is left out of
        lookups."""
        entity = self.get(entity_id, follow=False)
        if entity is None:
            raise LookupError(f"unknown entity: {entity_id}")
        entity.destroyed = True
        entity.changed()

    def merge(self, old_id: str, new_id: str) -> Entity:
        """Merge one entity into another, and return the other.

        The old entity's statements move to the new one, and its labels
        and aliases become the new one's aliases, unless the new one
        has no label in that language.  The old one redirects to the new
        one, so links to it still lead somewhere."""
        old = self.get(old_id, follow=False)
        new = self.get(new_id)
        if old is None or new is None:
            missing = old_id if old is None else new_id
            raise LookupError(f"unknown entity: {missing}")
        if old is new or old.redirect is not None:
            raise ValueError(f"can't merge {old_id} into {new_id}")
        if type(old) is not type(new):
            raise ValueError("can't merge an item and a property")

        names = [(lang, label) for lang, label in old.labels.items()]
        names += [(lang, a) for lang, aliases in old.aliases.items()
                  for a in aliases]
        for language, name in names:
            if language not in new.labels:
                new.set_label(name, language)
            elif (name != new.labels[language]
                  and name not in new.get_aliases(language)):
                new.add_alias(name, language)
        for language, description in old.descriptions.items():
            if language not in new.descriptions:
                new.set_description(description, language)
        for s in list(old.statements):
            old.remove_statement(s)
            new.add_statement(s)

        old.redirect = new.id
        old.changed()
        return new

    def entities(self) -> Iterator[Entity]:
        """Return all entities, except destroyed and merged ones."""
        if self.store is not None:
            self.flush()
            ids = self.store.ids()
        else:
            ids = sorted([*self.items, *self.properties])
        for entity_id in ids:
            entity = self.get(entity_id, follow=False)
            if entity is not None and _live(entity):
                yield entity

    def _entities(self, ids: Iterable[str]) -> List[Entity]:
        """Return the entities with some identifiers, if live."""
        entities = [self.get(i, follow=False) for i in ids]
        return [e for e in entities if e is not None and _live(e)]

    def _label_ids(self, label: str, language: str) -> List[str]:
        if self.store is not None:
            self.flush()
            return self.store.find_label(label, language)
        return sorted(self.labels.get((language, label), ()))

    def find_labelled(self, label: str, language: str = "en") -> List[Entity]:
        """Look up entities by label, in order of identifier."""
        return self._entities(self._label_ids(label, language))

    def find_items(self, label: str, language: str = "en") -> List[Item]:
        """Look up items by label, in order of identifier."""
        return [e for e in self._entities(self._label_ids(label, language))
                if isinstance(e, Item)]

    def find_item(self, label: str, language: str = "en") -> Optional[Item]:
        """Look up item by label.

        Raises LookupError if more than one item has the label."""
        items = self.find_items(label, language)
        if len(items) > 1:
            raise LookupError(f"{len(items)} items labelled {label!r}")
        return items[0] if items else None

    def find_properties(self,
                        label: str,
                        language: str = "en") -> List[Property]:
        """Look up properties by label, in order of identifier."""
        return [e for e in self._entities(self._label_ids(label, language))
                if isinstance(e, Property)]

    def find_property(self,
                      label: str,
                      language: str = "en") -> Optional[Property]:
        """Look up property by label.

        Raises LookupError if more than one property has the label."""
        properties = self.find_properties(label, language)
        if len(properties) > 1:
            raise LookupError(
                f"{len(properties)} properties labelled {label!r}")
        return properties[0] if properties else None

    def search(self, text: str, language: str = "en") -> List[Entity]:
        """Find entities whose label or an alias has words starting with
        each of the text's words, ignoring case."""
        if self.store is not None:
            self.flush()
            return self._entities(self.store.search(text, language))

        words = re.findall(r"\w+", fold(text))
        if not words:
            return []
        found = []
        for entity in self.entities():
            names = [entity.labels.get(language, ""),
                     *entity.get_aliases(language)]
            for name in names:
                have = re.findall(r"\w+", fold(name))
                if all(any(h.startswith(w) for h in have) for w in words):
                    found.append(entity)
                    break
        return found

    def find_by_value(self, property_id: str, value: str) -> List[Entity]:
        """Find entities with a string value for a property.

        Values are compared normalised, so phone numbers match however
        they're written."""
        value = normalise(value)
        if self.store is not None:
            self.flush()
            return self._entities(self.store.find_value(property_id, value))
        return [e for e in self.entities()
                if any(p == property_id and v == value
                       for _, p, v in entity_strings(e))]

    def find_links(self,
                   target_id: str,
                   property_id: Optional[str] = None,
                   role: str = "main") -> List[Entity]:
        """Find the entities that link to one.

        :param property_id: The linking property, or any.
        :param role: Where the link is in a statement: "main",
        "qualifier", or "reference"."""
        if self.store is not None:
            self.flush()
            return self._entities(
                self.store.find_links(target_id, property_id, role))
        return [e for e in self.entities()
                if any(t == target_id and r == role
                       and (property_id is None or p == property_id)
                       for _, p, t, r in entity_links(e))]

    def instances(self, class_id: str) -> List[Entity]:
        """Find the instances of a class, or of any of its subclasses."""
        if self.store is not None:
            self.flush()
            return self._entities(self.store.instances(class_id))

        def targets(entity: Entity, property_id: str) -> Set[str]:
            return {t for _, p, t, r in entity_links(entity)
                    if p == property_id and r == "main"}

        entities = list(self.entities())
        classes = {class_id}
        while True:
            more = {e.id for e in entities
                    if e.id not in classes
                    and targets(e, SUBCLASS_OF) & classes}
            if not more:
                break
            classes |= more
        return [e for e in entities if targets(e, INSTANCE_OF) & classes]

    def check(self, statement: Statement):
        """Check a statement against this KnowledgeBase.

        Raises ValueError unless each snak's property is registered, and
        its value has the property's datatype, refers to a registered
        entity of the right kind, and is within its bounds."""
        snaks = [statement.main_snak, *statement.qualifiers]
        for reference in statement.references:
            snaks.extend(reference.snaks)

        for snak in snaks:
            prop = self.get_property(snak.property_id)
            if prop is None:
                raise ValueError(f"unknown property: {snak.property_id}")

            value = snak.value
            expected = VALUE_TYPES[prop.datatype]
            if not isinstance(value, expected):
                raise ValueError(
                    f"{prop.id} needs a {expected.__name__}, "
                    f"not {type(value).__name__}")

            if isinstance(value, EntityValue):
                kind = Item if prop.datatype == Datatype.Item else Property
                if not isinstance(self.get(value.entity_id), kind):
                    raise ValueError(
                        f"{prop.id}: unknown {prop.datatype.name.lower()}: "
                        f"{value.entity_id}")

            elif isinstance(value, QuantityValue):
                if (value.unit is not None
                        and self.get_item(value.unit) is None):
                    raise ValueError(f"{prop.id}: unknown unit: {value.unit}")
                if ((value.lower is not None and value.lower > value.amount)
                        or (value.upper is not None
                            and value.upper < value.amount)):
                    raise ValueError(
                        f"{prop.id}: amount out of bounds: {value}")

    def describe(self, entity_id: str, language: str = "en") -> str:
        """Return an entity's statements as text, with labels for ids."""
        entity = self.get(entity_id)
        if entity is None:
            raise LookupError(f"unknown entity: {entity_id}")

        lines = [self._name(entity_id, language)]
        description = entity.get_description(language)
        if description is not None:
            lines[0] += f": {description}"
        aliases = entity.get_aliases(language)
        if aliases:
            lines.append(f"  also known as: {', '.join(aliases)}")

        for s in entity.statements:
            line = "  " + self._snak(s.main_snak, language)
            if s.rank != Rank.Normal:
                line += f" ({s.rank.name.lower()})"
            lines.append(line)
            for q in s.qualifiers:
                lines.append("    " + self._snak(q, language))
            for r in s.references:
                lines.append("    source: " + "; ".join(
                    self._snak(a, language) for a in r.snaks))
        return "\n".join(lines)

    def _name(self, entity_id: str, language: str) -> str:
        """Return an entity's label and identifier, or its identifier."""
        entity = self.get(entity_id)
        label = entity.get_label(language) if entity is not None else None
        return f"{label} ({entity_id})" if label is not None else entity_id

    def _snak(self, snak: Snak, language: str) -> str:
        """Return a snak as text."""
        prop = self.get_property(snak.property_id)
        label = prop.get_label(language) if prop is not None else None
        name = label if label is not None else snak.property_id

        value = snak.value
        if isinstance(value, EntityValue):
            text = self._name(value.entity_id, language)
        elif isinstance(value, StringValue):
            text = repr(value.value)
        elif isinstance(value, TextValue):
            text = value.get_text(language) or repr(value)
        elif isinstance(value, QuantityValue) and value.unit is not None:
            unit = self.get(value.unit)
            unit_label = unit.get_label(language) if unit else None
            text = str(QuantityValue(value.amount, None,
                                     value.lower, value.upper))
            text += f" {unit_label or value.unit}"
        else:
            text = str(value)
        return f"{name}: {text}"


def _content(statement: Statement) -> str:
    """Return a statement's content, without its identifier, as text."""
    return json.dumps(encode_statement(statement, with_id=False),
                      sort_keys=True)


class Import:
    """Entities and statements from a source, such as an address book.

    An import can be repeated.  The first time it gets an entity, by
    item() or property(), the statements an earlier import from the same
    source made about it are removed, and the entity's other statements
    are kept.  finish() gives each new statement a reference to the
    source: one that's the same as a statement removed keeps its
    identifier, and its place, so an entity that hasn't changed isn't
    written again.
    An entity the earlier import made statements about, which this one
    didn't get, loses them, and is destroyed if it has none left."""

    def __init__(self, kb: KnowledgeBase, source_id: str, stated_in: str):
        """Constructor.

        :param kb: KnowledgeBase to import into.
        :param source_id: Identifier of the source's item.
        :param stated_in: Identifier of the property for a reference to
        the source, such as stated in (P248)."""
        self.kb = kb
        self.source_id = source_id
        self.stated_in = stated_in

        # Entities got, by identifier; the statements on them that are
        # kept; the identifiers of those removed, by content; and the
        # order of their statements, by identifier.
        self.entities: Dict[str, Entity] = {}
        self.kept: Set[str] = set()
        self.removed: Dict[str, Dict[str, List[str]]] = {}
        self.order: Dict[str, Dict[str, int]] = {}

    def _sourced(self, statement: Statement) -> bool:
        """Return whether a statement has a reference to the source."""
        return any(a.property_id == self.stated_in
                   and isinstance(a.value, EntityValue)
                   and a.value.entity_id == self.source_id
                   for r in statement.references for a in r.snaks)

    def _got(self, entity: Entity):
        """Remove an entity's statements from an earlier import."""
        if entity.id in self.entities:
            return
        self.entities[entity.id] = entity
        self.order[entity.id] = {s.id: i for i, s in
                                 enumerate(entity.statements)}
        removed: Dict[str, List[str]] = {}
        for s in list(entity.statements):
            if self._sourced(s):
                removed.setdefault(_content(s), []).append(s.id)
                entity.remove_statement(s)
            else:
                self.kept.add(s.id)
        self.removed[entity.id] = removed

    def _describe(self,
                  entity: Entity,
                  label: Optional[str],
                  desc: Optional[str],
                  language: str):
        if label is not None and entity.get_label(language) != label:
            entity.set_label(label, language)
        if desc is not None and entity.get_description(language) != desc:
            entity.set_description(desc, language)

    def item(self,
             item_id: str,
             label: Optional[str] = None,
             desc: Optional[str] = None,
             language: str = "en") -> Item:
        """Return an item, creating it if needed, with a label."""
        entity = self.kb.get(item_id)
        if entity is None:
            entity = self.kb.add_item(label, item_id, desc, language)
        elif not isinstance(entity, Item):
            raise ValueError(f"{item_id} isn't an item")
        else:
            self._describe(entity, label, desc, language)
        self._got(entity)
        return entity

    def property(self,
                 property_id: str,
                 label: Optional[str],
                 datatype: Datatype,
                 desc: Optional[str] = None,
                 language: str = "en") -> Property:
        """Return a property, creating it if needed, with a label."""
        entity = self.kb.get(property_id)
        if entity is None:
            entity = self.kb.add_property(label, datatype, property_id, desc,
                                          language)
        elif not isinstance(entity, Property):
            raise ValueError(f"{property_id} isn't a property")
        else:
            self._describe(entity, label, desc, language)
            if entity.get_datatype() != datatype:
                entity.set_datatype(datatype)
        self._got(entity)
        return entity

    def finish(self):
        """Give the new statements references to the source, and remove
        an earlier import's statements about entities not got since."""
        for entity in self.entities.values():
            removed = self.removed[entity.id]
            for s in entity.statements:
                if s.id in self.kept:
                    continue
                if not self._sourced(s):
                    s.add_reference(Reference().add(
                        self.stated_in, EntityValue(self.source_id)))
                ids = removed.get(_content(s))
                if ids:
                    s.id = ids.pop(0)

            # Statements that were there before go back in their places,
            # and new ones go after them.
            order = self.order[entity.id]
            entity.statements.sort(key=lambda st: order.get(st.id, len(order)))

        for entity in self.kb.find_links(self.source_id, self.stated_in,
                                         "reference"):
            if entity.id in self.entities:
                continue
            for s in list(entity.statements):
                if self._sourced(s):
                    entity.remove_statement(s)
            if not entity.statements:
                self.kb.destroy(entity.id)


if __name__ == "__main__":
    kb = KnowledgeBase()

    # Define some properties.
    kb.add_property("instance of", Datatype.Item, "P31")
    kb.add_property("educated at", Datatype.Item, "P69")
    kb.add_property("end time", Datatype.Time, "P582")
    kb.add_property("height", Datatype.Quantity, "P2048")
    kb.add_property("stated in", Datatype.Item, "P248")

    # Define some items.  The source has no Q-id, so it gets a UUID.
    kb.add_item("human", "Q5")
    kb.add_item("St John's College", "Q691283")
    kb.add_item("metre", "Q11573")
    source = kb.add_item("Example biography")

    douglas = kb.add_item("Douglas Adams", "Q42",
                          "English author and humorist")
    douglas.set_label("Douglas Adams", "fr")
    douglas.add_alias("Douglas Noël Adams")

    # Simple statement: Douglas Adams -> instance of -> human.
    douglas.claim("P31", EntityValue("Q5"))

    # Statement with a qualifier and a reference: Douglas Adams ->
    # educated at -> St John's College (end time -> 1974).
    douglas.claim("P69", EntityValue("Q691283")) \
        .add_qualifier("P582", TimeValue.parse("1974")) \
        .add_reference(Reference().add("P248", EntityValue(source.get_id())))

    # Quantity with a unit and bounds.
    douglas.claim("P2048", QuantityValue("1.96", "Q11573", "1.95", "1.97"))

    for statement in douglas.get_statements():
        kb.check(statement)

    print(kb.describe("Q42"))
    print(kb.find_item("Douglas Adams", "fr"))
