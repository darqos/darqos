# darqos
# Copyright (C) 2026 David Arnold
"""Scale test for the Knowledge Base's store.

Grows one database, in steps, with synthetic entities shaped like
model B in doc/entity-vs-item.rst (every item an entity: mostly emails,
then files, photos, and people and organisations), and measures, at
each step, the speed of writes and lookups, the database's size, and
the process's memory.  It runs the service's own code, KnowledgeBase
and Store from services/kb, in this process, without the p-kernel:
each KB p-call would add about 0.6 ms.

    venv313/bin/python bench/kb_scale.py --dir /var/tmp/kb-scale \\
        --steps 10000,100000,1000000,3000000

Results go to kb-scale.csv in the directory, a row per measurement,
and a summary to standard output.  The data is made up, from a seed,
so runs can be compared; nothing reads or writes run/kb.sqlite.

The service's KnowledgeBase keeps every entity it reads or writes, and
never forgets one, which at millions of entities would exhaust memory.
So the cache's cost per entity is measured over the first few thousand
writes, and after that, written entities are forgotten after each
commit, as a cache with a bound would forget them.
"""

import argparse
import csv
import gc
import importlib
import os
import pathlib
import random
import resource
import statistics
import sys
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import darq.kb as model  # noqa: E402
from darq.kb import (Datatype, EntityValue, Item, Query, StringValue,  # noqa: E402
                     TimePrecision, TimeValue)


def load(name: str):
    # The service's modules import each other by name, as they do run
    # from their directory.
    path = str(ROOT / "services" / "kb")
    sys.path.insert(0, path)
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(path)


kb_module = load("kb")
store_module = load("store")
query_module = load("query")

# Shares of each kind of entity created.
MIX = (("email", 0.80), ("file", 0.15), ("photo", 0.04), ("person", 0.01))

# Organisations, as a share of people.
ORGS_PER_PERSON = 0.2

WORDS = ("project budget meeting review draft report quarterly invoice "
         "update notes travel schedule design proposal contract team "
         "release plan agenda summary request feedback status launch "
         "hiring offer lunch dinner flight hotel visa conference talk "
         "paper slides demo bug patch build test deploy server network "
         "backup storage kernel compositor terminal index search history "
         "photo family holiday beach mountain garden birthday wedding "
         "school university course lecture exam thesis research grant "
         "dataset model analysis chart figure table appendix chapter "
         "book edition author editor publisher review").split()

FIRST = ("Alice Bob Carol David Erin Frank Grace Heidi Ivan Judy Karl Liam "
         "Mia Noah Olivia Pete Quinn Ruth Sam Tina Uma Victor Wendy Xavier "
         "Yara Zoe Ahmed Bea Chen Dmitri Elena Farah Goran Hiro Ines Jun "
         "Kofi Lena Mateo Nadia Omar Priya Raj Sofia Tariq Ulla Vera Wei").split()

LAST = ("Smith Jones Brown Taylor Wilson Nguyen Li Garcia Kim Patel Müller "
        "Rossi Dubois Silva Kowalski Novak Andersen Tanaka Okafor Haddad "
        "Ivanova Costa Murphy Walsh Kelly Byrne Ryan Chen Wang Zhang Singh "
        "Khan Ali Lopez Martin Bernard Petit Moreau Fischer Weber Becker "
        "Hoffmann Schulz Koch Richter Klein Wolf Schröder Neumann").split()

FILE_TYPES = ("public.utf8-plain-text", "com.adobe.pdf", "public.html",
              "org.idpf.epub-container", "public.json", "public.csv")

PHOTO_TYPES = ("public.jpeg", "public.heic", "public.png")


