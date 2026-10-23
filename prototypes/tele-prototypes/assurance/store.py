"""Read-only adapters over the simulated systems S1-S5, plus the writable stores S6 (cases) and
S7 (carrier portal). Signatures follow simulated-systems.md section 7, so a real connector can
replace one adapter later without touching the steps.

Every record an adapter returns is a dict with a `cite_id` (sim://...) and `source` {path, line}.
Writes never touch the scenario folders: they go to a per-run workspace, which is also where the
scenario runner delivers files (so a delivered file shadows nothing and the next run starts clean).
"""
import csv
import gzip
import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

D = Decimal
SUBMIT_OK = "0x00000000"


def q2(x):
    return D(x).quantize(D("0.01"), rounding=ROUND_HALF_UP)


def money(x):
    """Amounts as text: thousands separator, 2 decimals (the ledger checks these against calcs)."""
    return f"{q2(x):,.2f}"


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class DataRoot:
    """One scenario's data, with the overlay rule from simulated-systems.md section 9: workspace
    first, then the variant folder, then base unless the path is listed in overlay `deleted`."""

    def __init__(self, scenario_dir, workspace):
        self.dir = os.path.abspath(scenario_dir)
        self.workspace = os.path.abspath(workspace)
        self.base, self.deleted = None, set()
        ov = os.path.join(self.dir, "overlay.json")
        if os.path.exists(ov):
            o = json.load(open(ov))
            dataset_dir = os.path.dirname(os.path.dirname(self.dir))
            self.base = os.path.join(dataset_dir, o["inherits"])
            self.deleted = set(o["deleted"])

    def _layers(self):
        return [self.workspace, self.dir] + ([self.base] if self.base else [])

    def path(self, rel):
        if os.path.exists(os.path.join(self.workspace, rel)):
            return os.path.join(self.workspace, rel)
        if rel in self.deleted:
            return None
        for layer in self._layers()[1:]:
            p = os.path.join(layer, rel)
            if os.path.exists(p):
                return p
        return None

    def listdir(self, rel):
        names = set()
        for layer in self._layers():
            p = os.path.join(layer, rel)
            if os.path.isdir(p):
                names.update(os.listdir(p))
        return sorted(n for n in names if self.path(os.path.join(rel, n)) is not None)

    def write_path(self, rel):
        p = os.path.join(self.workspace, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def json(self, rel):
        p = self.path(rel)
        return None if p is None else json.load(open(p))

    def csv(self, rel):
        """Rows as dicts with their 1-based file line number under `_line`."""
        p = self.path(rel)
        if p is None:
            return None
        opener = gzip.open if p.endswith(".gz") else open
        with opener(p, "rt", newline="") as f:
            return [dict(r, _line=i + 2) for i, r in enumerate(csv.DictReader(f))]


def _rec(row, cite_id, rel, line=None):
    r = {k: v for k, v in row.items() if k != "_line"}
    r["cite_id"] = cite_id
    r["source"] = {"path": rel, "line": line if line is not None else row.get("_line")}
    return r


# ---------------------------------------------------------------- S1 contract repository
class Contracts:
    def __init__(self, root):
        self.root = root

    def get_agreement(self, agreement_id):
        rel = f"contracts/agreements/{agreement_id}.json"
        a = self.root.json(rel)
        return None if a is None else _rec(a, f"sim://contracts/agreement/{agreement_id}", rel, 1)

    def find_by_ref(self, document_number):
        for name in self.root.listdir("contracts/agreements"):
            a = self.get_agreement(name[:-5])
            if a and a["document_number"] == document_number:
                return a
        return None

    def clause_cite(self, agreement_id, clause_id):
        return f"sim://contracts/agreement/{agreement_id}#clause-{clause_id}"

    def get_clause(self, agreement_id, clause_id):
        a = self.get_agreement(agreement_id)
        rel = f"contracts/documents/{agreement_id}.md"
        line = None
        p = self.root.path(rel)
        if p:
            for i, text in enumerate(open(p), 1):
                if f'id="clause-{clause_id}"' in text:
                    line = i
        c = next(c for c in a["clauses"] if c["clause_id"] == clause_id)
        return _rec(c, self.clause_cite(agreement_id, clause_id), rel, line)


# ---------------------------------------------------------------- S2 rate deck store
class Rates:
    def __init__(self, root):
        self.root = root

    def list_decks(self, rate_schedule_ref):
        rows = self.root.csv("rates/deck_history.csv")
        return [_rec(h, f"sim://rates/deck_history/{h['deck_id']}", "rates/deck_history.csv")
                for h in rows if h["rate_schedule_ref"] == rate_schedule_ref]

    def get_deck_rows(self, deck_id, network_group=None):
        rel = f"rates/decks/{deck_id}.csv"
        rows = self.root.csv(rel)
        if rows is None:
            return None
        return [_rec(dict(r, deck_id=deck_id), f"sim://rates/deck/{deck_id}/row/{r['mcc_mnc']}", rel)
                for r in rows if network_group is None or r["network_group"] == network_group]

    def get_notice(self, notice_id):
        rel = f"rates/notices/{notice_id}.json"
        n = self.root.json(rel)
        return None if n is None else _rec(n, f"sim://rates/notice/{notice_id}", rel, 1)

    def group_map(self, rate_schedule_ref):
        """mcc_mnc -> network_group from every deck on file."""
        out = {}
        for h in self.list_decks(rate_schedule_ref):
            for r in self.get_deck_rows(h["deck_id"]) or []:
                out[r["mcc_mnc"]] = r["network_group"]
        return out

    def contracted_rate(self, agreement, mcc_mnc, day):
        """Spec S2 rule for a local billing day: exact MCC-MNC, later received wins, and an
        INCREASE or NEW row only applies from the first full day (billing timezone) that starts at
        least `rate_notice_days_increase` days after its notice was sent. Returns
        {row, valid_from, notice} or None."""
        tz = ZoneInfo(agreement["billing_timezone"])
        cands = []
        for h in self.list_decks(agreement["rate_schedule_ref"]):
            if h["load_status"] != "loaded":
                continue
            for r in self.get_deck_rows(h["deck_id"]) or []:
                end = date.fromisoformat(r["end_date"]) if r["end_date"] else date.max
                if r["mcc_mnc"] == mcc_mnc and date.fromisoformat(r["effective_date"]) <= day < end:
                    cands.append((h["received_at"], r, h))
        for _received, r, h in sorted(cands, key=lambda c: c[0], reverse=True):
            valid_from, notice = date.fromisoformat(r["effective_date"]), None
            if r["change_flag"] in ("INCREASE", "NEW"):
                notice = self.get_notice(h["notice_id"]) if h["notice_id"] else None
                sent = parse_dt(notice["sent_at"] if notice else h["received_at"]).astimezone(tz)
                earliest = sent + timedelta(days=agreement["rate_notice_days_increase"])
                first_full = earliest.date() + timedelta(days=0 if earliest.time() == datetime.min.time() else 1)
                valid_from = max(valid_from, first_full)
                if day < valid_from:
                    continue
            return {"row": r, "valid_from": valid_from, "notice": notice}
        return None


# ---------------------------------------------------------------- S3 usage store (mediation)
_ROLLUP_CACHE = {}


class Usage:
    def __init__(self, root):
        self.root = root

    def message_files(self, start, end):
        out = []
        d = start
        while d <= end:
            rel = f"usage/messages/{d.isoformat()}.csv.gz"
            if self.root.path(rel):
                out.append(rel)
            d += timedelta(days=1)
        return out

    def messages(self, carrier_id, start, end, mcc_mncs=None):
        """Iterates message records (as dicts, with cite_id) for the carrier in [start, end]."""
        for rel in self.message_files(start, end):
            with gzip.open(self.root.path(rel), "rt", newline="") as f:
                rd = csv.reader(f)
                head = next(rd)
                for i, row in enumerate(rd, 2):
                    r = dict(zip(head, row))
                    if r["supplier_id"] == carrier_id and (mcc_mncs is None or r["mcc_mnc"] in mcc_mncs):
                        yield _rec(r, f"sim://usage/message/{r['msg_id']}", rel, i)

    def rollup(self, carrier_id, start, end, group_map):
        """Message records rolled up by (date_utc, network_group), plus duplicate supplier_msg_id
        and non-accepted submit counts. Cached per file set: base holds 1.35M records."""
        files = tuple(self.root.path(rel) for rel in self.message_files(start, end))
        key = (files, carrier_id, tuple(sorted(group_map.items())))
        if key not in _ROLLUP_CACHE:
            agg, seen = {}, set()
            dups = bad_status = n = 0
            dlr_idx = {"DELIVRD": 2, "UNDELIV": 3, "EXPIRED": 4, "": 5}
            for p in files:
                with gzip.open(p, "rt", newline="") as f:
                    rd = csv.reader(f)
                    h = {k: i for i, k in enumerate(next(rd))}
                    for row in rd:
                        if row[h["supplier_id"]] != carrier_id:
                            continue
                        n += 1
                        k = (row[h["submitted_at_utc"]][:10], group_map.get(row[h["mcc_mnc"]], "UNMAPPED"))
                        a = agg.setdefault(k, [0, 0, 0, 0, 0, 0])
                        a[0] += 1
                        a[1] += int(row[h["segments"]])
                        a[dlr_idx[row[h["dlr_stat"]]]] += 1
                        bad_status += row[h["submit_status"]] != SUBMIT_OK
                        sid = row[h["supplier_msg_id"]]
                        dups += sid in seen
                        seen.add(sid)
            _ROLLUP_CACHE[key] = {"by_day_group": agg, "duplicates": dups, "bad_status": bad_status,
                                  "messages": n, "files": [os.path.relpath(p, self.root.dir) for p in files]}
        return _ROLLUP_CACHE[key]

    def daily_summary(self, carrier_id, start, end):
        rows = self.root.csv("usage/daily_summary.csv")
        return [_rec(r, f"sim://usage/daily/{r['date_utc']}/{r['network_group']}", "usage/daily_summary.csv")
                for r in rows if r["supplier_id"] == carrier_id and start.isoformat() <= r["date_utc"] <= end.isoformat()]

    def rejects(self, carrier_id, start, end, connection_map):
        """Rejects whose bind belongs to the carrier at any time (a reject on an unmapped bind is
        exactly the case where today's map is wrong for the reject's date)."""
        binds = {c["connection_id"] for c in connection_map if c["supplier_id"] == carrier_id}
        rows = self.root.csv("usage/rejects.csv")
        return [_rec(r, f"sim://usage/reject/{r['reject_id']}", "usage/rejects.csv") for r in rows
                if r["smpp_connection"] in binds and start.isoformat() <= r["submitted_at_utc"][:10] <= end.isoformat()]

    def connection_map(self):
        rows = self.root.csv("usage/connection_map.csv")
        return [_rec(r, f"sim://usage/connection_map/{r['connection_id']}", "usage/connection_map.csv") for r in rows]


# ---------------------------------------------------------------- S4 rated charges
class Rating:
    def __init__(self, root):
        self.root = root

    def get_run(self, carrier_id, period_start, period_end):
        """The complete run covering the period, else the latest partial one."""
        found = []
        for name in self.root.listdir("rating/runs"):
            if name.endswith(".json"):
                rel = f"rating/runs/{name}"
                r = self.root.json(rel)
                if r["carrier_id"] == carrier_id and r["period_start"] <= period_start and r["period_end"] >= period_end:
                    found.append(_rec(r, f"sim://rating/run/{r['run_id']}", rel, 1))
        found.sort(key=lambda r: (r["status"] == "complete", r["run_at"]))
        return found[-1] if found else None

    def lines(self, run_id):
        rel = f"rating/runs/{run_id}/lines.csv"
        return [_rec(r, f"sim://rating/run/{run_id}/line/{r['date_local']}/{r['mcc_mnc']}", rel)
                for r in self.root.csv(rel) or []]


# ---------------------------------------------------------------- S5 carrier invoice inbox
class Invoices:
    def __init__(self, root):
        self.root = root

    def poll_inbox(self, since=None):
        out = []
        for inv_id in self.root.listdir("invoices/inbox"):
            rel = f"invoices/inbox/{inv_id}/envelope.json"
            e = self.root.json(rel)
            if e and (since is None or parse_dt(e["received_at"]) >= since):
                out.append(_rec(dict(e, invoice_id=inv_id), f"sim://invoices/envelope/{inv_id}", rel, 1))
        return out

    def get_invoice(self, invoice_id):
        rel = f"invoices/inbox/{invoice_id}/invoice.json"
        inv = self.root.json(rel)
        rec = _rec(inv, f"sim://invoices/invoice/{invoice_id}", rel, 1)
        rec["lines"] = [_rec(l, f"sim://invoices/invoice/{invoice_id}/line/{l['line_no']}", rel, 1)
                        for l in inv["lines"]]
        return rec

    def detail_rel(self, invoice_id):
        return f"invoices/inbox/{invoice_id}/message_detail.csv.gz"

    def get_carrier_detail(self, invoice_id):
        """Carrier message detail records, or None if the carrier has not supplied them."""
        rel = self.detail_rel(invoice_id)
        rows = self.root.csv(rel)
        if rows is None:
            return None
        return [_rec(r, f"sim://invoices/invoice/{invoice_id}/detail/{r['carrier_record_id']}", rel) for r in rows]


# ---------------------------------------------------------------- S6 case store
class Cases:
    def __init__(self, root):
        self.root = root

    def _rel(self, case_id):
        return f"cases/{case_id}.json"

    def next_id(self):
        n = len([f for f in self.root.listdir("cases") if f.endswith(".json")])
        return f"CASE-{n + 1:04d}"

    def get(self, case_id):
        return self.root.json(self._rel(case_id))

    def save(self, case, at, text):
        """Writes the case and appends one history line (status, sub_status, text)."""
        case["cite_id"] = f"sim://cases/case/{case['case_id']}"
        json.dump(case, open(self.root.write_path(self._rel(case["case_id"])), "w"), indent=2)
        with open(self.root.write_path(f"cases/{case['case_id']}.history.jsonl"), "a") as f:
            f.write(json.dumps({"at": at, "status": case["status"], "sub_status": case["sub_status"],
                                "text": text}) + "\n")

    def history(self, case_id):
        p = self.root.path(f"cases/{case_id}.history.jsonl")
        return [json.loads(l) for l in open(p)] if p else []


# ---------------------------------------------------------------- S7 carrier dispute portal
class Portal:
    def __init__(self, root):
        self.root = root

    def submit(self, submission):
        """Analyst-triggered only (the runner calls this after the step 7 approval)."""
        n = len([f for f in self.root.listdir("carrier-portal/submissions") if f.endswith(".json")])
        claim_id = f"NB-DSP-{98341 + n:07d}"
        sub = dict(submission, claim_id=claim_id, cite_id=f"sim://carrier-portal/submission/{claim_id}")
        json.dump(sub, open(self.root.write_path(f"carrier-portal/submissions/{claim_id}.json"), "w"), indent=2)
        return sub

    def add_response(self, response):
        rel = f"carrier-portal/responses/{response['claim_id']}.json"
        existing = self.root.json(rel) or []
        response = dict(response, cite_id=f"sim://carrier-portal/response/{response['claim_id']}/{len(existing) + 1}")
        json.dump(existing + [response], open(self.root.write_path(rel), "w"), indent=2)
        return response

    def responses(self, claim_id):
        return self.root.json(f"carrier-portal/responses/{claim_id}.json") or []


class Systems:
    """All adapters over one data root, plus resolve(cite_id)."""

    def __init__(self, root):
        self.root = root
        self.contracts, self.rates, self.usage = Contracts(root), Rates(root), Usage(root)
        self.rating, self.invoices = Rating(root), Invoices(root)
        self.cases, self.portal = Cases(root), Portal(root)
        self._index = None
        self._detail = {}

    def _build_index(self):
        """cite_id -> record for every file-backed record type except message records and
        carrier detail (those are resolved by scanning on demand)."""
        idx = {}
        r = self.root
        for name in r.listdir("contracts/agreements"):
            a = self.contracts.get_agreement(name[:-5])
            idx[a["cite_id"]] = a
            for c in a["clauses"]:
                cl = self.contracts.get_clause(a["agreement_id"], c["clause_id"])
                idx[cl["cite_id"]] = cl
        for h in self.root.csv("rates/deck_history.csv") or []:
            idx[f"sim://rates/deck_history/{h['deck_id']}"] = _rec(h, "", "rates/deck_history.csv")
            for row in self.rates.get_deck_rows(h["deck_id"]) or []:
                idx[row["cite_id"]] = row
            if h["notice_id"] and self.rates.get_notice(h["notice_id"]):
                n = self.rates.get_notice(h["notice_id"])
                idx[n["cite_id"]] = n
        for row in r.csv("usage/daily_summary.csv") or []:
            idx[f"sim://usage/daily/{row['date_utc']}/{row['network_group']}"] = _rec(row, "", "usage/daily_summary.csv")
        for row in r.csv("usage/rejects.csv") or []:
            idx[f"sim://usage/reject/{row['reject_id']}"] = _rec(row, "", "usage/rejects.csv")
        for c in self.usage.connection_map():
            idx[c["cite_id"]] = c
        for name in r.listdir("rating/runs"):
            if name.endswith(".json"):
                run = r.json(f"rating/runs/{name}")
                idx[f"sim://rating/run/{run['run_id']}"] = _rec(run, "", f"rating/runs/{name}", 1)
                for l in self.rating.lines(run["run_id"]):
                    idx[l["cite_id"]] = l
        for e in self.invoices.poll_inbox():
            inv = self.invoices.get_invoice(e["invoice_id"])
            idx[inv["cite_id"]] = inv
            for l in inv["lines"]:
                idx[l["cite_id"]] = l
        for k, v in idx.items():
            v["cite_id"] = k
        self._index = idx

    def resolve(self, cite_id, ledger=None):
        """Record plus source for a cite_id, or None if it does not resolve."""
        if cite_id.startswith("sim://ledger/calc/"):
            return ledger.get_calc(cite_id.rsplit("/", 1)[1]) if ledger else None
        if cite_id.startswith("sim://cases/case/"):
            return self.cases.get(cite_id.rsplit("/", 1)[1])
        if "/detail" in cite_id:
            inv_id = cite_id.split("/")[4]
            if inv_id not in self._detail:
                rows = self.invoices.get_carrier_detail(inv_id) or []
                self._detail[inv_id] = {d["cite_id"]: d for d in rows}
                if rows:
                    self._detail[inv_id][f"sim://invoices/invoice/{inv_id}/detail"] = {
                        "rows": len(rows), "source": {"path": self.invoices.detail_rel(inv_id), "line": None}}
            return self._detail[inv_id].get(cite_id)
        if self._index is None:
            self._build_index()
        return self._index.get(cite_id)
