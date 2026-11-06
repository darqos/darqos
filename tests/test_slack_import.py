# darqos
# Copyright (C) 2026 David Arnold

# Tests for the Slack import, tools/slack-import: converting Slack's
# text; reading an export; importing one, with a KB service and KB type
# implementation in this process (see test_kb_type.py), and History
# faked; and then the whole system, through a real p-kernel, with an
# export made up for them.

import importlib
import importlib.util
import json
import pathlib
import sys

import pytest

import darq
import darq.kb as model
import darq.message

from test_kb_type import Writer, implementation, index, kb, kb_service, ports  # noqa: F401
from test_pkernel import live_pkernel, pkernel, run_kernel_until, write_app  # noqa: F401
from test_vcard_import import History, Implementation, find


TOOL_PATH = pathlib.Path(__file__).parent.parent / "tools" / "slack-import"


def load():
    # Its modules import each other, as they do run by pyloader, which
    # puts the executable's directory on the path.
    sys.path.insert(0, str(TOOL_PATH))
    try:
        modules = [importlib.import_module(name) for name in ("slack_export", "slack_importer", "slack_text")]
        spec = importlib.util.spec_from_file_location("slack_import", TOOL_PATH / "main.py")
        assert spec is not None and spec.loader is not None
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
    finally:
        sys.path.remove(str(TOOL_PATH))
    return (*modules, tool)


slack_export, slack_importer, slack_text, tool = load()

TEAM = "T1"


################################################################
# Slack's text
################################################################

def names(key):
    return {"U1": "Ada Lovelace"}.get(key, "")


@pytest.mark.parametrize("text, body", [
    ("plain", "plain"),
    ("*bold* words", "**bold** words"),
    ("_italic_ words", "*italic* words"),
    ("~struck~ words", "struck words"),
    ("2*3*4 and snake_case_name", "2*3*4 and snake_case_name"),
    ("a &lt; b &amp;&amp; c &gt; d", "a < b && c > d"),
    ("see <https://example.org/a_b_c>", "see https://example.org/a_b_c"),
    ("see <https://example.org|the site>", "see the site (https://example.org)"),
    ("<https://example.org|https://example.org>", "https://example.org"),
    ("<mailto:a@example.org|a@example.org>", "a@example.org"),
    ("in <#C1|general> today", "in #general today"),
    ("in <#C1> today", "in #C1 today"),
    ("<!here> look", "@here look"),
    ("<!subteam^S1|@team> look", "@team look"),
    ("<!date^1392734382^{date}|Feb 18> then", "Feb 18 then"),
    ("*bold <https://example.org/a_b|link>*", "**bold link (https://example.org/a_b)**"),
])
def test_convert(text, body):
    assert slack_text.convert(text, names)[0] == body


def test_convert_mentions():
    body, mentions = slack_text.convert("hi <@U1> and <@U2|bob> and <@U3>", names)
    assert body == "hi @Ada Lovelace and @bob and @U3"
    assert mentions == ["U1", "U2", "U3"]


def test_code_is_left_alone():
    assert slack_text.convert("use `*x*` and `a &lt; b`", names)[0] == "use `*x*` and `a < b`"
    assert slack_text.convert("```\n*x* _y_\n```\n*bold*", names)[0] == "```\n*x* _y_\n```\n**bold**"


def test_tokens_in_code_are_converted():
    assert slack_text.convert("run `curl <https://example.org/a_b>`", names)[0] == "run `curl https://example.org/a_b`"
    assert slack_text.convert("```\ncurl <https://example.org|https://example.org>\n*x*\n```", names)[0] \
        == "```\ncurl https://example.org\n*x*\n```"


def test_a_links_text_can_run_over_lines():
    assert slack_text.convert("<https://example.org|one\ntwo>", names)[0] == "one two (https://example.org)"


def test_a_bots_text_is_in_its_attachments():
    message = {"text": "", "attachments": [{"pretext": "Build", "title": "Passed", "text": "In 3m"},
                                           {"fallback": "Other"}]}
    assert slack_text.body_of(message, names)[0] == "Build\nPassed\nIn 3m\nOther"


