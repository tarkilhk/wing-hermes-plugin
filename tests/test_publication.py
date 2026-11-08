import base64
import importlib
import shutil
from types import SimpleNamespace

import pytest
import yaml
from conftest import ROOT

SHA = "a" * 40
OLD = "b" * 40


class FakeGitHub:
    def __init__(self, read=None):
        self.read = read or {}
        self.calls = []

    def request(self, method, path, body=None, missing=False):
        self.calls.append((method, path, body))
        if method == "GET":
            if path in self.read:
                return self.read[path]
            if missing:
                return None
            raise AssertionError(f"Unexpected read: {path}")
        if path.endswith("/pulls"):
            return {"html_url": "https://github.com/NousResearch/hermes-agent/pull/fixture"}
        if path.endswith("/releases"):
            return {"id": 7}
        return {}


@pytest.fixture
def publication(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    catalog = importlib.import_module("catalog")
    release = importlib.import_module("release")
    for relative in importlib.import_module("package").FILES + ("catalog.yaml",):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return SimpleNamespace(
        catalog=catalog, release=release, root=tmp_path, entry=catalog.render(SHA, tmp_path)
    )


def test_catalog_derives_version_capabilities_and_pins_screenshot(publication):
    entry = publication.entry
    assert entry["version"] == "0.3.0"
    assert entry["capabilities"]["provides_tools"] == ["wing_email_draft", "wing_email_draft_read"]
    assert SHA in entry["screenshots"][0]
    assert yaml.safe_load(publication.catalog.entry_yaml(entry)) == entry
    with pytest.raises(ValueError, match="commit hash"):
        publication.catalog.render("main", publication.root)


def test_publish_uploads_before_exposing_release_and_keeps_source_identity(publication):
    github = FakeGitHub()

    def upload(tag, assets):
        assert tag == "v0.3.0"
        assert len(assets) == 3 and all(p.is_file() for p in assets)
        assert yaml.safe_load(assets[2].read_text())["sha"] == SHA
        assert not any(method == "PATCH" for method, _, _ in github.calls)

    assert publication.release.publish(github, SHA, publication.root, upload) == SHA
    created = next(body for method, path, body in github.calls if path.endswith("/releases"))
    assert created["draft"] is True and created["target_commitish"] == SHA
    assert github.calls[-1][2] == {"draft": False}


def test_same_version_push_keeps_published_tag_and_does_not_upload(publication):
    repo = f"repos/{publication.catalog.PLUGIN_REPO}"
    github = FakeGitHub(
        {f"{repo}/releases/tags/v0.3.0": {"draft": False}, f"{repo}/commits/v0.3.0": {"sha": OLD}}
    )
    assert (
        publication.release.publish(
            github, SHA, publication.root, lambda *_: pytest.fail("must not upload")
        )
        == OLD
    )
    assert all(method == "GET" for method, _, _ in github.calls)


def test_retry_finishes_only_its_own_draft(publication):
    repo = f"repos/{publication.catalog.PLUGIN_REPO}"
    record = {"id": 7, "draft": True, "target_commitish": SHA}
    github = FakeGitHub({f"{repo}/releases/tags/v0.3.0": record})
    assert publication.release.publish(github, SHA, publication.root, lambda *_: None) == SHA
    assert not any(method == "POST" for method, _, _ in github.calls)
    record["target_commitish"] = OLD
    with pytest.raises(RuntimeError, match="another source"):
        publication.release.publish(github, SHA, publication.root, lambda *_: pytest.fail())


def test_upload_failure_keeps_release_unpublished(publication):
    github = FakeGitHub()

    def fail(*_):
        raise RuntimeError("upload failed")

    with pytest.raises(RuntimeError, match="upload failed"):
        publication.release.publish(github, SHA, publication.root, fail)
    assert not any(method == "PATCH" for method, _, _ in github.calls)


def test_submission_creates_only_version_branch_entry_and_one_pr(publication):
    catalog = publication.catalog
    query = "repos/NousResearch/hermes-agent/pulls?state=all&head=tarkilhk%3Awing-plugin-v0.3.0&base=main"
    github = FakeGitHub(
        {query: [], f"repos/{catalog.UPSTREAM}/git/ref/heads/main": {"object": {"sha": OLD}}}
    )
    assert catalog.submit(publication.entry, github).endswith("/pull/fixture")
    mutations = [(method, path, body) for method, path, body in github.calls if method != "GET"]
    assert [method for method, _, _ in mutations] == ["POST", "PUT", "POST"]
    assert mutations[0][2] == {"ref": "refs/heads/wing-plugin-v0.3.0", "sha": OLD}
    assert mutations[1][1].endswith("/contents/plugin-catalog/wing.yaml")
    assert yaml.safe_load(base64.b64decode(mutations[1][2]["content"])) == publication.entry
    assert mutations[2][2]["head"] == "tarkilhk:wing-plugin-v0.3.0"


@pytest.mark.parametrize("merged", [False, True])
def test_existing_submission_is_idempotent_even_after_branch_deleted(publication, merged):
    entry = publication.entry
    query = "repos/NousResearch/hermes-agent/pulls?state=all&head=tarkilhk%3Awing-plugin-v0.3.0&base=main"
    github = FakeGitHub(
        {
            query: [
                {
                    "state": "closed" if merged else "open",
                    "merged_at": "today" if merged else None,
                    "html_url": "https://github.com/pr/1",
                    "head": {"sha": OLD},
                }
            ],
            f"repos/tarkilhk/hermes-agent/contents/plugin-catalog/wing.yaml?ref={OLD}": {
                "content": base64.b64encode(publication.catalog.entry_yaml(entry).encode()).decode()
            },
        }
    )
    assert publication.catalog.submit(entry, github) == "https://github.com/pr/1"
    assert all(method == "GET" for method, _, _ in github.calls)


def test_closed_unmerged_submission_is_not_reopened(publication):
    query = "repos/NousResearch/hermes-agent/pulls?state=all&head=tarkilhk%3Awing-plugin-v0.3.0&base=main"
    github = FakeGitHub(
        {query: [{"state": "closed", "merged_at": None, "html_url": "https://github.com/pr/1"}]}
    )
    with pytest.raises(RuntimeError, match="closed"):
        publication.catalog.submit(publication.entry, github)
    assert len(github.calls) == 1
