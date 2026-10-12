"""Real native snapshots/objects through HTTP; substituted auth/PG/S3 are counted."""

import base64
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.platform.auth.dependencies import get_current_user
from src.platform.authorization.dependencies import get_authorization_service
from src.version_engine.adapters.product.operation_adapter import ProductOperationAdapter
from src.version_engine.bootstrap.dependencies import get_product_operation_adapter
from src.version_engine.domain.errors import ObjectNotFoundError
from src.version_engine.entrypoints.http.content import router
from src.version_engine.entrypoints.http.content_resource import TEXT_PREVIEW_BYTES
from src.version_engine.read.repository_snapshot import repository_snapshot
from src.version_engine.write_engine.git_object_format import TreeEntry, encode_object, encode_tree
from tests.repository_hosting.integration.test_ref_transaction_service import grant
from tests.repository_hosting.unit.test_repository_snapshot import fixture, row

pytestmark = pytest.mark.hosting_component


@pytest.fixture(params=["sha1", "sha256"])
def resource_repo(request):
    wire, calls, objects, control, backend = fixture()
    fmt = wire["object_format"] = request.param

    def put(kind, body):
        oid, raw = encode_object(kind, body, object_format=fmt)
        objects[oid] = raw
        return oid

    def publish(entries):
        tree = put("tree", encode_tree(entries, object_format=fmt))
        commit = put(
            "commit",
            f"tree {tree}\nauthor A <a@local> 1 +0000\ncommitter A <a@local> 1 +0000\n\nTest\n".encode(),
        )
        wire["refs"] = [
            wire["refs"][0],
            row(b"refs/heads/main", {"kind": "oid", "oid": commit}, "commit"),
        ]
        return commit

    backend.get_durable = lambda oid: get(oid)

    def get(oid):
        calls.append("get")
        if oid not in objects:
            raise ObjectNotFoundError("missing fixture object")
        return objects[oid]

    blob = put("blob", "你好, workspace".encode())
    child = put("tree", encode_tree([TreeEntry("名字 #?%.md", b"100644", blob)], object_format=fmt))
    entries = [TreeEntry("docs", b"40000", child), TreeEntry("read me.md", b"100644", blob)]
    commit = publish(entries)
    manager = Mock()
    manager.open_native_read.side_effect = lambda project, actor: repository_snapshot(
        control, backend, actor, project_id=project
    )
    manager.get_native_service.side_effect = AssertionError("preview entered write pipeline")
    ops = ProductOperationAdapter(manager)
    authorization = Mock()
    authorization.authorize.return_value = grant("p")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_product_operation_adapter] = lambda: ops
    app.dependency_overrides[get_authorization_service] = lambda: authorization
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="u")
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            calls=calls,
            objects=objects,
            wire=wire,
            put=put,
            publish=publish,
            entries=entries,
            blob=blob,
            child=child,
            commit=commit,
            manager=manager,
            authorization=authorization,
            ops=ops,
            app=app,
        )


def read(repo, path="", **query):
    return repo.client.get("/api/v1/content/p/resource", params={"path": path, **query})


def test_one_http_text_read_one_grant_one_snapshot(resource_repo):
    r = resource_repo
    response = read(r, "docs/名字 #?%.md")
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["content_text"] == "你好, workspace"
    assert body["repository_revision"]["expected_oid"] == r.commit
    assert body["entry"]["size_bytes"] == len("你好, workspace".encode())
    assert body["path"] == "docs/名字 #?%.md"
    assert response.headers["cache-control"] == "private, no-store"
    r.authorization.authorize.assert_called_once()
    r.manager.open_native_read.assert_called_once()
    assert r.calls.count("begin") == r.calls.count("release") == 1
    # Commit, root tree, docs tree and blob; stat/read reuse snapshot objects.
    assert r.calls.count("get") == 4
    r.manager.get_native_service.assert_not_called()


@pytest.mark.parametrize("count", [1, 200, 1000])
def test_directory_cost_independent_of_child_count(resource_repo, count):
    r = resource_repo
    r.publish([TreeEntry(f"dir-{i:04}", b"40000", r.child) for i in range(count)])
    # Opening the parent must work without accessing any child tree.
    del r.objects[r.child]
    response = read(r)
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert len(body["entries"]) == min(count, 100)
    assert all(item["children_count"] is None for item in body["entries"])
    assert bool(body["next_cursor"]) == (count > 100)
    assert r.calls == ["begin", "get", "get", "release"]
    r.authorization.authorize.assert_called_once()