def test_a_repeated_preview_is_said_once():
    message = {"text": "Hello", "attachments": [{"text": "Hello"}]}
    assert slack_text.body_of(message, names)[0] == "Hello"


################################################################
# The export
################################################################

USERS = [
    {"id": "U1", "team_id": TEAM, "real_name": "Ada Lovelace", "name": "ada", "profile": {}},
    {"id": "U2", "team_id": TEAM, "real_name": "Bob Babbage", "name": "bob", "profile": {}},
    {"id": "UB", "team_id": TEAM, "is_bot": True, "real_name": "Buildbot", "name": "buildbot", "profile": {}},
    {"id": "USLACKBOT", "team_id": TEAM, "real_name": "Slackbot", "name": "slackbot", "profile": {}},
]

CONVERSATIONS = [
    {"id": "C1", "name": "dev", "purpose": {"value": "Development"}, "topic": {"value": ""}},
    {"id": "D1", "is_im": True, "user": "U2", "purpose": {}, "topic": {}},
    {"id": "D2", "is_im": True, "user": "UB", "purpose": {}, "topic": {}},
    {"id": "D3", "is_im": True, "user": "USLACKBOT", "purpose": {}, "topic": {}},
]

FILES = [
    {"id": "F1", "name": "a.pdf", "mimetype": "application/pdf", "local_path": "files/F1/a.pdf", "size_on_disk": 4},
    {"id": "F2", "name": "b.mov", "mimetype": "video/quicktime", "local_path": "files/F2/b.mov", "size_on_disk": 4},
    {"id": "F3", "name": "c.gif", "mimetype": "image/gif", "url_private": "https://example.org"},
]

MESSAGES = {
    "C1": [
        {"ts": "1000.000100", "subtype": "channel_join", "user": "U1", "text": "joined"},
        {"ts": "1001.000100", "user": "U1", "text": "Hello <@U2>", "reactions": [
            {"name": "tada", "users": ["U2", "U1", "UNKNOWN"]}], "edited": {"ts": "1002.000000"}},
        {"ts": "1003.000100", "user": "U2", "text": "A question", "thread_replies": [
            {"ts": "1004.000100", "user": "U1", "text": "An answer"},
            {"ts": "1005.000100", "user": "U1", "text": "Broadcast", "subtype": "thread_broadcast"}]},
        {"ts": "1005.000100", "user": "U1", "text": "Broadcast", "subtype": "thread_broadcast"},
        {"ts": "1006.000100", "bot_id": "B1", "username": "GitLab", "text": "", "subtype": "bot_message",
         "attachments": [{"text": "Pushed"}]},
        {"ts": "1007.000100", "user": "UB", "bot_id": "B2", "text": "Built"},
        {"ts": "1008.000100", "user": "UGONE", "text": "From someone who's gone"},
        {"ts": "1009.000100", "user": "U1", "text": "Files", "files": [{"id": "F1"}, {"id": "F2"}, {"id": "F3"}]},
        {"ts": "1010.000100", "user": "U2", "text": "", "files": [{"id": "F1"}]},
    ],
    "D1": [{"ts": "2000.000100", "user": "U2", "text": "Private"}],
    "D2": [],
}


def make_export(path, messages=None):
    path.mkdir(exist_ok=True)
    (path / "messages").mkdir(exist_ok=True)
    (path / "users.json").write_text(json.dumps(USERS))
    (path / "conversations.json").write_text(json.dumps(CONVERSATIONS))
    (path / "files.json").write_text(json.dumps(FILES))
    for channel, said in (MESSAGES if messages is None else messages).items():
        (path / "messages" / f"{channel}.json").write_text(json.dumps(said))
    for record in FILES:
        if "local_path" in record:
            (path / record["local_path"]).parent.mkdir(parents=True, exist_ok=True)
            (path / record["local_path"]).write_bytes(b"data")
    return path


