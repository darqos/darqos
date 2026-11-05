# darqos
# Copyright (C) 2026 David Arnold

# A small run of the KB scale test, bench/kb_scale.py, so it keeps
# working as the store changes: to a few thousand entities, checking
# that every measurement is made.

import csv
import importlib.util
import pathlib

import pytest

BENCH = pathlib.Path(__file__).parent.parent / "bench" / "kb_scale.py"


def load():
    spec = importlib.util.spec_from_file_location("kb_scale", BENCH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("variant", ["as-is", "cache-64m"])
def test_small_run(tmp_path, variant):
    bench = load()
    assert bench.main(["--dir", str(tmp_path), "--steps", "300,1000", "--samples", "5",
                       "--cache-sample", "200", "--variant", variant]) == 0

    with open(tmp_path / "kb-scale.csv") as f:
        rows = list(csv.DictReader(f))
    metrics = {(int(r["entities"]), r["metric"]) for r in rows}
    for step in (300, 1000):
        for metric in ("create rate", "bytes per entity", "get", "find_label", "search (two words)",
                       "find_value (message ID)", "find_links (the operator), first batch",
                       "find_links (the operator), every batch", "instances (email), every batch",
                       "query (emails from O's staff)", "update (add a tag)", "optimize", "open"):
            assert (step, metric) in metrics, (step, metric)

    # Every email has the operator as a recipient unless they sent it,
    # and the lookups find them.
    found = [r for r in rows if r["metric"] == "find_links (the operator), every batch" and r["entities"] == "1000"]
    assert int(found[0]["value"].split()[2]) > 500
