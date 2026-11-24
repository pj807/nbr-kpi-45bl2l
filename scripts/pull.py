"""NBR KPI dashboard: read GHL (read-only), compute totals, write data.json and report.txt.

Location: JResmiMkYCKiyhmQ3Aa3 (NBR) only. The WUW location is refused.
Token: env GHL_TOKEN (GitHub Action secret) or macOS Keychain item "ghl-nbr-kpi" (your Mac). Never printed.
READ-ONLY GUARD: every call goes through API.req(), which refuses anything that is not a GET
(the only exception is POST /contacts/search, a read-only search). Nothing in GHL is ever created,
edited, saved, published, paused, scheduled or sent.

Sources, in order:
  1. GHL API (token present)                         -> payments, buyer access, campaigns, email per day, clicks per send
  2. data/raw/bootstrap_api_*.json (Mac only)         -> the same data read once in Chrome before the token existed
  3. neither                                          -> keeps the API sections of the last data.json, marked stale
UI-only numbers (provider blocks, complaints/unsubscribes per day, GHL sending notice) come from ui.json,
which ./refresh.sh updates from a read-only Chrome read on the Mac. They carry their own "read at" time.

Only totals are written. Per-contact data stays in memory (or in data/raw/ on the Mac, never committed).
"""
import glob, hashlib, json, os, re, subprocess, sys, time
import urllib.request, urllib.parse, urllib.error
from datetime import date as Date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
TZ = ZoneInfo(CFG["timezone"])
LOC = CFG["location_id"]
BASE = "https://services.leadconnectorhq.com"
KEEP = "Nothing published; the page keeps the last good numbers."
ON_ACTIONS = bool(os.environ.get("GITHUB_ACTIONS"))

FORBIDDEN = set(CFG.get("forbidden_location_sha256", []))


def is_forbidden(text):
    return any(hashlib.sha256(w.encode()).hexdigest() in FORBIDDEN for w in re.findall(r"[A-Za-z0-9]{20}", text or ""))


if LOC != "JResmiMkYCKiyhmQ3Aa3" or is_forbidden(LOC):
    raise SystemExit("Stopped: config.json location_id is not the NBR location. Nothing read.")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def pct(a, b, nd=1):
    return round(100.0 * a / b, nd) if b else None


def money(n):
    return f"${n:,.0f}"


def et(dt):
    return dt.astimezone(TZ)


def parse(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def short_day(d):
    d = Date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d.strftime('%a %b')} {d.day}"


def iso_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ------------------------------------------------------------------ read-only API
READ_ONLY_POSTS = {"/contacts/search"}        # searches only; never a write


class NotReadOnly(Exception):
    pass


def token():
    if os.environ.get("GHL_TOKEN", "").strip():
        return os.environ["GHL_TOKEN"].strip()
    if ON_ACTIONS:
        return None
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", "ghl-nbr-kpi", "-w"], capture_output=True, text=True)
        return r.stdout.strip() or None
    except FileNotFoundError:
        return None


def where(path):   # no IDs in the public run log
    return re.sub(r"/[A-Za-z0-9-]{16,}", "/…", path.split("?")[0])