@pytest.fixture
def export(tmp_path):
    return slack_export.Export(make_export(tmp_path / "export"))


def test_team(export):
    assert export.team_id == TEAM


def test_system_messages_are_left_out(export):
    assert all(m.get("subtype") != "channel_join" for m in export.messages("C1"))


def test_replies_are_flat_and_in_order(export):
    said = export.messages("C1")
    assert [m["ts"] for m in said] == sorted(m["ts"] for m in said)
    assert next(m for m in said if m["text"] == "An answer")["thread_parent"] == "1003.000100"


def test_a_broadcast_reply_is_one_message(export):
    said = [m for m in export.messages("C1") if m["text"] == "Broadcast"]
    assert len(said) == 1 and said[0]["thread_parent"] == "1003.000100"


def test_a_conversation_with_no_file_is_empty(export):
    assert export.messages("D3") == []


def test_file_path(export):
    assert export.file_path(export.files["F1"]).name == "a.pdf"
    assert export.file_path(export.files["F3"]) is None
    assert export.file_path({"local_path": "../../etc/passwd"}) is None


def test_microseconds():
    assert slack_export.microseconds("1423298927.000003") == 1_423_298_927_000_003
    assert slack_export.microseconds("1.5") == 1_500_000
    assert slack_export.microseconds("7") == 7_000_000


################################################################
# Importing
################################################################

class Files:
    """Makes items of PDFs, and no others."""

    def __init__(self):
        self.made: list[str] = []
        self.types = {"application/pdf": "com.adobe.pdf"}

    def create(self, media_type, extension, data):
        type_id = self.types.get(media_type)
        if type_id is None:
            return None
        self.made.append(media_type)
        return f"{type_id}:{len(self.made):032x}"


@pytest.fixture
def target(kb, implementation, monkeypatch):  # noqa: F811
    adapter = Implementation(implementation)

    class ItemClient:
        def __init__(self, port, timeout=None):
            self.port = port

        def close(self):
            implementation.items[self.port].close(None, 0)

    monkeypatch.setattr(slack_importer, "EntityItem", type("EntityItem", (), {"Client": ItemClient}))
    return kb, adapter, History(), Files()


def run(target, export):
    client, adapter, history, files = target
    items = model.KB(client.client, Writer(adapter.handler))
    return slack_importer.Importer(client, items, adapter, history, files, export, "Example",
                                   lambda step, done, total: None).run()


def message_at(client, channel, ts):
    return client.get(darq.message.message_id(f"slack:{channel}:{ts}", slack_export.microseconds(ts)))


def values(entity, property_id):
    return [s.get_value() for s in entity.get_statements(property_id)]


def test_first_import(target, export):
    result = run(target, export)
    assert result.created == 10 and result.failed == 0
    assert result.conversations == 2 and result.empty == 2
    assert result.people == 2 and result.bots == 4
    assert (result.files, result.files_recorded) == (1, 2)


def test_statements_cite_the_workspace(target, export):
    run(target, export)
    client = target[0]
    message = message_at(client, "C1", "1001.000100")
    workspace = slack_importer.identifier(TEAM, "workspace", TEAM)
    assert all(model.is_sourced(s, workspace) for s in message.statements
               if s.get_property_id() not in (model.DARQOS_TYPE, model.INSTANCE_OF))


def test_a_message(target, export):
    run(target, export)
    client = target[0]
    message = message_at(client, "C1", "1001.000100")
    ada = find(client, "Ada Lovelace")[0]
    assert darq.message.body_of(message) == "Hello @Bob Babbage"
    assert darq.message.sender_of(message) == ada
    assert values(message, darq.message.TIME)[0].second == 41
    assert len(values(message, darq.message.LAST_EDITED)) == 1
    assert values(message, darq.message.MENTIONS)[0].entity_id == find(client, "Bob Babbage")[0]


