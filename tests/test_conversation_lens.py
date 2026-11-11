# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Conversation lens, lenses/conversation: its timeline and
# rendering, without Qt; its window, offscreen, against fake items; and
# then the whole system, through a real p-kernel (see test_pkernel.py for
# the fixtures).

import importlib.util
import pathlib
import sys

import pytest

import darq
import darq.kb as model
import darq.message
from darq.errors import DeadPortError
from darq.message import time_value
from darq.protocols import kb as wire
from darq.protocols.kb import GetEntityResponse, GetLinkedEntitiesResponse, Linked
from darq.protocols.lens import Status, UseRequest

from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401


LENS_PATH = pathlib.Path(__file__).parent.parent / "lenses" / "conversation"
CONVERSATION = "darq.conversation:c"
START = 1_700_000_000_000_000
MINUTE = 60_000_000


def load():
    sys.path.insert(0, str(LENS_PATH))
    try:
        spec = importlib.util.spec_from_file_location("conversation_lens", LENS_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(LENS_PATH))
    return module


lens = load()
render = lens.message_page
timeline = lens.timeline


def make_message(number: int, sender: str = "ada", when: int | None = None, body: str | None = None) -> model.Item:
    """Make a message, a minute after the one before."""
    item = model.Item(f"m{number:05d}")
    item.claim(darq.message.TIME, time_value(START + number * MINUTE if when is None else when))
    item.claim(darq.message.SENDER, model.EntityValue(sender))
    item.claim(darq.message.CONVERSATION, model.EntityValue("c"))
    item.claim(darq.message.BODY, model.TextValue(f"Message {number}" if body is None else body, "und"))
    return item


class World:
    """A conversation, as an EntityItem implementation would have it."""

    def __init__(self, count: int = 0):
        self.messages = [make_message(n) for n in range(count)]
        self.conversation = model.Item("c")
        self.conversation.set_label("#dev")
        self.conversation.claim(darq.message.PARTICIPANT, model.EntityValue("ada"))
        self.linked = {
            "ada": Linked("ada", "darq.person:ada", "en", "Ada Lovelace"),
            "c": Linked("c", CONVERSATION, "en", "#dev"),
        }
        self.reads = 0
        self.closed: list[str] = []
        self.viewed: list[tuple[str, str]] = []
        self.ports: dict[int, str] = {}
        self.next_port = 700
        self.failing = False
        self.unopenable: set[str] = set()

    def open(self, item_id):
        if item_id in self.unopenable:
            raise darq.LaunchError(f"Can't open {item_id}: NOT_FOUND")
        self.next_port += 1
        self.ports[self.next_port] = item_id
        return self.next_port

    def entity(self, item_id):
        if item_id == CONVERSATION:
            return self.conversation
        entity_id = darq.split_item_id(item_id)[1]
        return next(m for m in self.messages if m.id == entity_id)


class FakeItem:
    """Stands in for an EntityItem.Client."""

    def __init__(self, world: World, port: int):
        self.world = world
        self.item_id = world.ports[port]

    def get_entity(self, languages):
        entity = self.world.entity(self.item_id)
        linked = [self.world.linked[i] for i in sorted(model.referenced(entity)) if i in self.world.linked]
        return GetEntityResponse(entity=model.entity_to_wire(entity), linked=linked, status=wire.Status.OK)

    def get_linked_entities(self, property_id, role, type_id, languages, max, after, reverse):
        if self.world.failing:
            return GetLinkedEntitiesResponse(status=wire.Status.NOT_FOUND)
        assert property_id == darq.message.CONVERSATION and type_id == "darq.message"
        self.world.reads += 1
        found = sorted(self.world.messages, key=lambda m: m.id, reverse=reverse)
        if after:
            found = [m for m in found if (m.id < after if reverse else m.id > after)]
        return GetLinkedEntitiesResponse(entities=[model.entity_to_wire(m) for m in found[:max]],
                                         linked=list(self.world.linked.values()), status=wire.Status.OK)

    def close(self):
        self.world.closed.append(self.item_id)

    def viewed(self, source="", note=""):
        self.world.viewed.append((self.item_id, source))


class FakeOpener:
    def __init__(self, world):
        self.world = world
        self.used: list[str] = []

    def open(self, item_id):
        return self.world.open(item_id)

    def use(self, item_id):
        self.used.append(item_id)


class FakeLoop:
    def __init__(self):
        self.stopped = False

    def post(self, function):
        function()

    def stop(self):
        self.stopped = True