class API:
    def __init__(self, tok):
        self.tok = tok

    def req(self, method, path, params=None, body=None, version="2021-07-28"):
        method = method.upper()
        # READ-ONLY GUARD (hard rule): GET only, plus listed read-only searches.
        if method != "GET" and not (method == "POST" and path in READ_ONLY_POSTS):
            raise NotReadOnly(f"Refused {method} {where(path)}: this script only reads GHL.")
        blob = json.dumps([path, params, body])
        if is_forbidden(blob):
            raise NotReadOnly("Refused: the request names a non-NBR (WUW) location.")
        if "locationId" in (params or {}) and params["locationId"] != LOC or "altId" in (params or {}) and params["altId"] != LOC:
            raise NotReadOnly("Refused: the request is for a location other than NBR.")
        url = BASE + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        for i in range(6):
            r = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": "Bearer " + self.tok, "Version": version, "Accept": "application/json",
                "Content-Type": "application/json", "User-Agent": "nbr-kpi/1.0"})
            try:
                with urllib.request.urlopen(r, timeout=120) as resp:
                    return json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(3 * (i + 1)); continue
                if e.code in (401, 403):
                    raise SystemExit(f"Stopped: GHL refused the key (HTTP {e.code}) reading {where(path)}. "
                                     f"The GHL_TOKEN may be missing a read scope (see README). {KEEP}")
                raise SystemExit(f"Stopped: GHL returned HTTP {e.code} reading {where(path)}. {KEEP}")
            except urllib.error.URLError:
                time.sleep(3 * (i + 1))
        raise SystemExit(f"Stopped: no answer from GHL for {where(path)} after 6 tries. {KEEP}")

    def get(self, path, params=None, version="2021-07-28"):
        return self.req("GET", path, params, version=version)

    # --- reads used by the dashboard
    def transactions(self, since):
        out, off = [], 0
        while True:
            rows = self.get("/payments/transactions", {"altId": LOC, "altType": "location", "limit": 100, "offset": off}).get("data", [])
            out += rows
            if len(rows) < 100 or (rows and parse(rows[-1]["createdAt"]) < since):
                return [t for t in out if parse(t["createdAt"]) >= since]
            off += 100

    def order(self, oid):
        return self.get(f"/payments/orders/{oid}", {"altId": LOC, "altType": "location"})

    def contact_tags(self, cid):
        c = self.get(f"/contacts/{cid}")
        return set((c.get("contact") or c).get("tags") or [])

    def messages(self, since, until=None, contact=None):
        out, cur = [], None
        while True:
            p = {"locationId": LOC, "channel": "Email", "limit": 1000, "startDate": iso_utc(since), "sortOrder": "asc"}
            if until:
                p["endDate"] = iso_utc(until)
            if contact:
                p["contactId"] = contact
            if cur:
                p["cursor"] = cur
            r = self.get("/conversations/messages/export", p)
            ms = r.get("messages", [])
            out += ms
            cur = r.get("nextCursor")
            if not cur or not ms:
                return out

    def campaigns(self):
        out, off = [], 0
        while off < 1000:
            r = self.get("/emails/schedule", {"locationId": LOC, "limit": 100, "offset": off})
            rows = r.get("schedules") or r.get("data") or []
            out += rows
            if len(rows) < 100:
                return out
            off += 100
        return out

    def workflows(self):
        return self.get("/workflows/", {"locationId": LOC}).get("workflows", [])


# ------------------------------------------------------------------ helpers
def brand_of(name):
    n = (name or "").lower()
    for prefix, b in CFG["brands"]["rules"]:
        if n.startswith(prefix.lower()):
            return b
    return "Other"


def access_rule_for(product):
    p = (product or "").lower()
    for a in CFG["access_checks"]["products"]:
        if any(p.startswith(x.lower()) for x in a["products"]):
            return a
    return None


def norm(s):
    return re.sub(r"\s+", " ", (s or "").replace("’", "'")).strip().lower()


EMAIL_STATUS = {   # GHL message export status -> dashboard bucket (verify on the first token run; unknown ones are logged)
    "delivered": "delivered", "opened": "delivered", "clicked": "delivered", "read": "delivered",
    "bounced": "bounced", "failed": "failed", "undelivered": "failed",
    "sent": "pending", "pending": "pending", "scheduled": "pending", "queued": "pending",
}


