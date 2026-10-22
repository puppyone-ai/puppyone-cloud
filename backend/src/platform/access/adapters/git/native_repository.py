"""Canonical Git transport over S3 objects and PG refs, without a local repo."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.responses import Response

from src.platform.access.adapters.git.execution import ObjectGitResponse, checkpoint
from src.platform.access.adapters.git.native_fetch import FetchRequest, fetch_chunks
from src.platform.access.adapters.git.native_wire import (
    advertisement,
    ls_refs,
    receive_commands,
    upload_packets,
    version,
)
from src.platform.access.adapters.git.object_pack import IncomingPack
from src.platform.access.adapters.git.object_reader import PublishedObjectReader
from src.platform.access.adapters.git.protocol import flush_pkt, pkt_line
from src.version_engine.read.repository_snapshot import repository_snapshot
from src.version_engine.write_engine.git_object_graph import object_edges
from src.version_engine.write_engine.ref_transaction import (
    RefState,
    RefTransactionService,
    admitted_actor,
)


class PublicationIndeterminateError(RuntimeError):
    """No definitive result was obtained; the client must rediscover refs."""


class NativeGitRepository:
    def __init__(self, service: RefTransactionService, *, expected_generation=None):
        self.expected_generation = expected_generation
        self.service = service
        self.control = service.control
        self.project_id = service.project_id
        self.format = service.object_format

    def check_generation(self, generation):
        if self.expected_generation is not None and generation != self.expected_generation:
            raise PermissionError("Git repository generation changed")

    def info_refs(self, grant, service: str, *, protocol=""):
        actor = admitted_actor(grant, self.project_id, write=False)
        if service not in {"git-upload-pack", "git-receive-pack"}:
            raise ValueError("unsupported Git service")
        snapshot = self.control.read_snapshot(self.project_id, actor)
        self.check_generation(snapshot.get("generation"))
        output = advertisement(snapshot, service, protocol, self.format)
        return Response(
            pkt_line(f"# service={service}\n".encode()) + flush_pkt() + output,
            media_type=f"application/x-{service}-advertisement",
            headers={"Cache-Control": "no-cache"},
        )

    def upload(self, grant, request_path: Path, *, protocol=""):
        actor = admitted_actor(grant, self.project_id, write=False)
        packets = upload_packets(request_path)
        protocol_version = version(protocol)
        commands = [
            value.strip()
            for value in packets
            if isinstance(value, bytes) and value.startswith(b"command=")
        ]
        if protocol_version == 2:
            if len(commands) != 1 or commands[0] not in {b"command=ls-refs", b"command=fetch"}:
                raise ValueError("unsupported protocol-v2 command")
            if commands[0] == b"command=ls-refs":
                snapshot = self.control.read_snapshot(self.project_id, actor)
                self.check_generation(snapshot.get("generation"))
                return Response(
                    ls_refs(snapshot, packets, self.format),
                    media_type="application/x-git-upload-pack-result",
                )
        elif commands:
            raise ValueError("protocol-v2 command without negotiation")
        request = FetchRequest(packets, self.format, protocol_version)
        context = repository_snapshot(
            self.control, self.service.backend, grant, project_id=self.project_id
        )
        read = context.__enter__()
        try:
            self.check_generation(read.generation)
            read.backend = read.backend.pinned_reader(read)
            if read.object_format != self.format:
                raise ValueError("repository object format mismatch")
            reader = PublishedObjectReader(
                read, self.control, proof_factory=self.service.proof_factory
            )
            # Reject unreadable wants before HTTP headers are sent. No blob is
            # downloaded here, including a raw-OID partial-clone lazy fetch.
            reader.authorize([*request.wants, *request.shallow])
            return ObjectGitResponse(fetch_chunks(reader, request), context)
        except BaseException:
            context.__exit__(None, None, None)
            raise

    def commands(self, request_path):
        with request_path.open("rb") as handle:
            return receive_commands(handle, self.format)

    def receive(self, grant, request_path: Path, *, publication=None, initial_attempt=None,
                max_pack_bytes=None):
        admitted_actor(grant, self.project_id, write=True)
        outcomes = {}
        with request_path.open("rb") as handle:
            edits, capabilities = receive_commands(handle, self.format)
            if initial_attempt is not None:
                initial_attempt = str(uuid.UUID(initial_attempt))
                # The extension identifies one immutable initial-main attempt.
                # Ordinary Git and multi-ref pushes retain their existing path.
                if (publication is not None or len(edits) != 1
                        or edits[0].name != b"refs/heads/main"
                        or edits[0].expected != RefState() or edits[0].new.oid is None):
                    raise ValueError("initial attempt requires creation of main")
            if publication is not None:
                publication.validate(edits)
            with repository_snapshot(
                self.control, self.service.backend, grant, project_id=self.project_id
            ) as snapshot:
                self.check_generation(snapshot.generation)
                snapshot.backend = snapshot.backend.pinned_reader(snapshot)
                reader = PublishedObjectReader(
                    snapshot, self.control, proof_factory=self.service.proof_factory
                )
                if publication is not None and snapshot.generation != publication.base.generation:
                    raise PermissionError("Run repository generation changed")
                with IncomingPack(reader) as incoming:
                    try:
                        incoming.read(handle, max_input_bytes=max_pack_bytes)
                    except (ValueError, KeyError, PermissionError) as exc:
                        return self.report(
                            edits, capabilities, {}, unpack="invalid pack: " + str(exc)
                        )
                    accepted, roots = [], {}
                    for edit in edits:
                        try:
                            if edit.name == b"HEAD":
                                raise ValueError("HEAD is changed through repository metadata")
                            if snapshot.refs.get(edit.name, RefState()) != edit.expected:
                                raise ValueError("stale ref")
                            closure = {}
                            if edit.new.oid is not None:
                                kind, _ = incoming.get(edit.new.oid)
                                if edit.name.startswith(b"refs/heads/") and kind != "commit":
                                    raise ValueError("branch target is not a commit")
                                roots[edit.name] = kind
                                stack = [(edit.new.oid, kind)]
                                seen = set()
                                while stack:
                                    checkpoint()
                                    oid, expected = stack.pop()
                                    if oid in seen:
                                        if oid in closure and closure[oid] != expected:
                                            raise ValueError("incoming edge type mismatch")
                                        continue
                                    seen.add(oid)
                                    if oid not in incoming.objects:
                                        reader.authorize([oid])
                                        if reader.allowed[oid] not in (None, expected):
                                            raise ValueError("published edge type mismatch")
                                        continue
                                    kind, body = incoming.get(oid)
                                    if kind != expected:
                                        raise ValueError("incoming edge type mismatch")
                                    closure[oid] = kind
                                    stack.extend(
                                        object_edges(kind, body, object_format=self.format)
                                    )
                            if publication is not None and publication.base.expected.oid:
                                ancestry, visited = [edit.new.oid], set()
                                ancestor = publication.base.expected.oid
                                while ancestry and ancestor not in visited:
                                    parent = ancestry.pop()
                                    if parent in visited:
                                        continue
                                    visited.add(parent)
                                    if parent == ancestor:
                                        break
                                    kind, body = incoming.get(parent)
                                    if kind != "commit":
                                        raise ValueError("Run ancestry is not a commit")
                                    ancestry.extend(
                                        oid
                                        for oid, kind in object_edges(
                                            kind, body, object_format=self.format
                                        )
                                        if kind == "commit"
                                    )
                                if ancestor not in visited:
                                    raise PermissionError(
                                        "Run candidate does not descend from its base"
                                    )
                            accepted.append(edit)
                        except (ValueError, KeyError, PermissionError) as exc:
                            outcomes[edit.name] = str(exc)
                    if "atomic" in capabilities and len(accepted) != len(edits):
                        accepted = []
                        outcomes.update({edit.name: "atomic receive rejected" for edit in edits})
                    batches = (
                        [accepted] if "atomic" in capabilities else [[edit] for edit in accepted]
                    )
                    pack_persisted = False
                    for batch in batches:
                        if not batch:
                            continue
                        desired = {
                            edit.new.oid: roots[edit.name]
                            for edit in batch
                            if edit.new.oid is not None
                        }

                        def prepare():
                            nonlocal pack_persisted
                            # Persist the completed standard pack, including any
                            # thin bases Git appended. Never re-encode incoming
                            # objects into the old loose/bundle representation.
                            if incoming.path is not None and not pack_persisted:
                                self.service.backend.put_git_pack_durable(incoming)
                                # The enclosing read pin protects the pack across
                                # non-atomic ref transactions. Later publications
                                # still make their own closure/capacity checks.
                                pack_persisted = True

                        request_key = (
                            publication.request_key
                            if publication is not None
                            else initial_attempt or str(uuid.uuid4())
                        )
                        try:
                            result = self.service.submit(
                                grant,
                                request_key=request_key,
                                generation=snapshot.generation,
                                edits=publication.base.edits(publication.candidate)
                                if publication is not None
                                else batch,
                                roots=desired,
                                prepare=prepare,
                                message="git push",
                                read_snapshot=snapshot,
                                ensure_empty_tree=False,
                            )
                        except Exception as exc:
                            actor = admitted_actor(grant, self.project_id, write=False)
                            try:
                                result = self.control.recover_result(
                                    self.project_id, actor, request_key
                                )
                            except Exception as lookup_error:
                                raise PublicationIndeterminateError(
                                    "Rediscover repository refs before retrying"
                                ) from lookup_error
                            if not result or result.get("status") not in {"committed", "rejected"}:
                                raise PublicationIndeterminateError(
                                    "Rediscover repository refs before retrying"
                                ) from exc
                        reason = None if result["status"] == "committed" else result["reason"]
                        outcomes.update({edit.name: reason for edit in batch})
        return self.report(edits, capabilities, outcomes)

    @staticmethod
    def report(edits, capabilities, outcomes, *, unpack="ok"):
        def safe(value):
            return (
                value.replace("\n", " ")
                .replace("\r", " ")
                .replace("\0", " ")[:1024]
                .encode("ascii", "replace")
            )

        report = pkt_line(b"unpack " + safe(unpack) + b"\n")
        for edit in edits:
            reason = outcomes.get(edit.name, "not published")
            line = (
                b"ok " + edit.name if reason is None else b"ng " + edit.name + b" " + safe(reason)
            )
            report += pkt_line(line + b"\n")
        report += flush_pkt()
        if "side-band-64k" in capabilities or "side-band" in capabilities:
            size = 65515 if "side-band-64k" in capabilities else 995
            report = (
                b"".join(
                    pkt_line(b"\x01" + report[i : i + size]) for i in range(0, len(report), size)
                )
                + flush_pkt()
            )
        return Response(
            report,
            media_type="application/x-git-receive-pack-result",
            headers={"Cache-Control": "no-cache"},
        )
