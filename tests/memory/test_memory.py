import json
import pytest
from companion.interfaces import MemoryStore as ABC
from companion.memory import (ConsentRequired, Disabled, MemoryError_, MemoryStore, ReflectionVault, WrongUser,
                              decide, extract_candidates)

D = 86400


@pytest.fixture
def mem(tmp_path, clock):
    return MemoryStore(tmp_path / "mem.db", "her", clock)


@pytest.fixture
def vault(tmp_path, clock):
    return ReflectionVault(tmp_path / "vault.db", "her", clock)


def rec(**kw):
    return {"category": "profile", "value": "Vulpix", "key": "favorite_pokemon", **kw}


def test_implements_replaceable_interface(mem):
    assert isinstance(mem, ABC)


def test_add_search_and_spec_example_shape(mem):
    mid = mem.add(rec())
    r = mem.search("what is my favorite pokemon")[0]
    assert r["id"] == mid and r["key"] == "favorite_pokemon" and r["value"] == "Vulpix"
    assert r["source"] == "explicit_user_statement" and r["status"] == "active"


def test_persists_across_restart(tmp_path, clock):
    MemoryStore(tmp_path / "m.db", "her", clock).add(rec())
    again = MemoryStore(tmp_path / "m.db", "her", clock)
    assert again.search("vulpix")


def test_same_key_updates_instead_of_duplicating(mem):
    a = mem.add(rec())
    b = mem.add(rec(value="Ninetales"))
    assert a == b and len(mem.list("profile")) == 1 and mem.list()[0]["value"] == "Ninetales"


def test_near_duplicate_text_merges(mem):
    a = mem.add({"category": "semantic", "value": "Foxes have nine tails in folklore"})
    b = mem.add({"category": "semantic", "value": "foxes have nine tails in folklore."})
    assert a == b and len(mem.list()) == 1


def test_ai_inference_is_pending_and_never_retrieved_until_confirmed(mem):
    mid = mem.add(rec(source="ai_inference", value="Eevee", key="favorite_pokemon2", confidence=0.4))
    assert mem.list()[0]["status"] == "pending"
    assert mem.search("eevee") == []
    mem.confirm(mid)
    r = mem.search("eevee")[0]
    assert r["source"] == "user_confirmed_inference"
    with pytest.raises(MemoryError_):
        mem.confirm(mid)  # no longer pending


def test_inference_cannot_overwrite_explicit_fact(mem):
    mem.add(rec())
    mem.add(rec(value="Eevee", source="ai_inference"))
    row = mem.list()[0]
    assert len(mem.list()) == 1 and row["value"] == "Vulpix" and row["status"] == "active"


def test_correct_and_delete_and_clear(mem):
    mid = mem.add(rec())
    mem.update(mid, {"value": "Ninetales"})
    row = mem.list()[0]
    assert row["value"] == "Ninetales" and row["source"] == "user_correction"
    with pytest.raises(MemoryError_):
        mem.update(mid, {"confidence": 0})  # not an editable field
    mem.delete(mid)
    assert mem.list() == []
    with pytest.raises(MemoryError_):
        mem.delete(mid)
    mem.add(rec()); mem.add({"category": "semantic", "value": "x y z"})
    assert mem.clear("profile") == 1 and len(mem.list()) == 1


def test_deleted_data_not_recoverable_via_search_or_export(mem):
    mid = mem.add(rec(value="secret-ish value"))
    mem.delete(mid)
    assert "secret-ish" not in mem.export() and mem.search("secret") == []


def test_disabled_category_blocks_writes_and_hides_reads(mem):
    mem.add(rec())
    mem.set_enabled("profile", False)
    with pytest.raises(Disabled):
        mem.add(rec(value="x"))
    assert mem.search("vulpix") == []
    mem.set_enabled("profile", True)
    assert mem.search("vulpix")


def test_episodic_retention_expires(mem, clock):
    mem.add({"category": "episodic", "value": "We talked about the beach trip"})
    assert mem.search("beach")
    clock.advance(91 * D)
    assert mem.search("beach") == [] and mem.list() == []


def test_custom_retention_and_validation(mem, clock):
    mem.set_retention("profile", 1)
    mem.add(rec())
    clock.advance(2 * D)
    assert mem.purge_expired() == 1
    with pytest.raises(MemoryError_):
        mem.set_retention("profile", 0)


