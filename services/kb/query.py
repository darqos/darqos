# darqos
# Copyright (C) 2026 David Arnold

"""Queries: a darq.kb Query's patterns, matched against a knowledge
base's entities.

check() makes a Plan of a query, checking that it can be answered.  A
Store answers it with the SQL compile() makes of the plan, over its
index tables; a KnowledgeBase in memory, with evaluate(), matching the
patterns one by one.  Each is a check of the other: their rows are the
same.  The design is in doc/kb-service.rst.

Patterns match statements of entities that aren't destroyed or merged,
and a variable's entity is one of those, too.  A string value matches
normalised (see darq.kb.normalise), and is returned as it's written."""

import time

from typing import (TYPE_CHECKING, Callable, Dict, Iterator, List, NamedTuple,
                    Optional, Sequence, Set, Tuple)

from darq.kb import (VALUE_TYPES, AnyValue, Datatype, Entity, EntityValue,
                     Pattern, Query, Rank, Repeat, StringValue, Variable,
                     entity_snaks, normalise)

if TYPE_CHECKING:
    from kb import KnowledgeBase


# Most patterns, and variables, a query can have.
MAX_PATTERNS = 16
MAX_VARIABLES = 16

# Seconds a query can run.
TIME_LIMIT = 2.0

# What a variable's value is.
ENTITY = "entity"
STRING = "string"
STATEMENT = "statement"

# The index table a pattern matches rows of: by its object.
LINKS = "links"
STRINGS = "strings"
SNAKS = "snaks"


class QueryError(ValueError):
    """A query can't be answered: it's malformed."""


class TooCostly(Exception):
    """A query ran past its time limit."""


class Plan(NamedTuple):
    """A checked query: its selected variables, what each variable's
    value is (ENTITY, STRING or STATEMENT), and the table each pattern
    matches."""

    query: Query
    select: List[Variable]
    kinds: Dict[Variable, str]
    tables: List[str]

    def columns(self) -> List[str]:
        """Return what each of the rows' values is."""
        return [self.kinds[v] for v in self.select]


def check(query: Query, datatype: Callable[[str], Optional[Datatype]]) -> Plan:
    """Return the plan for a query.

    :param datatype: Returns a property's datatype, or None if there's
                     no such property.
    :raises QueryError: if it can't be answered."""
    patterns = query.patterns
    positive = [p for p in patterns if not p.negated]
    if not positive:
        raise QueryError("a query needs a pattern to match")
    if len(patterns) > MAX_PATTERNS:
        raise QueryError(f"more than {MAX_PATTERNS} patterns")

    kinds: Dict[Variable, str] = {}

    def kind(v: Variable, k: str):
        if kinds.setdefault(v, k) != k:
            raise QueryError(f"{v!r} is both a {kinds[v]} and a {k}")

    tables = []
    for p in patterns:
        dt = datatype(p.property_id)
        if dt is None:
            raise QueryError(f"no such property: {p.property_id}")
        if isinstance(p.subject, Variable):
            kind(p.subject, ENTITY)
        elif not p.subject:
            raise QueryError("no subject")
        if p.statement is not None:
            kind(p.statement, STATEMENT)

        o = p.object
        if isinstance(o, AnyValue):
            table = SNAKS
        elif isinstance(o, EntityValue):
            table = LINKS
        elif isinstance(o, StringValue):
            table = STRINGS
        elif dt in (Datatype.Item, Datatype.Property):
            table = LINKS
            kind(o, ENTITY)
        elif VALUE_TYPES[dt] is StringValue:
            table = STRINGS
            kind(o, STRING)
        else:
            raise QueryError(f"{p.property_id}'s values can't be a variable's")

        if p.repeat != Repeat.Once:
            if p.role != "main" or p.statement is not None or table != LINKS:
                raise QueryError("only a main snak's link to an entity can be repeated")
            if isinstance(p.subject, Variable) and isinstance(o, Variable):
                raise QueryError("a repeated pattern needs a constant at one end")
        tables.append(table)

    if len(kinds) > MAX_VARIABLES:
        raise QueryError(f"more than {MAX_VARIABLES} variables")

    # A negated pattern's variables are bound by the patterns that must
    # match, or are its own.
    bound = {v for p in positive for v in p.variables()}
    seen: Set[Variable] = set()
    for p in patterns:
        if p.negated:
            own = set(p.variables()) - bound
            if own & seen:
                raise QueryError(f"{sorted(own & seen)[0]!r} is in two negated patterns, "
                                 "and none that must match")
            seen |= own

    select = query.selected or sorted(bound)
    for v in select:
        if v not in bound:
            raise QueryError(f"{v!r} isn't in a pattern that must match")
    return Plan(query, select, kinds, tables)