class Vocabulary:
    """The properties and classes the synthetic entities use."""

    def __init__(self, kb):
        def p(label: str, datatype: Datatype, prop_id: Optional[str] = None) -> str:
            return kb.add_property(label, datatype, prop_id).id

        self.instance_of = p("instance of", Datatype.Item, model.INSTANCE_OF)
        self.subclass_of = p("subclass of", Datatype.Item, model.SUBCLASS_OF)
        self.darqos_type = p("DarqOS type", Datatype.String, model.DARQOS_TYPE)
        self.sender = p("sender", Datatype.Item)
        self.recipient = p("recipient", Datatype.Item)
        self.date = p("point in time", Datatype.Time, model.POINT_IN_TIME)
        self.message_id = p("message ID", Datatype.ExternalId)
        self.tag = p("has tag", Datatype.Item)
        self.author = p("author", Datatype.Item, "P50")
        self.size = p("data size", Datatype.String)
        self.depicts = p("depicts", Datatype.Item, "P180")
        self.employer = p("employer", Datatype.Item, "P108")
        self.email = p("email address", Datatype.Url, "P968")

        def c(label: str) -> str:
            return kb.add_item(label).id

        self.item = c("DarqOS item")
        self.email_class = c("email message")
        self.file_class = c("file")
        self.photo_class = c("photograph")
        self.person_class = c("human")
        self.org_class = c("organisation")
        for cls in (self.email_class, self.file_class, self.photo_class):
            kb.get(cls).claim(self.subclass_of, EntityValue(self.item))
        self.tags = [c(f"tag {w}") for w in WORDS[:60]]


class Corpus:
    """Makes synthetic entities, and remembers what's needed to look
    them up again."""

    def __init__(self, kb, seed: int):
        self.kb = kb
        self.random = random.Random(seed)
        self.v = Vocabulary(kb)
        self.operator = kb.add_item("The Operator").id
        kb.get(self.operator).claim(self.v.instance_of, EntityValue(self.v.person_class))
        self.people: List[str] = [self.operator]
        self.orgs: List[str] = []
        self.ids: Dict[str, List[str]] = {kind: [] for kind, _ in MIX}
        self.pinned = set(self.people) | set(self.v.tags) | {
            self.v.item, self.v.email_class, self.v.file_class,
            self.v.photo_class, self.v.person_class, self.v.org_class}
        self.message = 0

    def words(self, low: int, high: int) -> str:
        return " ".join(self.random.choice(WORDS) for _ in range(self.random.randint(low, high)))

    def date(self) -> TimeValue:
        r = self.random
        return TimeValue(r.randint(2005, 2026), r.randint(1, 12), r.randint(1, 28),
                         r.randint(0, 23), r.randint(0, 59), r.randint(0, 59),
                         precision=TimePrecision.Seconds)

    def person(self) -> str:
        """Pick a person, some far more often than others."""
        people = self.people
        return people[min(int(self.random.paretovariate(1.2)) - 1, len(people) - 1)]

    def make(self, kind: str) -> Item:
        r, v = self.random, self.v
        item = Item()
        if kind == "email":
            self.message += 1
            item.set_label(self.words(2, 8).capitalize())
            item.claim(v.instance_of, EntityValue(v.email_class))
            item.claim(v.darqos_type, StringValue("public.email-message"))
            sender = self.person()
            item.claim(v.sender, EntityValue(sender))
            recipients = {self.operator} if sender != self.operator else set()
            for _ in range(r.randint(0, 4)):
                recipients.add(self.person())
            for p in sorted(recipients or {self.person()}):
                item.claim(v.recipient, EntityValue(p))
            item.claim(v.date, self.date())
            item.claim(v.message_id, StringValue(f"<{self.message}.{r.getrandbits(48):x}@example.org>"))
            for _ in range(r.choice((0, 0, 0, 1, 1, 2))):
                item.claim(v.tag, EntityValue(r.choice(v.tags)))
        elif kind == "file":
            item.set_label(self.words(1, 4) + r.choice((".txt", ".pdf", ".html", ".epub", ".json", ".csv")))
            item.claim(v.instance_of, EntityValue(v.file_class))
            item.claim(v.darqos_type, StringValue(r.choice(FILE_TYPES)))
            item.claim(v.date, self.date())
            item.claim(v.size, StringValue(str(r.randint(100, 50_000_000))))
            if r.random() < 0.2:
                item.claim(v.author, EntityValue(self.person()))
        elif kind == "photo":
            item.set_label(f"IMG_{r.randint(0, 99999):05d}")
            item.claim(v.instance_of, EntityValue(v.photo_class))
            item.claim(v.darqos_type, StringValue(r.choice(PHOTO_TYPES)))
            item.claim(v.date, self.date())
            for _ in range(r.randint(0, 3)):
                item.claim(v.depicts, EntityValue(self.person()))
        else:
            # A person, and sometimes an organisation, shaped roughly like
            # the address book's: a dozen statements.
            first, last = r.choice(FIRST), r.choice(LAST)
            item.set_label(f"{first} {last}")
            item.add_alias(first)
            item.set_description(self.words(2, 5))
            item.claim(v.instance_of, EntityValue(v.person_class))
            for _ in range(r.randint(1, 3)):
                item.claim(v.email, StringValue(f"mailto:{first.lower()}.{r.getrandbits(32):x}@example.org"))
            if self.orgs:
                item.claim(v.employer, EntityValue(r.choice(self.orgs)))
            for _ in range(r.randint(3, 8)):
                item.claim(v.tag, EntityValue(r.choice(v.tags)))
        return item

    def org(self) -> Item:
        item = Item()
        item.set_label(f"{self.random.choice(LAST)} {self.random.choice(('Ltd', 'Inc', 'GmbH', 'Pty Ltd', 'Group'))}")
        item.claim(self.v.instance_of, EntityValue(self.v.org_class))
        return item

    def kind(self) -> str:
        x, total = self.random.random(), 0.0
        for kind, share in MIX:
            total += share
            if x < total:
                return kind
        return MIX[-1][0]


