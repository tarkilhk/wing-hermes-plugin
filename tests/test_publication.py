import base64
import importlib
import json
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
        if path == "graphql":
            return {
                "data": {
                    "createPullRequest": {
                        "pullRequest": {
                            "url": "https://github.com/NousResearch/hermes-agent/pull/fixture"
                        }
                    }
                }
            }
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
    # Exercise publication behavior at a fixed fixture version, independently
    # of the next real release's version.
    manifest_path = tmp_path / "plugin.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["version"] = "0.3.0"
    manifest_path.write_text(yaml.safe_dump(manifest))
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
        {
            f"{repo}/releases/tags/v0.3.0": {"draft": False},
            f"{repo}/git/ref/tags/v0.3.0": {"object": {"type": "tag", "sha": "c" * 40}},
            f"{repo}/commits/v0.3.0": {"sha": OLD},
        }
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
        {
            query: [],
            f"repos/{catalog.UPSTREAM}/git/ref/heads/main": {"object": {"sha": OLD}},
            f"repos/{catalog.UPSTREAM}": {"node_id": "upstream-node"},
            f"repos/{catalog.FORK}": {"node_id": "fork-node"},
        }
    )
    assert catalog.submit(publication.entry, github).endswith("/pull/fixture")
    mutations = [(method, path, body) for method, path, body in github.calls if method != "GET"]
    assert [method for method, _, _ in mutations] == ["POST", "PUT", "POST"]
    assert mutations[0][2] == {"ref": "refs/heads/wing-plugin-v0.3.0", "sha": OLD}
    assert mutations[1][1].endswith("/contents/plugin-catalog/wing.yaml")
    assert yaml.safe_load(base64.b64decode(mutations[1][2]["content"])) == publication.entry
    assert mutations[2][1] == "graphql"
    request = mutations[2][2]["variables"]["input"]
    assert request["repositoryId"] == "upstream-node"
    assert request["headRepositoryId"] == "fork-node"
    assert request["headRefName"] == "tarkilhk:wing-plugin-v0.3.0"
    assert request["baseRefName"] == "main"
    assert request["maintainerCanModify"] is True
    assert SHA in request["body"]


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


def test_absent_tag_uses_404_ref_contract_without_requesting_missing_commit(
    publication, monkeypatch
):
    paths = []

    def run(command, **_):
        method, path = command[3:5]
        paths.append(path)
        if method == "GET" and path.endswith(("/releases/tags/v0.3.0", "/git/ref/tags/v0.3.0")):
            return SimpleNamespace(returncode=1, stdout="", stderr="gh: Not Found (HTTP 404)")
        if "/commits/" in path:
            pytest.fail("Missing tag must not use the commits endpoint, which returns HTTP 422")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"id": 7}), stderr="")

    monkeypatch.setattr(publication.catalog.subprocess, "run", run)
    assert (
        publication.release.publish(
            publication.catalog.GitHub(), SHA, publication.root, lambda *_: None
        )
        == SHA
    )
    assert any("/git/ref/tags/" in path for path in paths)


def test_missing_lookup_does_not_hide_other_422_failures(publication, monkeypatch):
    monkeypatch.setattr(
        publication.catalog.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="gh: Validation Failed (HTTP 422)"
        ),
    )
    with pytest.raises(RuntimeError, match="HTTP 422"):
        publication.catalog.GitHub().request("GET", "repos/example/ref", missing=True)


def test_catalog_cli_uses_explicit_release_source(publication, monkeypatch, capsys):
    def head(command, *, cwd, text):
        assert command == ["git", "rev-parse", "HEAD"]
        assert cwd == publication.root
        return SHA + "\n"

    def submit(entry, github):
        assert entry == publication.entry
        return "https://github.com/pr/fixture"

    monkeypatch.setattr(publication.catalog.subprocess, "check_output", head)
    monkeypatch.setattr(publication.catalog, "submit", submit)
    publication.catalog.main(["submit", "--source", str(publication.root), "--sha", SHA])
    assert capsys.readouterr().out.strip() == "https://github.com/pr/fixture"


def test_catalog_cli_rejects_wrong_release_head_before_submission(publication, monkeypatch):
    monkeypatch.setattr(publication.catalog.subprocess, "check_output", lambda *a, **k: OLD)
    monkeypatch.setattr(publication.catalog, "submit", lambda *a: pytest.fail("must not submit"))
    with pytest.raises(ValueError, match="exact release source"):
        publication.catalog.main(["submit", "--source", str(publication.root), "--sha", SHA])


def test_catalog_cli_requires_source_and_renders_that_source(publication, capsys):
    with pytest.raises(SystemExit) as result:
        publication.catalog.main(["render", "--sha", SHA])
    assert result.value.code == 2
    publication.catalog.main(["render", "--source", str(publication.root), "--sha", SHA])
    assert yaml.safe_load(capsys.readouterr().out) == publication.entry


def test_github_failure_reports_only_bounded_error_fields(publication, monkeypatch):
    secret = "test-token-not-for-output"
    monkeypatch.setenv("GH_TOKEN", secret)
    response = {
        "message": "Cannot create pull request " + secret,
        "errors": [
            {
                "resource": "PullRequest",
                "field": "head",
                "code": "custom",
                "message": "x" * 1000,
                "value": "private submitted value",
            }
        ]
        * 10,
        "token": "private response token",
        "body": "private response body",
        "user": {"email": "private email"},
    }
    monkeypatch.setattr(
        publication.catalog.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout=json.dumps(response), stderr="gh: Not Found (HTTP 404)"
        ),
    )
    with pytest.raises(RuntimeError) as result:
        publication.catalog.GitHub().request("POST", "repos/example/pulls", {"body": "private"})
    message = str(result.value)
    assert "HTTP 404" in message and "Cannot create pull request [redacted]" in message
    assert "private" not in message and secret not in message
    assert "x" * 513 not in message
    assert message.count('"resource": "PullRequest"') == 5


@pytest.mark.parametrize("output", ["<html>private</html>", "[]", "null"])
def test_github_error_details_ignore_non_error_json(publication, output):
    assert publication.catalog.error_details(output) == ""


def test_graphql_errors_fail_even_with_success_http_status(publication, monkeypatch):
    monkeypatch.setattr(
        publication.catalog.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": {"createPullRequest": None},
                    "errors": [{"type": "FORBIDDEN", "message": "Creation is restricted"}],
                }
            ),
            stderr="",
        ),
    )
    with pytest.raises(RuntimeError, match="Creation is restricted"):
        publication.catalog.GitHub().request("POST", "graphql", {"query": "mutation {}"})