# SQL

def _live(alias: str) -> str:
    return f"{alias}.destroyed = 0 and {alias}.redirect is null"


def _is_live(expression: str) -> str:
    return f"exists (select 1 from entities x where x.id = {expression} and {_live('x')})"


class _Compiler:
    """Compiles a plan's patterns, as from-items and conditions."""

    def __init__(self, plan: Plan):
        self.plan = plan
        self.ctes: List[str] = []
        self.cte_args: List[object] = []
        self.aliases = 0

    def alias(self) -> str:
        self.aliases += 1
        return f"t{self.aliases}"

    def rank(self, alias: str) -> str:
        if self.plan.query.deprecated:
            return ""
        return f" and {alias}.rank != 'deprecated'"

    def closure(self, p: Pattern) -> Tuple[str, bool]:
        """Add a CTE of the entities a repeated pattern reaches from its
        constant end: backwards from its object, if that's a constant,
        or else forwards from its subject.  Returns its name, and whether
        it reaches back to subjects."""
        name = f"r{len(self.ctes)}"
        backwards = isinstance(p.object, EntityValue)
        start = p.object.entity_id if isinstance(p.object, EntityValue) else p.subject
        near, far = ("target_id", "entity_id") if backwards else ("entity_id", "target_id")
        links = "from links l join entities e on e.id = l.entity_id"
        where = f"l.property_id = ? and l.role = 'main'{self.rank('l')} and {_live('e')}"
        if p.repeat == Repeat.ZeroOrMore:
            seed, seed_args = "select ?", [start]
        else:
            seed, seed_args = f"select l.{far} {links} where {where} and l.{near} = ?", [p.property_id, start]
        self.ctes.append(f"{name} (id) as ({seed} union "
                         f"select l.{far} {links} join {name} r on l.{near} = r.id where {where})")
        self.cte_args += [*seed_args, p.property_id]
        return name, backwards

    def pattern(self,
                p: Pattern,
                table: str,
                bindings: Dict[Variable, str],
                owners: Dict[Variable, str],
                outputs: Dict[Variable, str],
                live: Set[Variable]) -> Tuple[List[str], List[str], List[object]]:
        """Return a pattern's from-items, conditions and their arguments,
        binding its variables in bindings, or equating them with their
        bindings there."""
        froms: List[str] = []
        conditions: List[str] = []
        args: List[object] = []

        def bind(v: Variable, expression: str, is_live: bool, output: Optional[str] = None):
            if v in bindings:
                conditions.append(f"{expression} = {bindings[v]}")
            else:
                bindings[v] = expression
                outputs[v] = output or expression
            if is_live:
                live.add(v)

        def constant(expression: str, value: object):
            conditions.append(f"{expression} = ?")
            args.append(value)

        t = self.alias()
        if p.repeat != Repeat.Once:
            name, backwards = self.closure(p)
            froms.append(f"{name} {t}")
            column = f"{t}.id"
            if backwards:
                if isinstance(p.subject, Variable):
                    bind(p.subject, column, False)
                else:
                    constant(column, p.subject)
            else:
                assert isinstance(p.object, Variable)
                bind(p.object, column, False)
            return froms, conditions, args

        e = f"e{t}"
        froms += [f"{table} {t}", f"entities {e}"]
        conditions += [f"{e}.id = {t}.entity_id and {_live(e)}",
                       f"{t}.property_id = ? and {t}.role = ?{self.rank(t)}"]
        args += [p.property_id, p.role]

        if isinstance(p.subject, Variable):
            bind(p.subject, f"{t}.entity_id", True)
        else:
            constant(f"{t}.entity_id", p.subject)
        if p.statement is not None:
            if p.statement in owners:
                conditions.append(f"{t}.entity_id = {owners[p.statement]}")
            else:
                owners[p.statement] = f"{t}.entity_id"
            bind(p.statement, f"{t}.statement_id", False)

        o = p.object
        if isinstance(o, EntityValue):
            constant(f"{t}.target_id", o.entity_id)
        elif isinstance(o, StringValue):
            constant(f"{t}.value", normalise(o.value))
        elif isinstance(o, Variable):
            if table == LINKS:
                bind(o, f"{t}.target_id", False)
            else:
                bind(o, f"{t}.value", False, f"{t}.text")
        return froms, conditions, args


