"""Profile-local immutable email drafts, inspired by arsiktech/hermes-draft-cards.

See NOTICE.md and LICENSE. No provider credentials, delivery or approval state.
"""

from __future__ import annotations

import json
import re
import secrets
from contextlib import closing

_ID = re.compile(r"d_[0-9a-f]{32}\Z")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS email_drafts (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    fields TEXT NOT NULL,
    PRIMARY KEY (id, version)
);
"""


class DraftError(ValueError):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def _connect():
    from plugins.plugin_storage import plugin_db

    return plugin_db("wing", "email-drafts.db")


def valid_reference(draft_id, version):
    return (
        isinstance(draft_id, str)
        and _ID.fullmatch(draft_id) is not None
        and type(version) is int
        and 1 <= version <= 10000
    )


def validate_fields(fields):
    if not isinstance(fields, dict) or set(fields) != {"to", "cc", "bcc", "subject", "body"}:
        raise DraftError("Supply to, cc, bcc, subject and body as a complete draft.")
    for key in ("to", "cc", "bcc"):
        values = fields[key]
        if not isinstance(values, list) or len(values) > 50:
            raise DraftError(f"{key} must contain at most 50 email addresses.")
        for value in values:
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 320
                or any(c in value for c in "\r\n\x00,<> ")
                or value.count("@") != 1
                or not all(value.split("@"))
            ):
                raise DraftError(
                    f"{key} must contain plain email addresses, without display names."
                )
    for key, limit in (("subject", 4096), ("body", 65536)):
        value = fields[key]
        if not isinstance(value, str) or "\x00" in value or len(value.encode("utf-8")) > limit:
            raise DraftError(
                f"{key} must be text of at most {limit} UTF-8 bytes; nothing was saved."
            )
    if any(c in fields["subject"] for c in "\r\n"):
        raise DraftError("subject must be one line.")
    if not fields["body"].strip():
        raise DraftError("body must not be empty.")
    return fields


class DraftStore:
    def __init__(self, connect=None):
        self._connect = connect or _connect

    def _db(self):
        conn = self._connect()
        conn.executescript(_SCHEMA)
        return conn

    def read(self, draft_id, version):
        if not valid_reference(draft_id, version):
            raise DraftError("Invalid email draft reference.")
        with closing(self._db()) as conn:
            row = conn.execute(
                "SELECT fields FROM email_drafts WHERE id = ? AND version = ?",
                (draft_id, version),
            ).fetchone()
        if row is None:
            raise DraftError("This draft version is not available in this profile.", 404)
        return {"id": draft_id, "version": version, "fields": json.loads(row[0])}

    def save(self, fields, draft_id=None, base_version=None):
        fields = validate_fields(fields)
        if draft_id is None:
            if base_version is not None:
                raise DraftError("base_version requires draft_id.")
            draft_id, version = "d_" + secrets.token_hex(16), 1
        else:
            if not valid_reference(draft_id, base_version) or base_version == 10000:
                raise DraftError("Revising requires draft_id and its current base_version.")
            version = base_version + 1
        with closing(self._db()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if version > 1:
                row = conn.execute(
                    "SELECT MAX(version) FROM email_drafts WHERE id = ?", (draft_id,)
                ).fetchone()
                if row[0] is None:
                    raise DraftError("This draft is not available in this profile.", 404)
                if row[0] != base_version:
                    raise DraftError(
                        f"Draft has a newer version: {row[0]}. Read it before revising.", 409
                    )
            conn.execute(
                "INSERT INTO email_drafts (id, version, fields) VALUES (?, ?, ?)",
                (draft_id, version, json.dumps(fields, ensure_ascii=False)),
            )
        return {"id": draft_id, "version": version, "fields": fields}


_DESCRIPTION = """Create or revise an email draft for the user to open in their own email app.
Use whenever asked to draft/write an email. This only saves a draft, never sends it.
Supply the complete to/cc/bcc/subject/body fields. Use plain addresses without names;
unknown recipients may be left empty. To revise, supply draft_id and base_version
from the previous result. Include the returned card verbatim in your final answer,
OUTSIDE other code fences; Wing renders it as a readable draft with an open action.
Do not reconstruct the reference or duplicate the body in Wing. Other clients may
show the returned fields as ordinary text. Never claim the email was sent.
"""
_EMAIL_FIELDS = {
    **{name: {"type": "array", "items": {"type": "string"}} for name in ("to", "cc", "bcc")},
    "subject": {"type": "string"},
    "body": {"type": "string"},
}
CREATE_SCHEMA = {
    "name": "wing_email_draft",
    "description": _DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            **_EMAIL_FIELDS,
            "draft_id": {"type": "string"},
            "base_version": {"type": "integer"},
        },
        "required": list(_EMAIL_FIELDS),
        "additionalProperties": False,
    },
}
READ_SCHEMA = {
    "name": "wing_email_draft_read",
    "description": "Read an exact Wing email draft version before revising it. Never sends email.",
    "parameters": {
        "type": "object",
        "properties": {"draft_id": {"type": "string"}, "version": {"type": "integer"}},
        "required": ["draft_id", "version"],
        "additionalProperties": False,
    },
}


def _result(record):
    reference = {key: record[key] for key in ("id", "version")}
    return json.dumps(
        {"card": "```wing-email-draft\n" + json.dumps(reference) + "\n```", **record},
        ensure_ascii=False,
    )


def create_draft(args, **_):
    try:
        record = DraftStore().save(
            {key: args.get(key) for key in _EMAIL_FIELDS},
            args.get("draft_id"),
            args.get("base_version"),
        )
        return _result(record)
    except DraftError as exc:
        return json.dumps({"error": str(exc)})


def read_draft(args, **_):
    try:
        return _result(DraftStore().read(args.get("draft_id"), args.get("version")))
    except DraftError as exc:
        return json.dumps({"error": str(exc)})