def held(world: World) -> timeline.Timeline:
    return timeline.Timeline(FakeItem(world, world.open(CONVERSATION)), ["en"])


def numbers(messages: timeline.Timeline) -> list[int]:
    return [int(m.id[1:]) for m in messages.messages]


################################################################
# The timeline
################################################################

def test_newest():
    messages = held(World(250))
    messages.load_newest()
    assert numbers(messages) == list(range(150, 250))
    assert messages.more_older and not messages.more_newer


def test_oldest():
    messages = held(World(250))
    messages.load_oldest()
    assert numbers(messages) == list(range(100))
    assert messages.more_newer and not messages.more_older


def test_fewer_than_a_page():
    messages = held(World(3))
    messages.load_newest()
    assert numbers(messages) == [0, 1, 2]
    assert not messages.more_older and not messages.more_newer


def test_exactly_a_page_has_no_more():
    messages = held(World(timeline.PAGE))
    messages.load_newest()
    assert not messages.more_older


def test_empty():
    messages = held(World(0))
    messages.load_newest()
    assert messages.messages == []
    assert not messages.load_older() and not messages.load_newer()


def test_around_a_message():
    world = World(500)
    messages = held(world)
    messages.load_around(world.messages[200])
    assert numbers(messages) == list(range(150, 251))
    assert messages.more_older and messages.more_newer


def test_around_the_ends():
    world = World(20)
    messages = held(world)
    messages.load_around(world.messages[0])
    assert numbers(messages) == list(range(20))
    assert not messages.more_older and not messages.more_newer


def test_reading_on_in_both_directions():
    messages = held(World(250))
    messages.load_newest()
    assert messages.load_older()
    assert numbers(messages) == list(range(50, 250))
    assert messages.load_older()
    assert numbers(messages) == list(range(250))
    assert not messages.more_older and not messages.load_older()


def test_only_so_many_are_held():
    world = World(1000)
    messages = held(world)
    messages.load_newest()
    while len(messages.messages) < timeline.MAX_HELD:
        messages.load_older()
    assert not messages.more_newer

    # Reading on drops the far end, which can be read again.
    messages.load_older()
    assert len(messages.messages) == timeline.MAX_HELD
    assert messages.more_newer
    last = numbers(messages)[-1]
    assert messages.load_newer()
    assert len(messages.messages) == timeline.MAX_HELD
    assert numbers(messages)[-1] == last + timeline.PAGE
    assert numbers(messages) == sorted(numbers(messages))


def test_failing_raises():
    world = World(3)
    world.failing = True
    with pytest.raises(timeline.TimelineError):
        held(world).load_newest()


def test_holds():
    messages = held(World(3))
    messages.load_newest()
    assert messages.holds("m00001") and not messages.holds("m00009")


def test_alone():
    messages = timeline.Timeline(None, ["en"])
    assert messages.read("", True, 10) == []


################################################################
# Rendering
################################################################

@pytest.mark.parametrize("text, expected", [
    ("plain", "<p>plain</p>"),
    ("<script>", "<p>&lt;script&gt;</p>"),
    ("one\ntwo", "<p>one<br>two</p>"),
    ("some **bold** text", "<p>some <b>bold</b> text</p>"),
    ("some *italic* text", "<p>some <i>italic</i> text</p>"),
    ("2*3*4", "<p>2*3*4</p>"),
    ("use `a<b` here", "<p>use <code>a&lt;b</code> here</p>"),
    ("no `**bold**` in code", "<p>no <code>**bold**</code> in code</p>"),
])
def test_body(text, expected):
    assert render.body_html(text) == expected


def test_body_code_block():
    html = render.body_html("before\n```\nx = <1>\n**y**\n```\nafter")
    assert "<pre>x = &lt;1&gt;\n**y**</pre>" in html
    assert "<p>before</p>" in html and "<p>after</p>" in html


def test_body_empty():
    assert render.body_html("") == ""


@pytest.mark.parametrize("value, expected", [("512", "512 bytes"), ("1500", "1.5 KB"), ("2500000", "2.5 MB"),
                                             ("3000000000", "3.0 GB"), ("many", "many")])
def test_size_text(value, expected):
    assert render.size_text(value) == expected


def page_for(*messages, **options):
    page = render.Page({"ada": Linked("ada", "darq.person:ada", "en", "Ada <L>"),
                        "abc": Linked("abc", "com.adobe.pdf:abc", "en", "Report")}, **options)
    return page, page.render(list(messages), False, False)