def test_reactions_name_who_reacted(target, export):
    run(target, export)
    message = message_at(target[0], "C1", "1001.000100")
    [reaction] = message.get_statements(darq.message.REACTION)
    assert reaction.get_value().value == "tada"

    # Those Slack can't say who they are are left out.
    assert sum(q.property_id == darq.message.REACTED_BY for q in reaction.qualifiers) == 2


def test_replies_link_to_their_parents(target, export):
    run(target, export)
    client = target[0]
    parent = message_at(client, "C1", "1003.000100")
    for ts in ("1004.000100", "1005.000100"):
        assert values(message_at(client, "C1", ts), darq.message.REPLY_TO)[0].entity_id == parent.id


def test_people_are_people_and_bots_arent(target, export):
    run(target, export)
    client, _, _, _ = target
    assert model.recorded_type(client.get(find(client, "Ada Lovelace")[0])) == "darq.person"
    for name in ("Buildbot", "Slackbot", "GitLab"):
        assert model.recorded_type(client.get(find(client, name)[0])) == "darq.entity"


def test_senders(target, export):
    run(target, export)
    client = target[0]

    def sender(ts):
        return client.get(darq.message.sender_of(message_at(client, "C1", ts))).get_label()

    assert sender("1006.000100") == "GitLab"
    assert sender("1007.000100") == "Buildbot"
    assert sender("1008.000100") == "UGONE"


def test_conversations(target, export):
    run(target, export)
    client = target[0]
    channel = client.get(slack_importer.identifier(TEAM, "conversation", "C1"))
    assert channel.get_label() == "#dev" and channel.get_description() == "Development"
    assert model.recorded_type(channel) == "darq.conversation"
    assert len(values(channel, darq.message.PARTICIPANT)) == 5
    assert values(channel, darq.message.PLATFORM)[0].entity_id == slack_importer.SLACK


def test_a_direct_message_names_who_with(target, export):
    run(target, export)
    client = target[0]
    direct = client.get(slack_importer.identifier(TEAM, "conversation", "D1"))
    assert direct.get_label() == "Direct message with Bob Babbage"


def test_conversations_with_nothing_said_are_left_out(target, export):
    run(target, export)
    assert target[0].get(slack_importer.identifier(TEAM, "conversation", "D2")) is None


def test_attachments(target, export):
    run(target, export)
    message = message_at(target[0], "C1", "1009.000100")
    attachments = message.get_statements(darq.message.ATTACHMENT)
    assert [s.get_value().value[:14] for s in attachments] == ["com.adobe.pdf:", "", ""]

    def qualifier(statement, property_id):
        return next(q.value.value for q in statement.qualifiers if q.property_id == property_id)

    assert qualifier(attachments[1], darq.message.FILE_NAME) == "b.mov"
    assert qualifier(attachments[1], darq.message.MEDIA_TYPE) == "video/quicktime"
    assert qualifier(attachments[1], darq.message.FILE_SIZE) == "4"


def test_a_file_shared_twice_is_one_item(target, export):
    run(target, export)
    client, _, _, files = target
    assert files.made == ["application/pdf"]
    first = message_at(client, "C1", "1009.000100").get_statements(darq.message.ATTACHMENT)[0]
    second = message_at(client, "C1", "1010.000100").get_statements(darq.message.ATTACHMENT)[0]
    assert first.get_value().value == second.get_value().value


def test_a_message_of_only_a_file_has_no_body(target, export):
    run(target, export)
    assert darq.message.body_of(message_at(target[0], "C1", "1010.000100")) == ""


def test_history(target, export):
    run(target, export)
    _, _, history, _ = target
    assert sum(event == "created" for _, event, _ in history.events) == 2
    imported = [item for item, event, _ in history.events if event == "imported"]
    assert sum(item.startswith("darq.conversation:") for item in imported) == 2
    assert sum(item.startswith("com.adobe.pdf:") for item in imported) == 1
    assert all(source == "slack-import" for _, _, source in history.events)