# ------------------------------------------------------------------ source 1: API
def from_api(api, now):
    since30 = (now - timedelta(days=31)).replace(hour=0, minute=0, second=0, microsecond=0)
    win_hours = CFG["access_checks"]["within_hours"]
    seven_start = datetime.combine(now.date() - timedelta(days=7), datetime.min.time(), TZ)
    pays, failed = [], 0
    for t in api.transactions(since30):
        if not t.get("liveMode", True):
            continue
        if t.get("status") != "succeeded":
            failed += t.get("status") == "failed"
            continue
        items = []
        if t.get("entityType") == "order" and t.get("entityId"):
            o = api.order(t["entityId"])
            if o.get("markAsTest"):
                continue
            for i in o.get("items", []):
                pr = i.get("price") or {}
                items.append([(i.get("product") or {}).get("name") or i.get("name") or "", i.get("qty") or i.get("quantity") or 1, pr.get("amount") or 0])
        else:
            items.append([t.get("entitySourceName") or "Other", 1, t.get("amount") or 0])
        at = et(parse(t["createdAt"]))
        p = {"at": at.isoformat(), "amount": float(t.get("amount") or 0) - float(t.get("amountRefunded") or 0), "items": items, "access": {}}
        # buyer access check: last 7 days, paid orders only
        if at >= seven_start and p["amount"] > 0:
            cid = t.get("contactId")
            for name, _, _ in items:
                rule = access_rule_for(name)
                if not rule or rule["key"] in p["access"]:
                    continue
                proof = None
                if rule["proof_subjects"] and cid:
                    want = {norm(s) for s in rule["proof_subjects"]}
                    for m in api.messages(at - timedelta(minutes=5), at + timedelta(hours=win_hours), contact=cid):
                        if m.get("direction") == "outbound" and norm(m.get("subject")) in want:
                            proof = "email"; break
                if not proof and rule["proof_tags"] and cid:
                    if set(rule["proof_tags"]) & api.contact_tags(cid):
                        proof = "tag"
                p["access"][rule["key"]] = proof     # None = not matched
        pays.append(p)

    # email per day (last 14 days) and clicks per send (last 7 days)
    start14 = datetime.combine(now.date() - timedelta(days=14), datetime.min.time(), TZ)
    days, sends, unknown = {}, {}, {}
    for m in api.messages(start14, now):
        if m.get("direction") != "outbound":
            continue
        d = et(parse(m.get("dateAdded"))).date().isoformat()
        b = EMAIL_STATUS.get((m.get("status") or "").lower())
        if b is None:
            unknown[m.get("status")] = unknown.get(m.get("status"), 0) + 1
            b = "pending"
        x = days.setdefault(d, {"sent": 0, "delivered": 0, "bounced": 0, "failed": 0, "opened": 0, "clicked": 0})
        if b == "failed":
            x["failed"] += 1
        else:
            x["sent"] += 1
            if b == "delivered":
                x["delivered"] += 1
            if b == "bounced":
                x["bounced"] += 1
            st = (m.get("status") or "").lower()
            x["opened"] += st in ("opened", "clicked", "read")
            x["clicked"] += st == "clicked"
        if d >= (now.date() - timedelta(days=7)).isoformat() and m.get("source") != "app":
            key = (m.get("subject") or "(no subject)")[:120]
            s = sends.setdefault(key, {"name": key, "first": d, "sent": 0, "delivered": 0, "clicked": 0})
            if b != "failed":
                s["sent"] += 1
            s["delivered"] += b == "delivered"
            s["clicked"] += (m.get("status") or "").lower() == "clicked"
            s["first"] = min(s["first"], d)
    if unknown:
        log("Email statuses not mapped yet (counted as pending):", unknown)

    camps = []
    for c in api.campaigns():
        nxt = c.get("nextProcessingOn")
        camps.append({"name": c.get("name"), "status": c.get("status"), "emailStatus": c.get("emailStatus"),
                      "next_ms": nxt if isinstance(nxt, (int, float)) else None, "total": c.get("totalCount"),
                      "processed": c.get("processed")})
    wfs = {w.get("name"): w.get("status") for w in api.workflows()}
    return {"payments": pays, "failed_payments_30d": failed, "campaigns": camps, "workflows_live": wfs,
            "email_days": days, "sends": sorted(sends.values(), key=lambda s: -s["sent"])[:15],
            "read_at": now.isoformat(), "source": "api"}


