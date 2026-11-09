"""Typed host-connector adapters; no credentials, network clients, bodies or model calls."""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "setup-emailbutler" / "scripts"))
import private_state as b


class ConnectorFailure(Exception):
    def __init__(self, status=None, retry_after=None):
        super().__init__("Connector operation failed")
        self.status = status
        self.retry_after = retry_after


def payload(value):
    b.require(type(value) is dict, "Expected a structured connector response")
    if value.get("isError") or "error" in value or (str(value.get("status", "")).isdigit() and int(value["status"]) >= 400):
        error = value.get("error", {})
        error = error if type(error) is dict else {}
        code = error.get("status", error.get("status_code", error.get("code", value.get("status"))))
        inner = error.get("innerError", error.get("innererror", {}))
        if type(inner) is dict and not str(code).isdigit():
            code = inner.get("status", inner.get("code", code))
        status = int(code) if str(code).isdigit() else ({"TooManyRequests": 429, "ErrorAccessDenied": 403, "InvalidAuthenticationToken": 401}.get(code) if type(code) is str else None)
        retry = error.get("retry_after", error.get("retryAfter", value.get("retry_after")))
        if retry is None:
            headers = value.get("headers", error.get("headers", {}))
            if type(headers) is dict:
                retry = headers.get("Retry-After", headers.get("retry-after"))
        if retry is not None:
            try:
                retry = float(retry)
            except (ValueError, TypeError):
                from email.utils import parsedate_to_datetime
                from datetime import datetime, timezone
                try:
                    retry = max(0, (parsedate_to_datetime(str(retry)) - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    retry = float("inf")
        raise ConnectorFailure(status, retry)
    if value.get("success") is False or value.get("ok") is False:
        raise ConnectorFailure()
    for key in ("data", "result", "profile", "message"):
        if type(value.get(key)) is dict:
            return payload(value[key])
    return value


def identity(value, expected):
    value = payload(value)
    candidates = [value.get(key) for key in ("emailAddress", "email_address", "email", "mail", "userPrincipalName", "user_principal_name")]
    candidates = [v.get("address") if type(v) is dict else v for v in candidates]
    b.require(expected.casefold() in {v.casefold() for v in candidates if type(v) is str}, "Connected account does not match the approved scope")


def collection(value, key="value"):
    value = payload(value)
    rows = value.get(key, value.get("messages", value.get("items")))
    b.require(type(rows) is list and all(type(row) is dict for row in rows), "Unsupported connector collection shape")
    return rows


def field(value, camel, snake):
    return value.get(camel, value.get(snake))


def groups_by_size(rows, limit, identity):
    groups, current, size = [], [], 0
    for row in rows:
        weight = len(json.dumps(identity(row)).encode()) + 32
        b.require(weight <= 4500, "One connector identity exceeds the protocol bound")
        if current and (len(current) >= limit or size + weight > 4500):
            groups.append(current)
            current, size = [], 0
        current.append(row)
        size += weight
    if current:
        groups.append(current)
    return groups


class Outlook:
    provider = "outlook"
    def __init__(self, scope, keys, read_limit):
        b.require(scope["action"] in {"move", "archive"}, "Outlook execution requires an exact folder move")
        b.require(scope["source"] != scope["destination"], "Source and destination must differ")
        b.require(scope["action"] != "archive" or scope["destination"] == "archive", "Archive must use the explicitly approved archive destination")
        self.scope, self.keys, self.read_limit = scope, keys, read_limit

    def check(self, call):
        identity(call("outlook.profile", {}), self.scope["account"])

    def inspect(self, call, rows, destination_first):
        result = {}
        folders = [("verified", self.scope["destination"]), ("pending", self.scope["source"])]
        if not destination_first:
            folders.reverse()
        for status, folder in folders:
            missing = rows
            for group in groups_by_size(missing, self.read_limit, lambda row: self.keys[row["id"]].replace("'", "''")):
                expected = {self.keys[row["id"]]: row["id"] for row in group}
                query = " or ".join("internetMessageId eq '" + key.replace("'", "''") + "'" for key in expected)
                seen, cursor = set(), 0
                while True:
                    response = call("outlook.list", {"folder_id": folder, "filter": query, "select": ["internet_message_id", "parent_folder_id"], "top": len(group), "skip": cursor})
                    for item in collection(response):
                        key = field(item, "internetMessageId", "internet_message_id")
                        current_id = field(item, "id", "message_id")
                        b.require(key in expected and key not in seen and expected[key] not in result, "Ambiguous or unexpected Outlook identity in readback")
                        b.text_value(current_id, "current message identity", 1000)
                        parent = field(item, "parentFolderId", "parent_folder_id")
                        b.require(parent is None or parent == folder or folder in {"archive", "inbox"}, "Connector returned a message from another folder")
                        seen.add(key)
                        result[expected[key]] = {"id": expected[key], "status": status, "result_id": current_id, "evidence": "Independent folder-scoped metadata read by frozen Internet Message-ID"}
                    body = payload(response)
                    b.require(type(body.get("has_more")) is bool, "Readback lacks complete pagination metadata")
                    if not body["has_more"]:
                        b.require(not body.get("@odata.nextLink"), "Readback continuation metadata conflicts")
                        break
                    next_cursor = body.get("next_from_index")
                    b.require(type(next_cursor) is int and next_cursor > cursor, "Readback pagination did not advance")
                    cursor = next_cursor
        b.require(len(result) == len(rows), "Some frozen messages cannot be located unambiguously")
        return list(result.values())

    def groups(self, rows, write_limit):
        return [[row] for row in rows]

    def write(self, call, rows):
        args = {"message_id": rows[0]["result_id"]}
        args["destination_well_known_folder" if self.scope["action"] == "archive" else "destination_folder_id"] = self.scope["destination"]
        result = payload(call("outlook.move", args))
        b.text_value(field(result, "id", "message_id"), "returned move identity", 1000)
        return result


class Gmail:
    provider = "gmail"
    def __init__(self, scope, keys, read_limit):
        b.require(scope["source"].casefold() == "inbox", "Gmail execution currently supports an Inbox source")
        b.require(scope["action"] != "archive" or scope["destination"] == "archive", "Archive scope must name archive as destination")
        self.scope = scope

    def check(self, call):
        identity(call("gmail.profile", {}), self.scope["account"])
        if self.scope["action"] != "archive":
            labels = collection(call("gmail.labels", {}), "labels")
            matches = [row for row in labels if row.get("id") == self.scope["destination"]]
            b.require(len(matches) == 1 and matches[0].get("type") == "user", "Destination must be one existing user label ID")

    def inspect(self, call, rows, destination_first):
        results = []
        for row in rows:
            item = payload(call("gmail.read", {"message_id": row["id"], "format": "minimal"}))
            b.require(item.get("id") == row["id"], "Gmail readback returned a different message")
            labels = field(item, "labelIds", "label_ids")
            b.require(type(labels) is list and all(type(label) is str for label in labels), "Gmail label metadata is unavailable")
            b.require(not {"TRASH", "SPAM"}.intersection(labels), "Message is outside the approved filing route")
            present = self.scope["destination"] in labels
            done = (self.scope["action"] == "archive" or present) and (self.scope["action"] == "label" or "INBOX" not in labels)
            b.require(done or "INBOX" in labels, "Message left the approved source without reaching its destination")
            results.append({"id": row["id"], "status": "verified" if done else "pending", "result_id": row["id"], "evidence": "Independent minimal Gmail label-state read"})
        return results

    def groups(self, rows, write_limit):
        return groups_by_size(rows, write_limit, lambda row: row["id"])

    def write(self, call, rows):
        args = {"message_ids": [row["id"] for row in rows], "add_labels": [] if self.scope["action"] == "archive" else [self.scope["destination"]], "remove_labels": [] if self.scope["action"] == "label" else ["INBOX"]}
        result = call("gmail.modify", args)
        b.require(not result or result.get("success") is True or result.get("ok") is True or result.get("status") in {200, 204}, "Unsupported Gmail write response")
        return result


def adapter(scope, keys, read_limit):
    return {"gmail": Gmail, "outlook": Outlook}[scope["provider"]](scope, keys, read_limit)
