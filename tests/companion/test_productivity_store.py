import sqlite3
import threading

import pytest

from companion.productivity import ProductivityStore, StoreError
from companion.productivity.store import light_cleanup, suggest_title


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        self.t += 1
        return self.t


@pytest.fixture
def st(tmp_path):
    s = ProductivityStore(tmp_path / "p.db", Clock())
    yield s
    s.close()


def test_a_note_keeps_her_exact_words_and_gets_a_suggested_title(st):
    n = st.add_note("I want to try making a little robot enclosure with removable magnetic panels. Maybe two colours.", kind="dictated", source="voice")
    assert n["body"].startswith("I want to try making") and n["title"] == "I want to try making a"
    assert n["kind"] == "dictated" and n["cleaned"] is False and n["audio"] is False and n["created_at"]


def test_edit_append_rename_archive_delete(st):
    n = st.add_note("first line", title="Plan")
    st.update_note(n["id"], append="second line")
    assert st.get_note(n["id"])["body"] == "first line\nsecond line"
    st.update_note(n["id"], title="New name", tags=["#Robot", "robot", "Ideas"])
    assert st.get_note(n["id"])["title"] == "New name" and st.get_note(n["id"])["tags"] == ["robot", "ideas"]
    st.update_note(n["id"], archived=True)
    assert st.notes() == [] and len(st.notes(archived=True)) == 1
    assert st.delete_note(n["id"]) and st.get_note(n["id"]) is None and not st.delete_note(n["id"])


def test_empty_or_oversized_notes_are_refused_not_half_saved(st):
    for bad in ("", "   "):
        with pytest.raises(StoreError):
            st.add_note(bad)
    with pytest.raises(StoreError):
        st.add_note("x" * 100_001)
    n = st.add_note("keep me")
    with pytest.raises(StoreError):
        st.update_note(n["id"], body="  ")
    assert st.get_note(n["id"])["body"] == "keep me" and len(st.notes()) == 1


def test_search_by_words_kind_and_date(st):
    c = st.clock
    a = st.add_note("robot enclosure with magnetic panels", title="Enclosure idea", kind="dictated")
    c.t += 86400 * 3
    b = st.add_note("buy flour and eggs for the cake", kind="written")
    assert [n["id"] for n in st.notes("robot panels")] == [a["id"]]
    assert [n["id"] for n in st.notes("cake")] == [b["id"]]
    assert st.notes("quantum") == []
    assert [n["id"] for n in st.notes(kind="written")] == [b["id"]]
    assert [n["id"] for n in st.notes(since=b["created_at"] - 10)] == [b["id"]]
    assert [n["id"] for n in st.notes(until=a["created_at"] + 10)] == [a["id"]]
    assert st.last_note()["id"] == b["id"]


def test_lists_items_complete_restore_reorder(st):
    l = st.create_list("Grocery", ["eggs", "rice", "milk", "fruit"])
    assert l["total"] == 4 and l["done"] == 0
    eggs = l["items"][0]
    st.update_item(eggs["id"], done=True)
    g = st.get_list(l["id"])
    assert g["done"] == 1 and g["items"][0]["completed_at"]
    st.update_item(eggs["id"], done=False)                                    # restored
    assert st.get_list(l["id"])["items"][0]["completed_at"] is None
    st.move_item(g["items"][2]["id"], -1)
    assert [i["text"] for i in st.get_list(l["id"])["items"]] == ["eggs", "milk", "rice", "fruit"]
    st.rename_list(l["id"], "Groceries")
    assert st.get_list(l["id"])["name"] == "Groceries"
    with pytest.raises(StoreError):
        st.create_list("groceries")                                           # same name, different case: refused
    assert st.delete_item(eggs["id"]) and st.get_list(l["id"])["total"] == 3


def test_finding_lists_and_items_by_her_words(st):
    st.create_list("Grocery list", ["almond milk", "eggs"])
    st.create_list("Robot project", ["order batteries", "print enclosure"])
    assert [l["name"] for l in st.find_list("grocery")] == ["Grocery list"]
    assert [l["name"] for l in st.find_list("robot project")] == ["Robot project"]
    assert st.find_list("garden") == []
    assert [i["text"] for i in st.find_items("batteries")] == ["order batteries"]
    assert st.find_items("milk", only_open=True)[0]["list_name"] == "Grocery list"


def test_deleting_a_list_deletes_its_items(st):
    l = st.create_list("Temp", ["a", "b"])
    st.delete_list(l["id"])
    assert st.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_encryption_at_rest_wrong_passphrase_and_integrity(tmp_path):
    c = Clock()
    s = ProductivityStore(tmp_path / "e.db", c, passphrase="correct horse battery")
    n = s.add_note("my private plan about the surprise", tags=["secret"])
    l = s.create_list("Gift ideas", ["a blue scarf"])
    s.close()
    raw = (tmp_path / "e.db").read_bytes()
    assert b"private plan" not in raw and b"blue scarf" not in raw and b"Gift ideas" not in raw and b"secret" not in raw
    with pytest.raises(StoreError):
        ProductivityStore(tmp_path / "e.db", c, passphrase="wrong wrong wrong")
    with pytest.raises(StoreError):
        ProductivityStore(tmp_path / "e.db", c)                               # encrypted file, no passphrase
    s2 = ProductivityStore(tmp_path / "e.db", c, passphrase="correct horse battery")
    assert s2.get_note(n["id"])["body"].startswith("my private plan") and s2.get_list(l["id"])["items"][0]["text"] == "a blue scarf"
    other = s2.add_note("another")
    db = sqlite3.connect(tmp_path / "e.db")                                   # move one note's sealed body onto another: detected
    body = db.execute("SELECT body FROM notes WHERE id=?", (n["id"],)).fetchone()[0]
    db.execute("UPDATE notes SET body=? WHERE id=?", (body, other["id"]))
    db.commit()
    with pytest.raises(StoreError):
        s2.get_note(other["id"])


def test_export_and_delete_everything_including_audio_files(tmp_path):
    s = ProductivityStore(tmp_path / "p.db", Clock())
    n = s.add_note("hello there")
    audio = tmp_path / "rec.wav"
    audio.write_bytes(b"RIFF")
    s.db.execute("UPDATE notes SET audio_path=? WHERE id=?", (str(audio), n["id"]))
    s.db.commit()
    s.create_list("L", ["x"])
    e = s.export()
    assert len(e["notes"]) == 1 and e["lists"][0]["items"][0]["text"] == "x" and e["encrypted_at_rest"] is False
    assert s.delete_everything() == {"notes": 1, "lists": 1, "reminders": 0}
    assert s.notes() == [] and s.lists() == [] and not audio.exists()


def test_threads_can_share_the_store(st):
    errs = []

    def work(k):
        try:
            for i in range(20):
                st.add_note(f"note {k} {i}")
        except Exception as e:                                                # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(st.notes(limit=1000)) == 80


def test_titles_and_light_cleanup_never_reword():
    assert suggest_title("buy milk. then call mum") == "Buy milk"
    assert suggest_title("   ") == "Untitled note"
    raw = "um so i need to research how to make the sprite animations use less memory"
    out = light_cleanup(raw)
    assert out == "So i need to research how to make the sprite animations use less memory."
    assert "research how to make the sprite animations use less memory" in out
    assert light_cleanup("hello   world , again") == "Hello world, again."