# ------------------------------------------------------------------ build the page data
def build(src, ui, prev, now):
    today = now.date()
    yday = (today - timedelta(days=1)).isoformat()
    d7 = [(today - timedelta(days=i)).isoformat() for i in range(7, 0, -1)]          # last 7 full days
    d14 = [(today - timedelta(days=i)).isoformat() for i in range(14, 0, -1)]
    L = CFG["email_lines"]

    # ---- 1. email
    ui_days = (ui or {}).get("days", {})
    api_days = (src or {}).get("email_days", {})

    def day(d):
        if d in ui_days:
            return dict(ui_days[d], src="ui")
        if d in api_days:
            return dict(api_days[d], src="api")
        return None

    def block_for(key):
        return ((ui or {}).get("blocks") or {}).get(key)

    def agg(ds, key):
        rows = [day(d) for d in ds]
        have = [r for r in rows if r]
        if not have:
            return None
        t = {k: sum(r.get(k) or 0 for r in have) for k in ("sent", "delivered", "opened", "clicked", "bounced", "failed")}
        for k in ("complained", "unsubscribed"):
            t[k] = sum(r.get(k) or 0 for r in have) if all(k in r for r in have) else None
        t["blocks"] = block_for(key)
        t["days_found"] = len(have)
        t["days_needed"] = len(ds)
        t["src"] = "ui" if all(r["src"] == "ui" for r in have) else ("api" if all(r["src"] == "api" for r in have) else "mixed")
        s = t["sent"]
        t["delivered_pct"] = pct(t["delivered"], s)
        t["bounce_pct"] = pct(t["bounced"], s)
        t["complaint_pct"] = pct(t["complained"], s, 2) if t["complained"] is not None else None
        t["block_pct"] = pct(t["blocks"], s) if t["blocks"] is not None else None
        return t

    Y = agg([yday], yday)
    W = agg(d7, f"{d7[0]}..{d7[-1]}")
    sending = (ui or {}).get("sending") or {}
    state = sending.get("state", "unknown")
    lines = []

    def lvl(v, rule, n):
        if v is None or (n or 0) < L["min_sent_for_rate"]:
            return "grey"
        if "red_over" in rule and v > rule["red_over"]:
            return "red"
        if "amber_over" in rule and v > rule["amber_over"]:
            return "amber"
        return "green"

    for key, field in (("bounce_pct", "bounce_pct"), ("complaint_pct", "complaint_pct"), ("block_pct", "block_pct")):
        rule = L[key]
        for label, t in (("yesterday", Y), ("7 days", W)):
            v = t.get(field) if t else None
            lines.append({"metric": rule["label"], "period": label, "value": v,
                          "level": lvl(v, rule, t and t["sent"]),
                          "rule": " / ".join(x for x in (f"amber over {rule['amber_over']}%" if "amber_over" in rule else "",
                                                         f"red over {rule['red_over']}%") if x)})
    g = "red" if state in L["ghl_block"]["red_if"] else "amber" if state in L["ghl_block"]["amber_if"] else ("green" if state == "normal" else "grey")
    lines.append({"metric": "GHL sending status", "period": "now", "value": state, "level": g, "rule": "blocked = red, limited = amber"})
    order = {"red": 3, "amber": 2, "green": 1, "grey": 0}
    email_status = max((l["level"] for l in lines), key=lambda x: order[x]) if lines else "grey"
    if email_status == "grey":
        email_status = "grey"
    worst = max(lines, key=lambda l: order[l["level"]]) if lines else None
    trend = []
    for d in d14:
        r = day(d)
        if r:
            trend.append({"d": d, "sent": r.get("sent", 0), "delivered_pct": pct(r.get("delivered", 0), r.get("sent", 0)),
                          "bounce_pct": pct(r.get("bounced", 0), r.get("sent", 0)), "failed": r.get("failed", 0), "src": r["src"]})
    ui_read = (ui or {}).get("read_at")
    ui_age_h = round((now - parse(ui_read)).total_seconds() / 3600, 1) if ui_read else None
    email = {"status": email_status, "yesterday": Y, "last7": W, "yesterday_date": yday, "window": [d7[0], d7[-1]],
             "trend": trend, "lines": lines, "worst": worst,
             "sending": sending, "today_so_far": (ui or {}).get("today_so_far"),
             "ui_read_at": ui_read, "ui_stale": ui_age_h is None or ui_age_h > CFG["ui_stale_hours"],
             "top_blocks": (ui or {}).get("top_blocks"),
             "postmaster": {"state": "not_connected", "text": "Not connected yet"}}

    # ---- 2. buyer access + 3. money
    pays = (src or {}).get("payments", [])
    seven_start = datetime.combine(today - timedelta(days=7), datetime.min.time(), TZ)
    ystart = datetime.combine(today - timedelta(days=1), datetime.min.time(), TZ)
    tstart = datetime.combine(today, datetime.min.time(), TZ)
    s30 = datetime.combine(today - timedelta(days=30), datetime.min.time(), TZ)
    prods, brands, zero = {}, {b: {"y": [0, 0], "d7": [0, 0], "d30": [0, 0]} for b in ("NBR", "BMS", "ISLA", "Other")}, 0
    acc = {a["key"]: {"paid": 0, "matched": 0, "by_email": 0, "by_tag": 0} for a in CFG["access_checks"]["products"]}
    last_sale = None
    for p in pays:
        at = parse(p["at"])
        if at >= tstart or at < s30:
            continue          # today's sales show tomorrow; 30-day window = the 30 full days before today
        if p["amount"] <= 0:
            zero += 1
            continue
        last_sale = max(last_sale or at, at)
        total_items = sum(q * u for _, q, u in p["items"]) or p["amount"]
        for name, q, u in p["items"]:
            share = p["amount"] * (q * u) / total_items if total_items else p["amount"] / len(p["items"])
            b = brand_of(name)
            pr = prods.setdefault(name, {"name": name, "brand": b, "y": [0, 0], "d7": [0, 0], "d30": [0, 0]})
            for k, start in (("y", ystart), ("d7", seven_start), ("d30", s30)):
                if at >= start:
                    pr[k][0] += 1; pr[k][1] += share
                    brands[b][k][0] += 1; brands[b][k][1] += share
        if at >= seven_start:
            for k, proof in (p.get("access") or {}).items():
                if k in acc:
                    acc[k]["paid"] += 1
                    if proof:
                        acc[k]["matched"] += 1
                        acc[k]["by_" + ("email" if proof == "email" else "tag")] += 1
    totals = {k: [sum(b[k][0] for b in brands.values()), sum(b[k][1] for b in brands.values())] for k in ("y", "d7", "d30")}
    products = sorted(prods.values(), key=lambda x: (-x["d30"][1], x["name"]))
    wf_live = (src or {}).get("workflows_live", {})
    app = CFG["ghl_app"]
    access_rows, unmatched = [], 0
    for a in CFG["access_checks"]["products"]:
        c = acc[a["key"]]
        un = c["paid"] - c["matched"]
        unmatched += un
        access_rows.append({"key": a["key"], "label": a["label"], "brand": a["brand"], "workflow": a["workflow"],
                            "workflow_status": wf_live.get(a["workflow"], "unknown"),
                            "paid": c["paid"], "matched": c["matched"], "unmatched": un,
                            "proof": "tag" if not a["proof_subjects"] else "first email",
                            "open_orders": f"{app}/payments/v2/orders",
                            "open_workflow": f"https://app.gohighlevel.com/location/{LOC}/workflow/{a['workflow_id']}"})
    paid7 = sum(r["paid"] for r in access_rows)
    access_status = "red" if unmatched else ("green" if paid7 else "grey")
    ML = CFG["money_lines"]
    days_since = (now - last_sale).days if last_sale else None
    money_status = "green" if totals["d7"][0] else ("amber" if totals["d30"][0] else "red")
    clicks = [{"name": s["name"], "first": s["first"], "sent": s["sent"], "clicked": s["clicked"],
               "click_pct": pct(s["clicked"], s["delivered"])} for s in (src or {}).get("sends", [])]
    money_ = {"status": money_status, "totals": totals, "brands": brands, "products": products, "zero_orders": zero,
              "failed_payments_30d": (src or {}).get("failed_payments_30d"), "days_since_sale": days_since,
              "clicks": clicks, "clicks_state": "ok" if (src or {}).get("source") == "api" else "waiting_for_token",
              "shopify": {"state": "not_connected", "text": "Shopify not connected yet"}}
    access = {"status": access_status, "rows": access_rows, "paid": paid7, "unmatched": unmatched,
              "window": [d7[0], d7[-1]], "within_hours": CFG["access_checks"]["within_hours"]}

    # ---- 4. next sends
    N = CFG["next_sends"]
    end = now + timedelta(days=N["window_days"])
    rows = []
    for c in (src or {}).get("campaigns", []):
        if c.get("status") not in ("active", "scheduled") or not c.get("next_ms"):
            continue
        at = datetime.fromtimestamp(c["next_ms"] / 1000, TZ)
        overdue = at < now and not c.get("processed")
        if not (now - timedelta(hours=24) <= at <= end) or (at < now and not overdue):
            continue
        hint = next((h for h in N["audience_hints"] if (c["name"] or "").startswith(h["name_starts"])), None)
        size = c.get("total") or (hint and hint["contacts"])
        b = next((bb for pre, bb in N["brand_from_name"] if pre.lower() in (c["name"] or "").lower()), "Other")
        rows.append({"at": at.isoformat(), "date": short_day(at.date()), "time": at.strftime("%-I:%M %p").lower(),
                     "name": c["name"], "brand": b, "channel": "Email", "audience": size,
                     "audience_note": "list size" if c.get("total") else ("about, from " + hint["source"] if hint else "set when the send starts"),
                     "overdue": overdue})
    rows.sort(key=lambda r: r["at"])
    flags = []
    upcoming = [parse(r["at"]) for r in rows if not r["overdue"]]
    marks = [now] + upcoming + [end]
    for a, b in zip(marks, marks[1:]):
        gap = (b - a).total_seconds() / 86400
        if gap > N["gap_days_amber"]:
            flags.append({"level": "amber", "text": f"No sends scheduled for {gap:.0f} days ({short_day(et(a).date())} to {short_day(et(b).date())})."})
    for r in rows:
        if r["overdue"]:
            flags.append({"level": "amber", "text": f"{r['name']} was due {r['date']} {r['time']} and GHL shows it still scheduled with nothing processed."})
        if (r["audience"] or 0) > N["big_send_contacts"] and email_status in ("amber", "red") and not r["overdue"]:
            flags.append({"level": "red", "text": f"{r['name']} ({r['date']}) goes to about {r['audience']:,} contacts while email is {email_status}."})
    next_status = "red" if any(f["level"] == "red" for f in flags) else ("amber" if flags else ("green" if rows else "amber"))
    if not rows:
        flags.append({"level": "amber", "text": f"Nothing scheduled in the next {N['window_days']} days."})
    nxt = {"status": next_status, "rows": rows, "flags": flags, "window_days": N["window_days"],
           "sms_note": "Scheduled SMS (Bulk Actions) can't be read by the API yet; check GHL if any are planned."}

    # ---- daily report (fixed rules, config.report_rules)
    def wline(l):
        v = l["value"]
        return f"{l['metric'].lower()} {v}% ({l['period']})" if isinstance(v, (int, float)) else f"{l['metric']} {v}"
    if email_status == "red":
        head = f"Email is not healthy: {wline(worst)}."
    elif email_status == "amber":
        head = f"Email needs watching: {wline(worst)}."
    else:
        head = f"{totals['d7'][0]} sales in 7 days ({money(totals['d7'][1])}); email is healthy."
    if totals["y"][0]:
        win = f"{totals['y'][0]} sale{'s' if totals['y'][0] != 1 else ''} yesterday, {money(totals['y'][1])}."
    elif paid7 and not unmatched:
        win = f"All {paid7} buyer{'s' if paid7 != 1 else ''} in the last 7 days got their access automation."
    elif Y and (Y.get("delivered_pct") or 0) >= 95 and Y["sent"] >= L["min_sent_for_rate"]:
        win = f"Delivered {Y['delivered_pct']}% of email yesterday."
    else:
        win = "No win to report today."
    big = next((f for f in flags if f["level"] == "red"), None)
    if state in ("blocked", "limited"):
        watch = f"GHL sending is {state}: {sending.get('until_label') or sending.get('title', '')}".rstrip(": ") + "."
    elif big:
        watch = big["text"]
    elif unmatched:
        watch = f"{unmatched} buyer{'s' if unmatched != 1 else ''} with no access automation: open in GHL."
    elif any("No sends" in f["text"] for f in flags):
        watch = next(f["text"] for f in flags if "No sends" in f["text"])
    else:
        watch = "Nothing to watch."
    stamp = now.strftime("%a %b %-d, %-I:%M %p ET")
    report = (f"NBR daily KPIs, {stamp}\n"
              f"Headline: {head}\nWin: {win}\nWatch: {watch}\n"
              f"Email {email_status.upper()} | Buyer access {access_status.upper()} | Money {money_status.upper()} | Next sends {next_status.upper()}\n"
              f"Sales: yesterday {totals['y'][0]} ({money(totals['y'][1])}), 7 days {totals['d7'][0]} ({money(totals['d7'][1])}), "
              f"30 days {totals['d30'][0]} ({money(totals['d30'][1])})")
    return {
        "version": 1, "updated_at": now.isoformat(), "updated_label": stamp, "today": today.isoformat(), "yesterday": yday,
        "api_source": (src or {}).get("source", "none"), "api_read_at": (src or {}).get("read_at"),
        "status": {"mail": email_status, "access": access_status, "money": money_status, "next": next_status},
        "report": {"headline": head, "win": win, "watch": watch, "text": report},
        "mail": email, "access": access, "money": money_, "next": nxt,
    }, report


