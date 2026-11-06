# darqos
# Copyright (C) 2026 David Arnold

# Tests for darq.message: message identifiers, times and the vocabulary.

import uuid

import darq.kb as model
import darq.message
from darq.message import message_id, time_value


def test_message_id_is_repeatable():
    assert message_id("slack:C1:1.000100", 1000100) == message_id("slack:C1:1.000100", 1000100)
    assert message_id("slack:C1:1.000100", 1000100) != message_id("slack:C2:1.000100", 1000100)


def test_message_id_is_a_valid_uuid():
    parsed = uuid.UUID(message_id("key", 1_700_000_000_123_456))
    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122


def test_message_id_holds_the_time():
    parsed = uuid.UUID(message_id("key", 1_700_000_000_123_456))
    assert parsed.int >> 80 == 1_700_000_000_123
    assert parsed.int >> 64 & 0xfff == 456


def test_message_id_sorts_by_time():
    # Within a millisecond too: a hash mustn't decide their order.
    times = [1, 2, 999, 1000, 1001, 1_423_298_927_000_003, 1_423_298_927_000_004, 1_423_298_927_001_000,
             1_700_000_000_123_456]
    ids = [message_id(f"key{n}", t) for n, t in reversed(list(enumerate(times)))]
    assert sorted(ids) == list(reversed(ids))


def test_time_value():
    value = time_value(1_700_000_000_123_456)
    assert (value.year, value.month, value.day, value.hour, value.minute, value.second) == (2023, 11, 14, 22, 13, 20)
    assert value.nanosecond == 123_456_000
    assert value.precision == model.TimePrecision.Nanoseconds


def test_vocabulary():
    properties = darq.message.vocabulary()
    assert all(p.get_label() for p in properties)
    assert len({p.id for p in properties}) == len(properties)
    ids = {p.id for p in properties}
    for name in ("ATTACHMENT", "BODY", "CONVERSATION", "FILE_NAME", "FILE_SIZE", "LAST_EDITED", "MEDIA_TYPE",
                 "MENTIONS", "PARTICIPANT", "PLATFORM", "REACTED_BY", "REACTION", "REPLY_TO", "SENDER", "TIME"):
        assert getattr(darq.message, name) in ids


def test_inverse_labels_are_of_item_properties():
    entities = {e.id: e for e in darq.message.vocabulary()}
    for property_id, item_id, label in darq.message.INVERSE_LABELS:
        linking = entities[property_id]
        assert isinstance(linking, model.Property) and linking.datatype == model.Datatype.Item
        assert entities[item_id].get_label() == label


def message_with(body: str, microsecond: int = 1_700_000_000_123_456) -> model.Item:
    item = model.Item("m")
    item.claim(darq.message.TIME, time_value(microsecond))
    item.claim(darq.message.SENDER, model.EntityValue("ada"))
    item.claim(darq.message.BODY, model.TextValue(body, darq.message.UNDETERMINED))
    return item


def test_summary():
    assert darq.message.summary(message_with("Hello there"), "Ada") == "Ada, 2023-11-14: Hello there"
    assert darq.message.summary(message_with("Hello"), "") == "2023-11-14: Hello"


def test_summary_has_no_markup():
    assert darq.message.summary(message_with("The **fairness** of `locks`, *mostly*"), "") \
        == "2023-11-14: The fairness of locks, mostly"
    assert darq.message.summary(message_with("```\nx = 1\n```"), "") == "2023-11-14: x = 1"
    assert darq.message.plain("2*3*4 is **24**") == "2*3*4 is 24"


def test_summary_is_the_first_line_with_anything_on_it():
    assert darq.message.summary(message_with("\n  \nSecond line\nThird"), "Ada") == "Ada, 2023-11-14: Second line"


def test_summary_of_a_long_line_is_cut():
    line = darq.message.summary(message_with("word " * 100), "").partition(": ")[2]
    assert len(line) <= darq.message.SUMMARY_LENGTH and line.endswith("…")


def test_summary_with_little_to_say():
    assert darq.message.summary(model.Item("m"), "") == ""
    assert darq.message.summary(model.Item("m"), "Ada") == "Ada"


def test_sender_and_body():
    item = message_with("Hello")
    assert darq.message.sender_of(item) == "ada" and darq.message.body_of(item) == "Hello"
    assert darq.message.sender_of(model.Item("m")) == "" and darq.message.body_of(model.Item("m")) == ""
