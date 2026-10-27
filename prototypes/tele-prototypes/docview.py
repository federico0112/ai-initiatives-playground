"""Source view for a `sim://` citation: the record it resolves to, and the file it came from with
the cited lines highlighted.

Every record the engine cites carries `source: {path, line}`, so one viewer serves contracts, rate
decks, notices, invoices, usage and rating runs alike. CSV and Markdown sources cite a real line;
JSON sources cite the file, so the record's own block is located by its identifying field instead.
"""
import csv
import gzip
import os
import re

WINDOW = 14        # lines of context kept either side of the highlighted block
MAX_LINES = 400    # never render more than this from one file


def source_view(root, rec, highlight=None):
    """Returns {path, lines, total, truncated, hits} for a resolved record, or None.

    `highlight` overrides which lines are marked, for views that show a whole file rather than the
    one record a citation points at.
    """
    src = (rec or {}).get("source") or {}
    path = src.get("path")
    full = root.path(path) if path else None
    if not full or not os.path.isfile(full):
        return None
    opener = gzip.open if full.endswith(".gz") else open
    try:
        with opener(full, "rt", errors="replace") as f:
            text = [l.rstrip("\n") for l in f]
    except OSError:
        return None

    hits = set(highlight) if highlight is not None else _hits(path, rec, text, src.get("line"))
    lo, hi = _window(hits, len(text))
    lines = [{"n": i + 1, "text": text[i], "hit": (i + 1) in hits} for i in range(lo, hi)]
    return {
        "path": path,
        "total": len(text),
        "truncated": (lo, hi) != (0, len(text)),
        "hits": sorted(hits),
        "lines": lines,
        "table": _csv_table(path, text, lines) if _is_csv(path) else None,
    }


def _is_csv(path):
    return path.endswith(".csv") or path.endswith(".csv.gz")


# The columns a finding actually turns on, per source file. Marked in the header and emphasised in
# the cited row, so the reader sees which values decided it rather than scanning the whole row.
KEY_COLUMNS = {
    "rates/decks/": ("rate", "effective_date", "end_date", "change_flag"),
    "rates/deck_history.csv": ("deck_type", "received_at", "load_status", "notice_id"),
    "usage/daily_summary.csv": ("messages", "billable_segments"),
    "usage/rejects.csv": ("segments", "smpp_connection", "reject_reason", "reprocessed"),
    "usage/connection_map.csv": ("connection_id", "valid_from", "valid_to"),
}


def _key_columns(path, header):
    for pattern, cols in KEY_COLUMNS.items():
        if pattern in path:
            return [i for i, h in enumerate(header) if h.strip() in cols]
    return []


# What each column means, for a reader who does not work in carrier billing. Keyed by column name;
# the names are consistent across the files, so one entry serves every file that uses it.
COLUMN_HELP = {
    # who and where
    "mcc_mnc": "Mobile Country Code + Mobile Network Code: the global id of the destination network. 334-990 is Mexico, Altavia Movil.",
    "mcc": "Mobile Country Code: which country the destination network is in.",
    "mnc": "Mobile Network Code: which operator within that country.",
    "country": "The country of the destination network.",
    "destination": "The destination operator, in words.",
    "network_group": "Our own short code for that operator, used across our systems.",
    "supplier_id": "The carrier we hand the traffic to for delivery.",
    "customer_id": "The customer of ours whose traffic this is.",
    "sender_id": "The name or number the message is sent from.",
    "dest_masked": "The destination phone number, partly hidden for privacy.",
    # pricing
    "rate": "Price per segment, in the currency of this row.",
    "currency": "The currency the rate and amount are in.",
    "amount": "What this line comes to: segments multiplied by the rate.",
    "billing_unit": "What the rate is charged per. Segments here, not whole messages.",
    "service": "The kind of traffic this row prices. SMS throughout.",
    "effective_date": "The date this rate starts to apply. An increase also has to clear the contract's notice period before it counts.",
    "end_date": "The date this rate stops applying. Empty means it is still in force.",
    "change_flag": "What this row does to the previous price list: UNCHANGED, INCREASE, DECREASE or NEW.",
    # volumes
    "messages": "How many messages were sent. One message can be several segments.",
    "segments": "How many segments the message counts as. This is what gets charged, not the message.",
    "billable_segments": "The segments that actually price. This is the figure the charge is built from.",
    "delivered": "Messages the destination network confirmed it delivered.",
    "undelivered": "Messages the destination network said it could not deliver.",
    "expired": "Messages that timed out before the network could deliver them.",
    "no_receipt": "Messages we never got an answer about either way. Still payable: this contract bills on submit, not on delivery.",
    "total_rows": "How many price rows the deck carried.",
    # price lists
    "deck_id": "Our id for one loaded price list.",
    "rate_schedule_ref": "The family of price lists this belongs to, as named in the contract.",
    "deck_type": "full = a complete A-Z price list. partial = only the rows that changed.",
    "source_file": "The file the carrier actually sent us. Opens the price list it was loaded into.",
    "received_at": "When the carrier's file reached us. Notice periods are counted from this moment.",
    "loaded_at": "When we loaded it into our own pricing.",
    "loaded_by": "Who or what loaded it.",
    "load_status": "Whether the load succeeded.",
    "min_effective_date": "The earliest date any row in this deck takes effect.",
    "max_effective_date": "The latest date any row in this deck takes effect.",
    "notice_id": "The rate notice that announced this deck, if there was one.",
    # our own pricing run
    "run_id": "The pricing run that produced this line.",
    "date_local": "The traffic day this line prices.",
    "date_utc": "The traffic day, in UTC.",
    # connections
    "connection_id": "The SMPP connection (a 'bind') that traffic is sent over.",
    "smpp_connection": "The connection the message went out on. Traffic can only be attributed once the connection is mapped.",
    "system_id": "The login name the connection signs in with.",
    "direction": "Which way traffic flows over this connection.",
    "valid_from": "The date this connection starts being mapped to a network. Traffic sent before it cannot be attributed.",
    "valid_to": "The date the mapping ends. Empty means it is still current.",
    # message-level records
    "msg_id": "Our own id for the message.",
    "supplier_msg_id": "The carrier's id for the same message, returned when it accepted it. This is what both sides match on.",
    "submitted_at_utc": "When we handed the message to the carrier.",
    "submit_status": "What the carrier answered when we submitted it. 0x00000000 means accepted.",
    "data_coding": "The character encoding used, which decides how many characters fit in a segment.",
    "dlr_stat": "The delivery outcome the network reported. DELIVRD means it arrived.",
    "dlr_err": "The network's error code when delivery failed.",
    "dlr_received_at": "When the delivery report came back.",
    "mediation_batch_id": "The mediation batch this record was processed in.",
    "source_batch_ids": "The mediation batches these daily totals were rolled up from.",
    # rejects
    "reject_id": "Our id for one message that mediation could not process.",
    "raw_ref": "Pointer back to the raw record in mediation.",
    "reject_reason": "Why mediation could not process it. UNMAPPED_CONNECTION means the sending connection was not yet mapped to a network, so the traffic could not be attributed.",
    "rejected_at": "When mediation rejected it.",
    "reprocessed": "Whether it was later fixed and put back into our billing records. If not, our own totals are short by it.",
    # invoice lines
    "line_no": "The line number on the invoice.",
    "period_start": "First day of the period this line covers.",
    "period_end": "Last day of the period this line covers.",
}


