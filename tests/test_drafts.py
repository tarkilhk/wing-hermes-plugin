import importlib.util
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from conftest import PROFILE, ROOT, make_app
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location("drafts_test", ROOT / "drafts.py")
drafts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drafts)


def fields(body="Hello, 日本\n\nThank you & goodbye + ?"):
    return {
        "to": ["front@example.com"],
        "cc": [],
        "bcc": [],
        "subject": "Stay & bags",
        "body": body,
    }


@pytest.fixture
def store(tmp_path):
    return drafts.DraftStore(lambda: sqlite3.connect(tmp_path / "drafts.db", timeout=10))


def test_versions_are_immutable_and_conflicts_do_not_overwrite(store):
    first = store.save(fields())
    second = store.save(fields("Changed"), first["id"], 1)
    assert second["version"] == 2
    assert store.read(first["id"], 1) == first
    assert store.read(first["id"], 2) == second
    with pytest.raises(drafts.DraftError, match="newer version"):
        store.save(fields("Lost change"), first["id"], 1)
    with pytest.raises(drafts.DraftError) as error:
        store.read(first["id"], 3)
    assert error.value.code == 404


def test_competing_revisions_have_one_winner(store):
    first = store.save(fields())

    def revise(body):
        try:
            return store.save(fields(body), first["id"], 1)["version"]
        except drafts.DraftError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(revise, ["First", "Second"])) == [2, 409]


@pytest.mark.parametrize(
    "patch",
    [
        {"body": "界" * 22000},
        {"subject": "hello\r\nbcc:bad@example.com"},
        {"to": ["Name <a@example.com>"]},
        {"cc": "a@example.com"},
        {"to": ["a@example.com,b@example.com"]},
        {"body": " "},
    ],
)
def test_rejects_invalid_fields_without_clipping(store, patch):
    with pytest.raises(drafts.DraftError):
        store.save({**fields(), **patch})


def test_tool_registration_card_and_exact_read_without_voice_credentials(store, monkeypatch):
    monkeypatch.setattr(drafts, "DraftStore", lambda: store)
    result = json.loads(drafts.create_draft(fields("x" * 12000)))
    reference = json.loads(result["card"].splitlines()[1])
    assert reference == {"id": result["id"], "version": 1}
    assert result["fields"]["body"] == "x" * 12000
    read = json.loads(drafts.read_draft({"draft_id": result["id"], "version": 1}))
    assert read == result
    spec = importlib.util.spec_from_file_location(
        "wing_registration_test", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    import sys

    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    calls = []
    module.register(SimpleNamespace(register_tool=lambda **kwargs: calls.append(kwargs)))
    assert [call["name"] for call in calls] == ["wing_email_draft", "wing_email_draft_read"]
    assert all("check_fn" not in call and "requires_env" not in call for call in calls)


def test_dashboard_and_tools_share_profile_store_without_provider_calls(api, tmp_path, monkeypatch):
    def connect():
        return sqlite3.connect(tmp_path / (PROFILE.get() + ".db"))

    monkeypatch.setattr(drafts, "_connect", connect)
    monkeypatch.setattr(api._drafts, "_connect", connect)
    token = PROFILE.set("alice")
    try:
        draft = json.loads(drafts.create_draft(fields()))
    finally:
        PROFILE.reset(token)
    route = f"/api/plugins/wing/drafts/v1/{draft['id']}/versions/1"
    with TestClient(make_app(api)) as client:
        result = client.get(route, params={"profile": "alice"})
        assert result.status_code == 200
        assert result.json()["fields"] == fields()
        assert client.get(route, params={"profile": "bob"}).status_code == 404
        assert client.get(route + "?profile=missing").status_code == 404
        assert (
            client.get(route.replace("/versions/1", "/versions/2") + "?profile=alice").status_code
            == 404
        )


def test_bad_references_fail_before_opening_database():
    store = drafts.DraftStore(lambda: pytest.fail("must not touch storage"))
    for draft_id, version in [("../secret", 1), ("d_" + "a" * 32, True), ("d_" + "a" * 32, 0)]:
        with pytest.raises(drafts.DraftError):
            store.read(draft_id, version)