def test_directory_pages_and_stale_cursor(resource_repo):
    r = resource_repo
    first = read(r, limit=1).json()["data"]
    second = read(r, limit=1, cursor=first["next_cursor"])
    assert second.status_code == 200
    assert first["entries"][0]["path"] != second.json()["data"]["entries"][0]["path"]
    r.publish([TreeEntry("moved.md", b"100644", r.blob)])
    assert read(r, cursor=first["next_cursor"]).status_code == 409
    assert read(r, "read me.md").status_code == 404
    assert read(r, "moved.md").status_code == 200


def test_concurrent_head_change_does_not_mix_revision_and_body(resource_repo):
    r = resource_repo
    original = r.ops.open_read

    @contextmanager
    def changing_head(*args, **kwargs):
        with original(*args, **kwargs) as reader:
            r.publish([TreeEntry("other.md", b"100644", r.blob)])
            yield reader

    r.ops.open_read = changing_head
    response = read(r, "read me.md")
    assert response.status_code == 200
    assert response.json()["data"]["repository_revision"]["expected_oid"] == r.commit
    assert response.json()["data"]["content_text"] == "你好, workspace"


@pytest.mark.parametrize(
    "path", ["../secret", "docs/../read me.md", "docs//file", "/docs", "docs/", "a\x00b", "a\\b"]
)
def test_invalid_paths_fail_before_storage(resource_repo, path):
    assert read(resource_repo, path).status_code == 400
    assert not resource_repo.calls


def test_percent_names_are_not_decoded_twice(resource_repo):
    r = resource_repo
    r.publish([TreeEntry("%2F%2e%2e.md", b"100644", r.blob)])
    assert read(r, "%2F%2e%2e.md").json()["data"]["content_text"] == "你好, workspace"


def test_non_unicode_git_names_roundtrip_through_byte_locator(resource_repo):
    r = resource_repo
    name = b"legacy-\xff.md"
    r.publish([TreeEntry(name.decode("utf-8", "surrogateescape"), b"100644", r.blob)])
    listed = read(r).json()["data"]["entries"][0]
    assert base64.b64decode(listed["path_bytes_b64"]) == name
    response = read(r, path_bytes_b64=listed["path_bytes_b64"])
    assert response.status_code == 200, response.text
    assert response.json()["data"]["content_text"] == "你好, workspace"


def test_denied_and_foreign_grants_do_not_read_objects(resource_repo):
    r = resource_repo
    r.authorization.authorize.side_effect = HTTPException(404, "Project not found")
    assert read(r).status_code == 404
    assert not r.calls
    r.authorization.authorize.side_effect = None
    r.authorization.authorize.return_value = grant("other")
    assert read(r).status_code == 403
    assert not r.calls


def test_missing_auth_and_revocation(resource_repo):
    r = resource_repo
    assert read(r).status_code == 200
    r.calls.clear()
    r.authorization.authorize.side_effect = HTTPException(404, "Project not found")
    assert read(r).status_code == 404
    assert not r.calls

    def signed_out():
        raise HTTPException(401, "Sign in required")

    r.app.dependency_overrides[get_current_user] = signed_out
    assert read(r).status_code == 401
    assert not r.calls


def test_corruption_is_not_reported_as_absent_or_empty(resource_repo):
    r = resource_repo
    del r.objects[r.child]
    assert read(r, "docs").status_code == 500
    assert r.calls[-1] == "release"