# Cells that name another record, so the reader can open it. Per source file:
# {column that shows the name: (column holding the id, citation template)}.
LINK_COLUMNS = {
    "rates/deck_history.csv": {"source_file": ("deck_id", "sim://rates/deck/{}"),
                               "notice_id": ("notice_id", "sim://rates/notice/{}")},
    "usage/rejects.csv": {"smpp_connection": ("smpp_connection", "sim://usage/connection_map/{}")},
}


def _row_links(path, header, cells):
    """{column index: cite_id} for the cells in this row that name another record."""
    spec = next((v for k, v in LINK_COLUMNS.items() if k in path), None)
    if not spec:
        return {}
    at = {h.strip(): i for i, h in enumerate(header)}
    links = {}
    for shown, (id_col, template) in spec.items():
        i, j = at.get(shown), at.get(id_col)
        if i is None or j is None or j >= len(cells) or not cells[j].strip():
            continue
        if i < len(cells) and cells[i].strip():
            links[i] = template.format(cells[j].strip())
    return links


def _csv_table(path, text, lines):
    """A CSV read as its columns: the header row, plus the windowed rows.

    The header is taken from line 1 even when the window starts past it, so a row deep in a large
    file still shows what its columns mean. Fields are parsed, so quoted values holding commas
    (`"1,604,000"`) stay in one column.
    """
    if not text:
        return None
    header = _cells(text[0])
    rows = [{"n": l["n"], "cells": _cells(l["text"]), "hit": l["hit"]} for l in lines if l["n"] != 1]
    if not rows:
        return None
    width = max([len(header)] + [len(r["cells"]) for r in rows])
    for r in rows:                     # pad ragged rows so the columns stay aligned
        r["cells"] += [""] * (width - len(r["cells"]))
        r["links"] = _row_links(path, header, r["cells"])
    header += [""] * (width - len(header))
    key = _key_columns(path, header)
    return {"header": header, "rows": rows, "key": key,
            "help": [COLUMN_HELP.get(h.strip(), "") for h in header],
            "key_names": [header[i].replace("_", " ") for i in key]}


def _cells(line):
    try:
        return next(csv.reader([line]), [])
    except csv.Error:
        return [line]


def _window(hits, total):
    """The slice of the file to render: around the hit, or the head when there is none."""
    if not hits:
        return 0, min(total, MAX_LINES)
    lo = max(0, min(hits) - 1 - WINDOW)
    hi = min(total, max(hits) + WINDOW)
    return lo, min(hi, lo + MAX_LINES)


def _hits(path, rec, text, line):
    """1-based line numbers to highlight."""
    if path.endswith(".json"):
        return _json_block(rec, text)
    if not line or line > len(text):
        return set()
    hits = {line}
    # A Markdown clause cites its anchor; the clause itself is the next non-empty line.
    if path.endswith(".md") and not text[line - 1].strip().startswith(("#", "|")):
        for i in range(line, min(line + 3, len(text))):
            if text[i].strip():
                hits.add(i + 1)
                break
    return hits


def _json_block(rec, text):
    """The object in a pretty-printed JSON file that holds this record, found by its first field."""
    key = next((k for k in rec if k not in ("cite_id", "source")), None)
    if key is None:
        return set()
    value = rec[key]
    if isinstance(value, (dict, list)):
        return set()
    want = re.compile(r'"%s"\s*:\s*"?%s"?\s*[,}]' % (re.escape(key), re.escape(str(value))))
    at = next((i for i, l in enumerate(text) if want.search(l)), None)
    if at is None:
        return set()
    return {i + 1 for i in _enclosing_object(text, at)}


def _enclosing_object(text, at):
    """Range of the `{ ... }` block containing line `at`, by brace depth."""
    start, depth = at, 0
    while start > 0:
        depth += text[start].count("}") - text[start].count("{")
        if depth < 0:
            break
        start -= 1
    end, depth = at, 0
    while end < len(text) - 1:
        depth += text[end].count("{") - text[end].count("}")
        if depth < 0:
            break
        end += 1
    return range(start, end + 1)