def main():
    now = datetime.now(TZ)
    prev = None
    try:
        prev = json.load(open(os.path.join(ROOT, "data.json"), encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    ui = None
    try:
        ui = json.load(open(os.path.join(ROOT, "ui.json"), encoding="utf-8"))
    except FileNotFoundError:
        pass
    tok = token()
    src = None
    if tok:
        log("Reading GHL (read-only, NBR location)…")
        src = from_api(API(tok), now)
        if os.path.isdir(RAW) and not ON_ACTIONS:
            json.dump(src, open(os.path.join(RAW, f"api_{now:%Y-%m-%d_%H%M}.json"), "w"), indent=1)
    else:
        boots = sorted(glob.glob(os.path.join(RAW, "bootstrap_api_*.json")))
        if boots and not ON_ACTIONS:
            src = json.load(open(boots[-1], encoding="utf-8"))
            src["source"] = "chrome-bootstrap"
            log(f"No GHL token yet: using the Chrome read in {os.path.basename(boots[-1])}.")
        elif prev and prev.get("_src"):
            src = prev["_src"]
            src["source"] = "previous-run"
            log("No GHL token: keeping the last API numbers (marked as such on the page).")
        else:
            raise SystemExit(f"Stopped: no GHL token and no earlier data. Add the GHL_TOKEN secret (see README). {KEEP}")
    data, report = build(src, ui, prev, now)
    # carry the API-derived totals forward for runs without a token. Payments keep dates, amounts, products and
    # a matched/not-matched flag per access check only: no names, emails, contact or order IDs.
    data["_src"] = {"payments": [{"at": p["at"], "amount": p["amount"], "items": p["items"], "access": p.get("access", {})}
                                  for p in src.get("payments", [])],
                    "failed_payments_30d": src.get("failed_payments_30d"), "campaigns": src.get("campaigns", []),
                    "workflows_live": src.get("workflows_live", {}), "email_days": src.get("email_days", {}),
                    "sends": src.get("sends", []), "read_at": src.get("read_at")}
    json.dump(data, open(os.path.join(ROOT, "data.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    open(os.path.join(ROOT, "report.txt"), "w", encoding="utf-8").write(report + "\n")
    log(report)


if __name__ == "__main__":
    try:
        main()
    except NotReadOnly as e:
        raise SystemExit(f"Stopped by the read-only guard: {e} {KEEP}")