def test_render_a_message():
    page, html = page_for(make_message(1, body="Hello"))
    assert "Ada &lt;L&gt;</a>" in html
    assert "<p>Hello</p>" in html
    assert 'name="m-m00001"' in html
    assert page.targets == [render.Open("darq.person:ada")]


def test_render_a_senders_following_messages_together():
    _, html = page_for(make_message(1), make_message(2), make_message(3, when=START + 3 * MINUTE + 3_600_000_000))
    assert html.count('class="sender"') == 2


def test_render_another_sender_starts_afresh():
    _, html = page_for(make_message(1), make_message(2, "bob"))
    assert html.count('class="sender"') == 2
    assert "bob" in html


def test_render_days():
    day = 24 * 60 * MINUTE
    _, html = page_for(make_message(1), make_message(2, when=START + day))
    assert html.count("<h3>") == 2


def test_render_an_unknown_sender():
    item = model.Item("m")
    _, html = page_for(item)
    assert "Unknown" in html


def test_render_edited():
    message = make_message(1)
    message.claim(darq.message.LAST_EDITED, time_value(START))
    assert "(edited)" in page_for(message)[1]


def test_render_a_reply_quotes_its_parent():
    message = make_message(2)
    message.claim(darq.message.REPLY_TO, model.EntityValue("m00001"))
    page = render.Page({"m00001": Linked("m00001", "darq.message:m00001", "", "", summary="Ada: the question")})
    html = page.render([message], False, False)
    assert "the question" in html and render.Jump("m00001") in page.targets


def test_render_a_reply_to_something_unknown():
    message = make_message(2)
    message.claim(darq.message.REPLY_TO, model.EntityValue("gone"))
    assert "A message" in page_for(message)[1]


def attached(item_id, name="report.pdf", media_type="application/pdf", size="1500", linked=True):
    """A message with an attachment: linked to its item, or as it was,
    by its identifier, or empty, for a file that isn't an item."""
    message = make_message(1)
    if linked and item_id:
        statement = message.claim(darq.message.ATTACHMENT, model.EntityValue(darq.split_item_id(item_id)[1]))
    else:
        statement = message.claim(darq.message.OLD_ATTACHMENT, model.StringValue(item_id))
    statement.add_qualifier(darq.message.FILE_NAME, model.StringValue(name))
    statement.add_qualifier(darq.message.MEDIA_TYPE, model.StringValue(media_type))
    statement.add_qualifier(darq.message.FILE_SIZE, model.StringValue(size))
    return message


@pytest.mark.parametrize("linked", [True, False])
def test_render_an_attachment_opens_its_item(linked):
    page, html = page_for(attached("com.adobe.pdf:abc", linked=linked))
    assert "report.pdf</a>" in html and "application/pdf, 1.5 KB" in html
    assert render.Open("com.adobe.pdf:abc") in page.targets
    assert "not imported" not in html


def test_render_a_file_that_isnt_an_item():
    page, html = page_for(attached(""))
    assert "report.pdf" in html and "not imported" in html
    assert page.targets == [render.Open("darq.person:ada")]


def test_render_reactions():
    message = make_message(1)
    one = message.claim(darq.message.REACTION, model.StringValue("thumbsup"))
    one.add_qualifier(darq.message.REACTED_BY, model.EntityValue("ada"))
    two = message.claim(darq.message.REACTION, model.StringValue("tada"))
    two.add_qualifier(darq.message.REACTED_BY, model.EntityValue("ada"))
    two.add_qualifier(darq.message.REACTED_BY, model.EntityValue("bob"))
    html = page_for(message)[1]
    assert ":thumbsup:" in html and ":tada: 2" in html


def test_render_highlight():
    assert 'bgcolor=' in page_for(make_message(1), highlight="m00001")[1]
    assert 'bgcolor=' not in page_for(make_message(1))[1]


def test_render_empty():
    assert "No messages." in page_for()[1]


def test_conversation_of():
    assert lens.conversation_of(make_message(1)) == "c"
    assert lens.conversation_of(model.Item("m")) == ""


def test_languages(monkeypatch):
    monkeypatch.setenv("LANG", "en_AU.UTF-8")
    assert lens.languages() == ["en-AU", "en"]
    monkeypatch.setenv("LANG", "fr_FR")
    assert lens.languages() == ["fr-FR", "fr", "en"]
    monkeypatch.delenv("LANG")
    assert lens.languages() == ["en"]


