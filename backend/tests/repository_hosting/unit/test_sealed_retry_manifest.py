"""A sealed retry must rebuild the manifest its receipt sealed.

Stopping at the pin's own proofs shortens the walk to the root. That changes the
digest, and it hides new blobs from the file size policy, which reads them from
the manifest.
"""

from __future__ import annotations

from src.version_engine.infrastructure.supabase.object_proofs import ObjectProofRepository
from src.version_engine.storage.publication import ClosureVerifier
from src.version_engine.write_engine.git_object_format import encode_object

FORMAT = "sha1"
PROJECT = "project-1"


class Backend:
    publication_project_id = PROJECT

    def __init__(self):
        self.objects = {}

    def add(self, kind, body):
        oid, loose = encode_object(kind, body, object_format=FORMAT)
        self.objects[oid] = loose
        return oid

    def durable_readback(self):
        return self

    def get_durable(self, oid):
        return self.objects[oid]


class Control:
    """The visibility rule of get_version_object_proofs for one publication pin."""

    def __init__(self):
        self.rows = {}

    def call(self, name, **arguments):
        if name == "register_version_object_proof_page":
            for row in arguments["p_rows"]:
                self.rows[row["object_id"]] = {
                    **row,
                    "project_id": PROJECT,
                    "object_format": FORMAT,
                    "valid": True,
                    "published": False,
                }
            return len(arguments["p_rows"])
        assert name == "get_version_object_proofs"
        return {oid: self.rows[oid] for oid in arguments["p_oids"] if oid in self.rows}


def publication():
    backend = Backend()
    blob = backend.add("blob", b"too large for the limit")
    tree = backend.add("tree", b"100644 file\0" + bytes.fromhex(blob))
    person = "Product <product@example.test> 1700000000 +0000"
    commit = backend.add(
        "commit",
        f"tree {tree}\nauthor {person}\ncommitter {person}\n\nsave\n".encode(),
    )
    return backend, Control(), blob, {commit: "commit"}


def proofs(control):
    return ObjectProofRepository(control, PROJECT, "actor", "pin", FORMAT)


def verify(backend, repository, roots):
    verifier = ClosureVerifier(backend, object_format=FORMAT)
    verifier.proofs = repository
    return verifier.verify(roots)


def sealed():
    backend, control, blob, roots = publication()
    repository = proofs(control)
    manifest = verify(backend, repository, roots)
    repository.persist(manifest)
    return backend, control, blob, roots, manifest


def test_trusting_its_own_proofs_would_lose_the_sealed_manifest():
    backend, control, blob, roots, manifest = sealed()

    shortened = verify(backend, proofs(control), roots)

    assert shortened.digest != manifest.digest
    assert blob not in shortened.objects


def test_a_sealed_retry_rebuilds_the_sealed_manifest():
    backend, control, blob, roots, manifest = sealed()
    retry = proofs(control)
    retry.distrust_unpublished()

    resumed = verify(backend, retry, roots)

    assert resumed.digest == manifest.digest
    assert resumed.objects[blob].size == manifest.objects[blob].size


def test_a_sealed_retry_still_stops_at_published_history():
    backend, control, blob, roots, manifest = sealed()
    control.rows[blob]["published"] = True
    del backend.objects[blob]
    retry = proofs(control)
    retry.distrust_unpublished()

    resumed = verify(backend, retry, roots)

    assert resumed.digest == manifest.digest
    assert blob in retry.published