def compile(plan: Plan, after: Sequence[str] = (), limit: Optional[int] = None) -> Tuple[str, List[object]]:
    """Return the SQL that answers a plan, and its arguments: its rows,
    sorted, after the row after (if any), and at most limit (if
    given)."""
    c = _Compiler(plan)
    bindings: Dict[Variable, str] = {}
    owners: Dict[Variable, str] = {}
    outputs: Dict[Variable, str] = {}
    live: Set[Variable] = set()
    froms: List[str] = []
    conditions: List[str] = []
    args: List[object] = []

    for p, table in zip(plan.query.patterns, plan.tables):
        if not p.negated:
            f, w, a = c.pattern(p, table, bindings, owners, outputs, live)
            froms += f
            conditions += w
            args += a

    # A variable's entity is live.
    for v in sorted(bindings):
        if plan.kinds[v] == ENTITY and v not in live:
            conditions.append(_is_live(bindings[v]))

    # Negated patterns, with their own variables bound in their own
    # subqueries.
    for p, table in zip(plan.query.patterns, plan.tables):
        if p.negated:
            inner = dict(bindings)
            f, w, a = c.pattern(p, table, inner, dict(owners), {}, set())
            for v in sorted(set(inner) - set(bindings)):
                if plan.kinds[v] == ENTITY and v not in live:
                    w.append(_is_live(inner[v]))
            conditions.append(f"not exists (select 1 from {', '.join(f)} where {' and '.join(w) or '1'})")
            args += a

    columns = [f"c{n}" for n in range(len(plan.select))]
    selected = ", ".join(f"{outputs[v]} as {name}" for v, name in zip(plan.select, columns))
    sql = (f"select distinct {selected} from {', '.join(froms)} "
           f"where {' and '.join(conditions) or '1'}")
    if c.ctes:
        sql = f"with recursive {', '.join(c.ctes)} {sql}"
        args = c.cte_args + args

    outer: List[str] = []
    if after:
        outer.append(f"({', '.join(columns)}) > ({', '.join('?' * len(after))})")
        args += list(after)
    sql = f"select * from ({sql})"
    if outer:
        sql += f" where {' and '.join(outer)}"
    sql += f" order by {', '.join(columns)}"
    if limit is not None:
        sql += " limit ?"
        args.append(limit)
    return sql, args


# In memory

class _Fact(NamedTuple):
    """A snak, as an index row: its entity, statement, property, role,
    and value."""

    entity_id: str
    statement_id: str
    property_id: str
    role: str
    value: object


# A variable's binding: an entity's identifier, a string value,
# normalised, and as written, or a statement's identifier and entity's.
_Binding = Tuple[str, str]