################################################################
# The window, offscreen
################################################################

@pytest.fixture
def world():
    return World(250)


@pytest.fixture
def conversation_lens(qt_app, world, monkeypatch):
    monkeypatch.setattr(darq, "close_port", lambda port: None)
    used = lens.ConversationLens(FakeLoop(), FakeOpener(world))
    monkeypatch.setattr(used, "client", lambda port: used.ports.setdefault(port, FakeItem(world, port)))
    yield used
    if used.window is not None:
        used.window.close()


def use(conversation_lens, world, item_id=CONVERSATION):
    return conversation_lens.use(UseRequest(item_id=item_id, item=world.open(item_id)), 0)


def text_of(window):
    return window.browser.toPlainText()


def test_conversation_opens_at_the_newest(conversation_lens, world):
    assert use(conversation_lens, world).status == Status.OK
    window = conversation_lens.window
    assert window.title.text() == "#dev" and window.people.text() == "Ada Lovelace"
    assert "Message 249" in text_of(window)
    assert window.bar.value() == window.bar.maximum()
    # It's told it's viewed: its type records it.
    assert world.viewed == [(CONVERSATION, "conversation-lens")]


def test_empty_conversation(conversation_lens, world):
    world.messages = []
    assert use(conversation_lens, world).status == Status.OK
    assert "No messages." in text_of(conversation_lens.window)


def test_message_opens_in_its_conversation(conversation_lens, world):
    item_id = "darq.message:m00100"
    assert use(conversation_lens, world, item_id).status == Status.OK
    window = conversation_lens.window
    assert window.title.text() == "#dev"
    assert window.highlight == "m00100"
    assert "Message 100" in text_of(window) and "Message 50 " in text_of(window)

    # The message's port has gone, and the conversation's is what's left.
    assert world.closed == [item_id]
    # The message is told it's viewed, and then the conversation it's in.
    assert world.viewed == [(item_id, "conversation-lens"), (CONVERSATION, "conversation-lens")]


def test_message_without_a_conversation_is_shown_alone(conversation_lens, world):
    alone = model.Item("m09999")
    alone.claim(darq.message.BODY, model.TextValue("By itself", "und"))
    world.messages.append(alone)
    assert use(conversation_lens, world, "darq.message:m09999").status == Status.OK
    window = conversation_lens.window
    assert "By itself" in text_of(window) and window.title.text() == "Message"


def test_message_whose_conversation_cant_be_opened_is_shown_alone(conversation_lens, world):
    world.unopenable.add(CONVERSATION)
    assert use(conversation_lens, world, "darq.message:m00100").status == Status.OK
    assert "Message 100" in text_of(conversation_lens.window)
    assert world.closed == []


def test_scrolling_up_reads_earlier_messages(conversation_lens, world, qt_app):
    use(conversation_lens, world)
    window = conversation_lens.window
    reads = world.reads
    assert "Message 149" not in text_of(window)
    window.bar.setValue(0)
    assert world.reads == reads + 1
    assert "Message 149" in text_of(window)

    # It stays at the message it was at, not at the top.
    assert window.bar.value() > lens.NEAR_END


def test_scrolling_down_reads_later_messages(conversation_lens, world):
    use(conversation_lens, world)
    window = conversation_lens.window
    window.oldest()
    assert "Message 100" not in text_of(window)
    window.bar.setValue(window.bar.maximum())
    assert "Message 100" in text_of(window)


def test_home_and_end(conversation_lens, world):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent

    use(conversation_lens, world)
    window = conversation_lens.window

    def press(key):
        window.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, ""))

    press(Qt.Key.Key_Home)
    assert "Message 0" in text_of(window) and "Message 249" not in text_of(window)
    press(Qt.Key.Key_End)
    assert "Message 249" in text_of(window)
    assert window.bar.value() == window.bar.maximum()


def test_f5_reads_the_newest(conversation_lens, world):
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent

    use(conversation_lens, world)
    world.messages.append(make_message(250))
    conversation_lens.window.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_F5,
                                                     Qt.KeyboardModifier.NoModifier, ""))
    assert "Message 250" in text_of(conversation_lens.window)


def test_following_a_sender_opens_their_own_lens(conversation_lens, world):
    use(conversation_lens, world)
    window = conversation_lens.window
    window.choose(render.Open("darq.person:ada"))
    assert conversation_lens.opener.used == ["darq.person:ada"]
    assert window.title.text() == "#dev"