def test_retrieval_prefers_relevant_and_recent(mem, clock):
    mem.add({"category": "semantic", "value": "Cooking pasta takes ten minutes"})
    clock.advance(200 * D)
    mem.add({"category": "semantic", "value": "Pasta sauce uses tomatoes and basil"})
    mem.add({"category": "semantic", "value": "Cats sleep a lot"})
    hits = mem.search("pasta sauce")
    assert hits[0]["value"].startswith("Pasta sauce") and all("Cats" not in h["value"] for h in hits)


def test_validation_limits(mem):
    for bad in [{"category": "nope", "value": "x"}, {"category": "profile", "value": ""},
                {"category": "profile", "value": "x" * 5000}, rec(source="telepathy"), rec(confidence=2),
                {"category": "reflection", "value": "x"}]:
        with pytest.raises(MemoryError_):
            mem.add(bad)


def test_sql_injection_is_inert(mem):
    mem.add({"category": "semantic", "value": "x'); DROP TABLE memories;--"})
    assert len(mem.list()) == 1 and mem.search("drop table")


def test_export_contains_everything_including_pending(mem):
    mem.add(rec()); mem.add(rec(source="ai_inference", value="Eevee", key="k2"))
    data = json.loads(mem.export())
    assert len(data["memories"]) == 2 and data["user_id"] == "her"


# ---- vault -------------------------------------------------------------
def test_vault_off_by_default_and_needs_consent_every_time(vault):
    r = {"category": "reflection", "value": "I felt overwhelmed when the talk got loud"}
    with pytest.raises(Disabled):
        vault.add(r, consent=True)
    vault.set_enabled("reflection", True)
    with pytest.raises(ConsentRequired):
        vault.add(r)
    vault.add(r, consent=True)
    with pytest.raises(ConsentRequired):
        vault.add({**r, "value": "another one"})


def test_vault_refuses_ai_interpretations(vault):
    vault.set_enabled("reflection", True)
    with pytest.raises(MemoryError_):
        vault.add({"category": "reflection", "value": "She has avoidant attachment", "source": "ai_inference"},
                  consent=True)


def test_vault_is_separate_from_ordinary_memory(tmp_path, clock, mem, vault):
    vault.set_enabled("reflection", True)
    vault.add({"category": "reflection", "value": "private thought about the argument"}, consent=True)
    assert mem.search("argument") == [] and "argument" not in mem.export()
    assert vault.search("argument")
    with pytest.raises(MemoryError_):
        mem.add({"category": "reflection", "value": "x"})


def test_other_user_cannot_open_store(tmp_path, clock):
    ReflectionVault(tmp_path / "v.db", "her", clock).close()
    with pytest.raises(WrongUser):
        ReflectionVault(tmp_path / "v.db", "him", clock)
    MemoryStore(tmp_path / "m.db", "her", clock).close()
    with pytest.raises(WrongUser):
        MemoryStore(tmp_path / "m.db", "him", clock)


def test_vault_default_retention_and_delete(vault, clock):
    vault.set_enabled("reflection", True)
    mid = vault.add({"category": "reflection", "value": "temporary note"}, consent=True)
    clock.advance(61 * D)
    assert vault.purge_expired() == 1
    vault.add({"category": "reflection", "value": "keep"}, consent=True)
    vault.clear("reflection")
    assert vault.list() == []


# ---- extraction / policy -----------------------------------------------
def test_extraction_explicit_statements():
    c = extract_candidates("I love Vulpix!")[0]
    assert c["value"] == "Vulpix" and c["source"] == "explicit_user_statement" and decide(c) == "save"
    c = extract_candidates("My favorite color is soft blue")[0]
    assert c["key"] == "favorite_color" and c["value"] == "soft blue"
    assert extract_candidates("Please remember that my exam is on Friday")[0]["category"] == "semantic"
    assert extract_candidates("the weather is nice") == []


def test_sensitive_content_asks_and_inference_asks():
    c = extract_candidates("remember that I was diagnosed with anxiety last year")[0]
    assert c["sensitive"] and decide(c) == "ask"
    assert decide({"value": "likes foxes", "source": "ai_inference"}) == "ask"
    assert decide({"value": ""}) == "drop"
