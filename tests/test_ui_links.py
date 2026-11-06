# darqos
# Copyright (C) 2026 David Arnold

# Tests for darq.ui.links: what links to an item, in groups.

from darq.protocols.kb import LinkGroup, Linked, Role
from darq.ui import links


def group(property_id="P170", role=Role.MAIN, type_id="darq.message", count=2, label="creator", inverse="created"):
    return LinkGroup(property_id, role, type_id, count, label, inverse)


def anchor(target, text):
    return f"[{target}|{text}]"


def row(n: int, label: str = "", summary: str = "") -> Linked:
    return Linked(f"m{n}", f"darq.message:m{n}", "", label, "", summary=summary)


def test_type_names():
    assert links.type_name("darq.message", 1) == "Message"
    assert links.type_name("darq.message", 2) == "Messages"
    assert links.type_name("darq.person", 2) == "People"
    assert links.type_name("public.email-message", 2) == "public.email-message"


def test_heading_reads_the_property_backwards():
    assert links.heading(group()) == "Created"


def test_heading_without_an_inverse_label():
    assert links.heading(group(label="employer", inverse="")) == "With this as employer"
    assert links.heading(group(label="", inverse="")) == "With this as P170"


def test_an_inverse_label_is_for_a_statements_value():
    assert links.heading(group(role=Role.REFERENCE, label="stated in", inverse="source of")) \
        == "With this as stated in (in a reference)"
    assert links.heading(group(role=Role.QUALIFIER, inverse="")) == "With this as creator (in a qualifier)"


def test_sections_gather_a_propertys_types_largest_first():
    found = links.sections([group(type_id="darq.message", count=5), group(type_id="darq.group", count=500)])
    assert [s.heading for s in found] == ["Created"]
    assert [g.type_id for g in found[0].groups] == ["darq.group", "darq.message"]
    assert found[0].count == 505


def test_sections_order():
    found = links.sections([
        group("P1", Role.REFERENCE, count=1000, label="stated in", inverse=""),
        group("P2", count=1, label="small", inverse=""),
        group("P3", count=50, label="large", inverse="")])
    assert [s.heading for s in found] == ["With this as large", "With this as small",
                                          "With this as stated in (in a reference)"]


def test_is_inline():
    assert links.is_inline(group(count=links.INLINE_MAX))
    assert not links.is_inline(group(count=links.INLINE_MAX + 1))


def test_row_text_prefers_a_label():
    assert links.row_text(row(1, "Label", "Summary")) == "Label"
    assert links.row_text(row(1, "", "Summary")) == "Summary"
    assert links.row_text(row(1)) == "darq.message:m1"


def test_render_a_large_group_is_its_count():
    text = links.render_group(group(count=34277), [], anchor)
    assert text == f"[{links.OpenGroup(links.key_of(group()))}|34,277 Messages]"


def test_render_a_small_group_is_its_entities():
    text = links.render_group(group(count=2), [row(1, "One"), row(2, "Two")], anchor)
    assert "|One]" in text and "|Two]" in text and "(Messages)" in text


def test_render_sections_escapes_headings():
    text = links.render_sections(links.sections([group(inverse="<b>made")]), {}, anchor)
    assert "<h3>&lt;b&gt;made</h3>" in text


def test_view_positions():
    view = links.View(("P", Role.MAIN, "t"))
    assert (view.after, view.first) == ("", 0)
    view.cursors.append("m100")
    assert (view.after, view.first) == ("m100", links.PAGE_ROWS)
    copy = view.copy()
    copy.cursors.pop()
    assert view.after == "m100"


def test_render_view_turns():
    view = links.View(links.key_of(group()))
    text = links.render_view(group(count=205), view, [row(1, "One")], True, anchor)
    assert "205 Messages" in text and "1 to 1, newest first" in text
    assert "Older" in text and "Newer" not in text

    view.cursors.append("m1")
    text = links.render_view(group(count=205), view, [row(2, "Two")], False, anchor)
    assert "101 to 101, newest first" in text
    assert "Newer" in text and "Older" not in text


def test_render_view_can_be_turned_from_either_end():
    text = links.render_view(group(count=205), links.View(links.key_of(group())), [row(1, "One")], True, anchor)
    assert text.count("Older") == 2
    assert text.index("Older") < text.index("|One]") < text.rindex("Older")


def test_render_view_when_theyve_gone():
    text = links.render_view(group(), links.View(links.key_of(group())), [], False, anchor)
    assert "None, now." in text