def test_reimport_changes_nothing(target, export, kb_service):  # noqa: F811
    run(target, export)
    _, _, history, files = target
    events, writes = len(history.events), kb_service.store.writes
    result = run(target, export)
    assert (result.created, result.updated, result.failed, result.matches) == (0, 0, 0, [])
    assert result.unchanged == 10
    assert (len(history.events), kb_service.store.writes) == (events, writes)
    assert files.made == ["application/pdf"]


def test_reimport_picks_up_files_with_new_types(target, export):
    run(target, export)
    client, _, _, files = target
    files.types["video/quicktime"] = "public.mpeg-4"
    result = run(target, export)
    assert result.updated == 1 and result.files == 2
    attachments = message_at(client, "C1", "1009.000100").get_statements(darq.message.ATTACHMENT)
    assert len(attachments) == 3
    assert any(s.get_value().value.startswith("public.mpeg-4:") for s in attachments)


def test_someone_in_the_kb_is_found_by_name(target, export):
    client, adapter, _, _ = target
    response = adapter.create("darq.person")
    person = client.get(darq.split_item_id(response.item_id)[1])
    person.set_label("Ada Lovelace")
    client.put(person)

    result = run(target, export)
    assert result.matches == [("Ada Lovelace", response.item_id)]
    assert find(client, "Ada Lovelace") == [person.id]
    assert values(client.get(person.id), slack_importer.ACCOUNT)


def test_someone_found_before_is_found_by_their_identifier(target, export):
    client = target[0]
    run(target, export)
    ada = find(client, "Ada Lovelace")[0]
    renamed = client.get(ada)
    renamed.set_label("Ada King")
    client.put(renamed)
    result = run(target, export)
    assert result.matches == []
    assert find(client, "Ada Lovelace") == []


def test_a_batch_that_fails_is_counted_and_the_import_goes_on(target, export, monkeypatch):
    importer = slack_importer.Importer
    real = importer.message_statements

    def fail(self, channel, message, said):
        if channel == "D1":
            raise ValueError("no")
        return real(self, channel, message, said)

    monkeypatch.setattr(importer, "message_statements", fail)
    result = run(target, export)
    assert result.failed == 1 and result.created == 9


def test_nothing_of_a_message_is_logged(target, export, caplog):
    run(target, export)
    assert "Hello" not in caplog.text and "Private" not in caplog.text


################################################################
# The whole system
################################################################

def test_slack_import(live_pkernel, tmp_path):  # noqa: F811
    kernel = live_pkernel
    export_path = make_export(tmp_path / "export")

    client_path = write_app(tmp_path / "client", f"""
        import importlib.util
        import pathlib
        import sys
        import time
        import darq
        import darq.kb as model
        import darq.message
        from darq.protocols.type import Type

        TOOL = pathlib.Path({str(TOOL_PATH)!r})

        def darq_main():
            types = Type.Client(darq.connect("darq.type"), timeout=30)
            deadline = time.monotonic() + 30
            for type_id in ("darq.message", "com.adobe.pdf"):
                while not types.describe(type_id=type_id).implementation:
                    if time.monotonic() > deadline:
                        return f"{{type_id}} never registered"
                    time.sleep(0.05)

            spec = importlib.util.spec_from_file_location("slack_import", TOOL / "main.py")
            tool = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tool)

            result = tool.run_import({str(export_path)!r}, "Example", lambda step, done, total: None)
            if (result.created, result.failed, result.conversations) != (10, 0, 2):
                return f"imported {{result.lines()!r}}"
            again = tool.run_import({str(export_path)!r}, "Example", lambda step, done, total: None)
            if (again.created, again.updated, again.unchanged) != (0, 0, 10):
                return f"again {{again.lines()!r}}"
            return 0
        """)

    kernel.do_boot()
    client = kernel._process_table[kernel.do_spawn_process(None, client_path)]
    zombie = kernel.module.KernelProcessStatus.ZOMBIE
    run_kernel_until(kernel, lambda: client.get_status() == zombie, "the client to exit")
    assert (client.get_exit_status(), client.get_exit_error()) == (0, None)