def test_jumping_to_a_message_held(conversation_lens, world):
    use(conversation_lens, world)
    window = conversation_lens.window
    reads = world.reads
    window.choose(render.Jump("m00200"))
    assert window.highlight == "m00200" and world.reads == reads


def test_jumping_to_a_message_not_held(conversation_lens, world):
    use(conversation_lens, world)
    window = conversation_lens.window
    window.choose(render.Jump("m00020"))
    assert window.highlight == "m00020"
    assert "Message 20\n" in text_of(window) and "Message 249" not in text_of(window)


def test_jumping_to_a_message_thats_gone(conversation_lens, world):
    use(conversation_lens, world)
    window = conversation_lens.window
    world.unopenable.add("darq.message:m00020")
    window.choose(render.Jump("m00020"))
    assert window.status.text() == "That message can't be read"


def test_read_failure_is_shown(conversation_lens, world):
    use(conversation_lens, world)
    window = conversation_lens.window
    world.failing = True
    window.bar.setValue(0)
    assert "Can't read messages" in window.status.text()
    assert "Message 249" in text_of(window)


def test_anchor_targets(conversation_lens, world):
    use(conversation_lens, world)
    window = conversation_lens.window
    assert window.target("#0") == render.Open("darq.person:ada")
    assert window.target("#9999") is None and window.target("http://x") is None


def test_lens_refuses_a_second_item(conversation_lens, world):
    use(conversation_lens, world)
    assert use(conversation_lens, world).status == Status.BUSY


def test_lens_fails_if_the_item_has_gone(conversation_lens, world, monkeypatch):
    def gone(languages):
        raise DeadPortError("gone")

    monkeypatch.setattr(FakeItem, "get_entity", lambda self, languages: gone(languages))
    assert use(conversation_lens, world).status == Status.FAILED
    assert conversation_lens.window is None


def test_closing_closes_the_item_and_exits(conversation_lens, world):
    use(conversation_lens, world)
    conversation_lens.window.close()
    assert world.closed == [CONVERSATION]
    assert conversation_lens.loop.stopped


################################################################
# The whole system
################################################################

def test_conversation_lens(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel

    # Plays the Terminal: creates a conversation with a message, and has
    # the lens use the message.
    client_path = write_app(tmp_path / "client", """
        import time
        import darq
        import darq.kb as model
        import darq.launch
        import darq.message
        from darq.protocols import kb as wire
        from darq.protocols.history import History
        from darq.protocols.lens import Lens
        from darq.protocols.type import Status, Type

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            while not any(lens.endswith("/conversation")
                          for lens in types.find_lenses(type_id="darq.message", context="terminal").lenses):
                if time.monotonic() > deadline:
                    return "no lens registered"
                time.sleep(0.05)
            for type_id in ("darq.message", "darq.conversation"):
                path = darq.launch.find_lens(type_id)
                if not path.endswith("lenses/conversation"):
                    return f"lens for {type_id} is {path}"

            conversation_id, port = darq.launch.create_item("darq.conversation")
            darq.close_port(port)
            conversation = darq.split_item_id(conversation_id)[1]

            activated = types.activate(type_id="darq.message")
            kb = model.KB.connect(30, wire.EntityWriter.Client(activated.implementation, timeout=30))
            when = 1_700_000_000_123_456
            new = model.Item(darq.message.message_id("test:1", when))
            new.claim(darq.message.BODY, model.TextValue("Hello", darq.message.UNDETERMINED))
            new.claim(darq.message.TIME, darq.message.time_value(when))
            new.claim(darq.message.CONVERSATION, model.EntityValue(conversation))
            kb.create("darq.message", new)

            item_id = f"darq.message:{new.id}"
            lens = Lens.Client(darq.launch.use_item(item_id), timeout=30)
            # Its type records it viewed, as it comes, one way.
            history = History.Client(darq.connect("darq.history"), timeout=10)
            deadline = time.monotonic() + 10
            while True:
                events = [(e.event, e.source) for e in history.get_events(item_ids=[item_id]).events]
                if ("viewed", "conversation-lens") in events:
                    break
                if time.monotonic() > deadline:
                    return f"events are {events!r}"
                time.sleep(0.05)
            lens.close()
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)

    [lens_process] = [p for p in kernel._process_table.values() if p._image_path == str(LENS_PATH)]
    run_kernel_until(kernel, lambda: lens_process.get_status() == zombie, "the lens to exit")
    assert (lens_process.get_exit_status(), lens_process.get_exit_error()) == (0, None)
