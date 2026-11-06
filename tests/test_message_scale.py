# darqos
# Copyright (C) 2026 David Arnold

# A small run of the message measurement, bench/message_scale.py, on a
# made-up export, so it keeps working as the store changes, and tests of
# the identifiers and threads it designs.

import csv
import importlib.util
import json
import pathlib
import sys
import typing
import uuid

import pytest

BENCH = pathlib.Path(__file__).parent.parent / "bench"


def load():
    sys.path.insert(0, str(BENCH))
    try:
        spec = importlib.util.spec_from_file_location("message_scale", BENCH / "message_scale.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(BENCH))
    return module


bench = load()


def test_message_id_is_repeatable():
    assert bench.message_id("C1", "1700000000.000100") == bench.message_id("C1", "1700000000.000100")
    assert bench.message_id("C1", "1700000000.000100") != bench.message_id("C2", "1700000000.000100")


def test_message_id_is_a_uuid_of_version_7():
    assert uuid.UUID(bench.message_id("C1", "1700000000.123456")).version == 7


def test_message_id_sorts_by_time():
    # Slack's sequence numbers within a millisecond, as well as times.
    times = ["1423298927.000003", "1423298927.000004", "1423298927.000200", "1423298927.000999",
             "1423298927.001000", "1423298928.000001", "1700000000.123456"]
    ids = [bench.message_id(f"C{n}", ts) for n, ts in enumerate(times)]
    assert ids == sorted(ids)


def test_read_conversation(tmp_path):
    path = tmp_path / "C1.json"
    path.write_text(json.dumps([
        {"ts": "1.000100", "text": "joined", "subtype": "channel_join"},
        {"ts": "2.000100", "text": "parent", "thread_replies": [
            {"ts": "3.000100", "text": "reply"},
            {"ts": "4.000100", "text": "joined", "subtype": "channel_join"},
            {"ts": "5.000100", "text": "broadcast", "subtype": "thread_broadcast"}]},
        {"ts": "5.000100", "text": "broadcast", "subtype": "thread_broadcast"},
    ]))
    messages = list(bench.read_conversation(path))

    # System messages go, and a reply also in the channel is one message.
    assert [m["text"] for m in messages] == ["parent", "reply", "broadcast"]
    assert [m.get("thread_parent") for m in messages] == [None, "2.000100", "2.000100"]


def test_body_includes_attachments():
    assert bench.body_of({"text": "", "attachments": [{"text": "from a bot"}, {"fallback": "other"}]}) \
        == "from a bot\nother"


@pytest.mark.parametrize("shape", bench.SHAPES)
def test_small_run(tmp_path, shape):
    export = tmp_path / "export"
    (export / "messages").mkdir(parents=True)
    messages: list[dict[str, typing.Any]] = [
        {"ts": f"{1700000000 + n}.000100", "user": f"U{n % 3}", "text": f"message {n}"} for n in range(300)]
    messages[0]["reactions"] = [{"name": "thumbsup", "users": ["U1"]}]
    messages[1]["files"] = [{"id": "F1", "name": "a.pdf", "mimetype": "application/pdf"}]
    messages[2]["edited"] = {"ts": "1700000100.000000"}
    messages[3]["thread_replies"] = [{"ts": "1700001000.000100", "user": "U1", "text": "reply"}]
    messages[4]["blocks"] = [{"type": "rich_text", "elements": [{"type": "user", "user_id": "U2"}]}]
    (export / "messages" / "C1.json").write_text(json.dumps(messages))

    results = tmp_path / "results"
    assert bench.main(["--export", str(export), "--dir", str(results), "--shapes", shape]) == 0
    with open(results / "message-scale.csv", newline="") as f:
        rows = {row["metric"]: row for row in csv.DictReader(f)}
    assert rows["messages"]["value"] == "301"
    assert rows["ids in time order"]["value"] == "True"
    assert int(rows["bytes a message"]["value"]) > 0