# Variants of the store to compare, by name: the store as it is, and
# changes to it being tried.
class LargeCache(store_module.Store):
    """The store, with a 64 MiB page cache, rather than SQLite's 2 MiB."""

    def __init__(self, path: str):
        super().__init__(path)
        self.db.execute("pragma cache_size = -65536")


STORES = {"as-is": store_module.Store, "cache-64m": LargeCache}

# Identifiers in a batch, as the service's MAX_IDS.
BATCH = 1000


def rss() -> int:
    """Return the process's resident size, in bytes."""
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


class Bench:
    def __init__(self, directory: pathlib.Path, seed: int, batch: int, samples: int, variant: str):
        self.dir = directory
        self.Store = STORES[variant]
        self.path = str(directory / "kb.sqlite")
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass
        self.store = self.Store(self.path)
        self.kb = kb_module.KnowledgeBase(self.store)
        self.batch = batch
        self.samples = samples
        with self.kb.transaction():
            self.corpus = Corpus(self.kb, seed)
        self.random = random.Random(seed + 1)
        self.count = 0
        self.results = open(directory / "kb-scale.csv", "w", newline="")
        self.csv = csv.writer(self.results)
        self.csv.writerow(["entities", "metric", "n", "p50_ms", "p99_ms", "mean_ms", "value"])
        self.summary: List[Tuple[int, str, str]] = []

    def close(self):
        self.results.close()
        self.store.close()

    def record(self, metric: str, times: Sequence[float] = (), value: object = ""):
        row: List[object] = [self.count, metric, len(times)]
        shown = ""
        if times:
            ms = sorted(t * 1000 for t in times)
            p50 = statistics.median(ms)
            p99 = ms[min(len(ms) - 1, int(len(ms) * 0.99))]
            row += [f"{p50:.3f}", f"{p99:.3f}", f"{statistics.fmean(ms):.3f}"]
            shown = f"p50 {p50:9.3f} ms  p99 {p99:9.3f} ms"
        else:
            row += ["", "", ""]
        row.append(value)
        if value != "":
            shown += f"  {value}"
        self.csv.writerow(row)
        self.results.flush()
        self.summary.append((self.count, metric, shown))
        print(f"{self.count:>9}  {metric:<28} {shown}", flush=True)

    def forget(self, entities):
        """Forget written entities, as a bounded cache would."""
        for entity in entities:
            if entity.id not in self.corpus.pinned and entity.kb is self.kb:
                self.kb._evict(entity)

    # Writes.

    def grow(self, target: int, evict: bool) -> List[float]:
        """Create entities until there are target of them, a batch per
        transaction, checking each statement as the service does."""
        corpus, kb = self.corpus, self.kb
        times: List[float] = []
        while self.count < target:
            made = []
            start = time.perf_counter()
            for _ in range(min(self.batch, target - self.count)):
                kind = corpus.kind()
                if kind == "person" and len(corpus.people) * ORGS_PER_PERSON > len(corpus.orgs):
                    entity = corpus.org()
                    kb.register(entity)
                    corpus.orgs.append(entity.id)
                else:
                    entity = corpus.make(kind)
                    kb.register(entity)
                    corpus.ids[kind].append(entity.id)
                    if kind == "person":
                        corpus.people.append(entity.id)
                for s in entity.statements:
                    kb.check(s)
                made.append(entity)
                self.count += 1
            kb.commit()
            times.append(time.perf_counter() - start)
            if evict:
                self.forget(made)
        return times

    # Lookups.

    def timed(self, metric: str, run: Callable[[], object], n: Optional[int] = None, sized: bool = False):
        times, sizes, stopped = [], [], 0
        for _ in range(n or self.samples):
            start = time.perf_counter()
            try:
                result = run()
            except query_module.TooCostly:
                # Past the query's time limit: counted, not timed.
                stopped += 1
                continue
            times.append(time.perf_counter() - start)
            if sized:
                sizes.append(len(result))  # type: ignore[arg-type]
        value = f"rows median {int(statistics.median(sizes))} max {max(sizes)}" if sized and sizes else ""
        if stopped:
            value = f"{value}  {stopped} of {n or self.samples} too costly".strip()
        self.record(metric, times, value)

    def measure(self):
        corpus, store, kb, r = self.corpus, self.store, self.kb, self.random
        v = corpus.v
        emails = corpus.ids["email"]

        def any_id() -> str:
            kind = r.choice([k for k, ids in corpus.ids.items() if ids])
            return r.choice(corpus.ids[kind])

        # Reads from the store itself, as a cache miss is.
        self.timed("get", lambda: store.get(any_id()))

        labels = []
        for _ in range(self.samples):
            entity = store.get(any_id())
            if entity is not None and entity.labels:
                labels.append(entity.labels["en"])
        labels = labels or ["nothing"]
        self.timed("find_label", lambda: store.find_label(r.choice(labels), "en"), sized=True)
        self.timed("search (two words)",
                   lambda: store.search(f"{r.choice(WORDS)} {r.choice(WORDS)[:3]}", "en"),
                   n=max(5, self.samples // 10), sized=True)
        def message_id() -> str:
            entity = store.get(r.choice(emails))
            assert entity is not None
            return entity.get_statements(v.message_id)[0].main_snak.value.value

        if emails:
            ids = [message_id() for _ in range(self.samples)]
            self.timed("find_value (message ID)", lambda: store.find_value(v.message_id, r.choice(ids)), sized=True)
        self.timed("find_links (a person)",
                   lambda: store.find_links(r.choice(corpus.people[1:] or corpus.people), v.sender, "main"),
                   sized=True)
        # Lookups with millions of answers, a batch of BATCH at a time,
        # as the service answers them: the first batch, and every batch.
        def walk(lookup: Callable[[str, int], List[str]]) -> List[str]:
            found, after = [], ""
            while True:
                batch = lookup(after, BATCH)
                if not batch:
                    return found
                found += batch
                after = batch[-1]

        hubs = (("find_links (the operator)",
                 lambda after, n: store.find_links(corpus.operator, v.recipient, "main", after, n)),
                ("instances (email)", lambda after, n: store.instances(v.email_class, after, n)),
                ("instances (DarqOS item, via subclasses)", lambda after, n: store.instances(v.item, after, n)))
        for metric, lookup in hubs:
            self.timed(f"{metric}, first batch", lambda: lookup("", BATCH), sized=True)
            self.timed(f"{metric}, every batch", lambda: walk(lookup), n=1, sized=True)

        def tagged_from() -> List[Tuple[str, ...]]:
            q = Query()
            e = q.variable()
            # The people who send the most, so it finds something.
            q.match(e, v.sender, r.choice(corpus.people[1:21] or corpus.people))
            q.match(e, v.tag, r.choice(v.tags[:10]))
            q.select(e)
            return kb.query(q, time_limit=60)[1]

        def from_employees() -> List[Tuple[str, ...]]:
            q = Query()
            e, p = q.variables(2)
            q.match(e, v.sender, p)
            q.match(p, v.employer, r.choice(corpus.orgs) if corpus.orgs else v.org_class)
            q.select(e)
            return kb.query(q, time_limit=60)[1]

        self.timed("query (emails from X tagged T)", tagged_from, n=max(5, self.samples // 10), sized=True)
        self.timed("query (emails from O's staff)", from_employees, n=max(5, self.samples // 10), sized=True)

        # A new revision of an existing entity, through the KnowledgeBase.
        def update():
            entity = kb.get(any_id())
            assert entity is not None
            entity.claim(v.tag, EntityValue(r.choice(v.tags)))
            kb.commit()
            self.forget([entity])

        self.timed("update (add a tag)", update)

    def size(self):
        db = self.store.db
        db.execute("pragma wal_checkpoint(truncate)")
        total = os.path.getsize(self.path)
        self.record("database bytes", value=total)
        self.record("bytes per entity", value=total // max(self.count, 1))
        try:
            rows = db.execute("select name, sum(pgsize) from dbstat group by name order by 2 desc").fetchall()
        except Exception:
            rows = []
        for name, size in rows[:12]:
            self.record(f"size: {name}", value=size)
        revisions = db.execute("select count(*) from revisions").fetchone()[0]
        self.record("revisions", value=revisions)

    def reopen(self):
        """Refresh the planner's statistics, then close and reopen the
        store, as the service does at boot."""
        start = time.perf_counter()
        self.store.optimize()
        self.record("optimize", [time.perf_counter() - start])
        self.store.close()
        start = time.perf_counter()
        self.store = self.Store(self.path)
        self.store.get(self.corpus.operator)
        elapsed = time.perf_counter() - start
        self.kb.store = self.store
        self.record("open", [elapsed])


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--dir", default="/var/tmp/kb-scale",
                        help="directory for the database and results (default %(default)s)")
    parser.add_argument("--steps", default="10000,100000,1000000,3000000",
                        help="entity counts to measure at (default %(default)s)")
    parser.add_argument("--batch", type=int, default=1,
                        help="entities per transaction (default %(default)s, as a type implementation writes)")
    parser.add_argument("--samples", type=int, default=200,
                        help="samples per lookup (default %(default)s)")
    parser.add_argument("--cache-sample", type=int, default=5000,
                        help="entities written without forgetting them, to measure the cache (default %(default)s)")
    parser.add_argument("--variant", choices=sorted(STORES), default="as-is",
                        help="the store variant (default %(default)s)")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)

    steps = sorted(int(s) for s in args.steps.split(","))
    directory = pathlib.Path(args.dir)
    directory.mkdir(parents=True, exist_ok=True)
    bench = Bench(directory, args.seed, args.batch, args.samples, args.variant)
    started = time.perf_counter()
    try:
        # The cache's cost: entities written and kept, as the service
        # keeps them.
        gc.collect()
        before = rss()
        start, first = time.perf_counter(), bench.count
        sample = min(args.cache_sample, steps[0])
        times = bench.grow(sample, evict=False)
        gc.collect()
        kept = len(bench.kb.items) + len(bench.kb.properties)
        bench.record("cache bytes per entity", value=(rss() - before) // max(kept, 1))
        bench.forget(list(bench.kb.items.values()))
        gc.collect()

        for step in steps:
            times += bench.grow(step, evict=True)
            elapsed = time.perf_counter() - start
            bench.record("create (per transaction)", times)
            bench.record("create rate", value=f"{(bench.count - first) / max(elapsed, 1e-9):.0f}/s")
            times = []
            bench.size()
            bench.reopen()
            bench.measure()
            bench.record("process RSS", value=rss())
            bench.record("max RSS", value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)
            bench.record("elapsed", value=f"{time.perf_counter() - started:.0f}s")
            start, first = time.perf_counter(), bench.count
    finally:
        bench.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