class _Evaluator:
    """Matches a plan's patterns against a KnowledgeBase's entities."""

    def __init__(self, kb: "KnowledgeBase", plan: Plan, deadline: float):
        self.kb = kb
        self.plan = plan
        self.deadline = deadline
        self.facts: List[_Fact] = []
        self.steps = 0
        for entity in kb.entities():
            for s, snak, role in entity_snaks(entity):
                if plan.query.deprecated or s.rank != Rank.Deprecated:
                    self.facts.append(_Fact(entity.id, s.id, snak.property_id, role, snak.value))
        self.closures: Dict[int, Set[str]] = {}

    def tick(self):
        self.steps += 1
        if self.steps % 1000 == 0 and time.monotonic() > self.deadline:
            raise TooCostly()

    def live(self, entity_id: str) -> bool:
        entity: Optional[Entity] = self.kb.get(entity_id, follow=False)
        return entity is not None and not entity.destroyed and entity.redirect is None

    def closure(self, n: int, p: Pattern) -> Set[str]:
        """Return the entities a repeated pattern reaches from its
        constant end, as _Compiler.closure does."""
        if n in self.closures:
            return self.closures[n]
        backwards = isinstance(p.object, EntityValue)
        start = p.object.entity_id if isinstance(p.object, EntityValue) else p.subject
        assert isinstance(start, str)
        edges = [(f.entity_id, f.value.entity_id) for f in self.facts
                 if f.property_id == p.property_id and f.role == "main"
                 and isinstance(f.value, EntityValue)]
        if backwards:
            edges = [(b, a) for a, b in edges]
        reached: Set[str] = {start} if p.repeat == Repeat.ZeroOrMore else set()
        frontier = [start]
        while frontier:
            self.tick()
            here = set(frontier)
            frontier = [b for a, b in edges if a in here and b not in reached]
            reached.update(frontier)
        self.closures[n] = reached
        return reached

    def matches(self, n: int, p: Pattern, bindings: Dict[Variable, _Binding]) -> Iterator[Dict[Variable, _Binding]]:
        """Return the bindings that extend some, for a pattern to match."""
        kinds = self.plan.kinds

        def bind(b: Dict[Variable, _Binding], v: Variable, value: _Binding) -> bool:
            if v in b:
                # A string matches normalised, and a statement is its
                # entity's.
                return b[v] == value if kinds[v] == STATEMENT else b[v][0] == value[0]
            if kinds[v] == ENTITY and not self.live(value[0]):
                return False
            b[v] = value
            return True

        if p.repeat != Repeat.Once:
            reached = self.closure(n, p)
            if isinstance(p.object, EntityValue):
                end, ends = p.subject, reached
            else:
                end, ends = p.object, reached
            if isinstance(end, Variable):
                for entity_id in sorted(ends):
                    self.tick()
                    b = dict(bindings)
                    if bind(b, end, (entity_id, entity_id)):
                        yield b
            elif end in ends:
                yield dict(bindings)
            return

        for f in self.facts:
            self.tick()
            if f.property_id != p.property_id or f.role != p.role:
                continue
            b = dict(bindings)
            if isinstance(p.subject, Variable):
                if not bind(b, p.subject, (f.entity_id, f.entity_id)):
                    continue
            elif p.subject != f.entity_id:
                continue
            if p.statement is not None and not bind(b, p.statement, (f.statement_id, f.entity_id)):
                continue

            o = p.object
            if isinstance(o, AnyValue):
                pass
            elif isinstance(o, EntityValue):
                if not (isinstance(f.value, EntityValue) and f.value.entity_id == o.entity_id):
                    continue
            elif isinstance(o, StringValue):
                if not (isinstance(f.value, StringValue) and normalise(f.value.value) == normalise(o.value)):
                    continue
            elif kinds[o] == ENTITY:
                if not (isinstance(f.value, EntityValue) and bind(b, o, (f.value.entity_id, f.value.entity_id))):
                    continue
            elif not (isinstance(f.value, StringValue)
                      and bind(b, o, (normalise(f.value.value), f.value.value))):
                continue
            yield b

    def rows(self) -> List[Tuple[str, ...]]:
        patterns = list(enumerate(self.plan.query.patterns))
        positive = [(n, p) for n, p in patterns if not p.negated]
        negated = [(n, p) for n, p in patterns if p.negated]

        def solve(i: int, b: Dict[Variable, _Binding]) -> Iterator[Dict[Variable, _Binding]]:
            if i == len(positive):
                if not any(next(self.matches(n, p, b), None) is not None for n, p in negated):
                    yield b
                return
            n, p = positive[i]
            for extended in self.matches(n, p, b):
                yield from solve(i + 1, extended)

        def value(b: Dict[Variable, _Binding], v: Variable) -> str:
            return b[v][1] if self.plan.kinds[v] == STRING else b[v][0]

        return sorted({tuple(value(b, v) for v in self.plan.select) for b in solve(0, {})})


def evaluate(kb: "KnowledgeBase", plan: Plan, deadline: float) -> List[Tuple[str, ...]]:
    """Return a plan's rows, sorted, from a KnowledgeBase's entities.

    :raises TooCostly: if it's still running at the deadline, a
                       time.monotonic() time."""
    return _Evaluator(kb, plan, deadline).rows()