def test_preview_caps_utf8_and_binary_modes(resource_repo):
    r = resource_repo
    large = r.put("blob", b"a" * (TEXT_PREVIEW_BYTES - 1) + "你好".encode())
    image = r.put("blob", b"image bytes")
    binary = r.put("blob", b"\x00cannot preview")
    r.publish(
        [
            TreeEntry("large.md", b"100644", large),
            TreeEntry("pic.png", b"100644", image),
            TreeEntry("binary.txt", b"100644", binary),
            TreeEntry("book.pdf", b"100644", binary),
            TreeEntry("link", b"120000", r.blob),
            TreeEntry("module", b"160000", r.blob),
        ]
    )
    body = read(r, "large.md").json()["data"]
    assert body["truncated"] and body["content_text"] == "a" * (TEXT_PREVIEW_BYTES - 1)
    picture = read(r, "pic.png").json()["data"]
    assert base64.b64decode(picture["content_base64"]) == b"image bytes"
    assert read(r, "binary.txt").json()["data"]["preview"] == "unavailable"
    for name in ["book.pdf", "module"]:
        r.calls.clear()
        assert read(r, name).json()["data"]["preview"] == "unavailable"
        assert r.calls.count("get") == 2  # Metadata only; no blob download.
    assert read(r, "link").json()["data"]["preview"] == "text"


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": 201},
        {"cursor": "!"},
        {"cursor": "e30="},
        {"path_bytes_b64": "!"},
        {"path_bytes_b64": "Li4vZmlsZQ=="},
    ],
)
def test_page_limits_and_invalid_cursors(resource_repo, query):
    assert read(resource_repo, **query).status_code in (400, 422)


@pytest.mark.parametrize('name', ['complete.md', 'data.csv', 'data.json', 'page.html', 'source.ts', 'config.toml', 'events.jsonl', 'query.sql', 'run.sh'])
def test_full_resource_text_keeps_complete_utf8_in_one_admitted_snapshot(resource_repo, name):
    r = resource_repo
    text = 'a' * (TEXT_PREVIEW_BYTES - 1) + '你好\n' + 'end of complete file'
    blob = r.put('blob', text.encode())
    r.publish([TreeEntry(name, b'100644', blob)])
    response = read(r, name, text_mode='full')
    assert response.status_code == 200, response.text
    body = response.json()['data']
    assert body['content_text'] == text
    assert not body['truncated']
    assert r.calls.count('begin') == r.calls.count('release') == 1
    assert r.calls.count('get') == 3
    r.authorization.authorize.assert_called_once()


def test_full_text_mode_does_not_download_native_media_or_decode_binary(resource_repo):
    r = resource_repo
    binary = r.put('blob', b'\x00binary')
    r.publish([TreeEntry('film.mp4', b'100644', binary), TreeEntry('pic.png', b'100644', binary), TreeEntry('bad.txt', b'100644', binary)])
    assert read(r, 'film.mp4', text_mode='full').json()['data']['preview'] == 'unavailable'
    assert r.calls.count('get') == 2
    r.calls.clear()
    assert read(r, 'pic.png', text_mode='full').json()['data']['preview'] == 'unavailable'
    assert r.calls.count('get') == 2
    assert read(r, 'bad.txt', text_mode='full').json()['data']['preview'] == 'unavailable'


@pytest.mark.parametrize('name', [b'movie #?.mp4', b'legacy-\xff.pdf'])
def test_inline_media_reauthorizes_and_reads_a_single_snapshot_with_lossless_paths(resource_repo, name):
    r = resource_repo
    blob = r.put('blob', b'0123456789')
    r.publish([TreeEntry(name.decode('utf-8', 'surrogateescape'), b'100644', blob)])
    locator = {'path_bytes_b64': base64.b64encode(name).decode()}
    signed = r.client.post('/api/v1/content/p/inline/sign', json=locator)
    assert signed.status_code == 200, signed.text
    url = signed.json()['data']['url']
    r.authorization.authorize.reset_mock()
    response = r.client.get(url, headers={'Range': 'bytes=2-5'})
    assert response.status_code == 206, response.text
    assert response.content == b'2345'
    assert response.headers['content-range'] == 'bytes 2-5/10'
    assert response.headers['cache-control'] == 'private, no-store'
    r.authorization.authorize.assert_called_once()
    assert r.authorization.authorize.call_args.args[1] == 'u'
    assert r.calls.count('begin') == r.calls.count('release') == 1
    assert r.calls.count('get') == 3
    r.calls.clear()
    r.authorization.authorize.side_effect = HTTPException(404, 'Project not found')
    assert r.client.get(url).status_code == 404
    assert not r.calls


def test_inline_token_cannot_be_replayed_for_a_different_path(resource_repo):
    from urllib.parse import parse_qs, urlsplit

    r = resource_repo
    signed = r.client.post('/api/v1/content/p/inline/sign', json={'path': 'read me.md'})
    token = parse_qs(urlsplit(signed.json()['data']['url']).query)['token'][0]
    response = r.client.get('/api/v1/content/p/inline', params={'path': 'other.md', 'token': token})
    assert response.status_code == 403
    assert not r.calls
