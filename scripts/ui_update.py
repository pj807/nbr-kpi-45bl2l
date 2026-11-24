"""Merge a read of GHL's UI-only numbers into ui.json (totals only, safe to publish).

GHL shows these only in the web app (Settings > Email Services), not in the API:
  - Email Analytics per day: sent, delivered, opened, clicked, complained, bounced, unsubscribed, failed
  - Bounce Classification: ESP/ISP (provider) blocks for a day or a date range, top block reasons
  - the "Email sending is blocked/limited" notice
How the numbers are read: in Claude Code (Claude NBR folder) say "nbr kpi: read the GHL email screens".
Claude opens those screens in Chrome on the NBR location (view only, it changes date filters and nothing else),
writes what it reads to a JSON file shaped like the example below, and runs:
    python3 -I scripts/ui_update.py data/raw/ui_read_<date>.json && ./refresh.sh
Example input:
  {"read_at": "2026-11-24T22:05:00-05:00",
   "days": {"2026-11-23": [500, 400, 95, 0, 0, 100, 0, 4733]},     # sent, delivered, opened, clicked, complained, bounced, unsubscribed, failed
   "blocks": {"2026-11-23": 99, "2026-11-17..2026-11-23": 552},
   "top_blocks": [["Gmail", "550 5.7.1 Low Domain Reputation", 70]],
   "sending": {"state": "blocked", "title": "...", "until": "2026-11-24T10:04:00-05:00"},
   "today_so_far": {"date": "2026-11-24", "sent": 0, "failed": 1179}}
"""
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["sent", "delivered", "opened", "clicked", "complained", "bounced", "unsubscribed", "failed"]


def main(path):
    new = json.load(open(path, encoding="utf-8"))
    out_path = os.path.join(ROOT, "ui.json")
    try:
        ui = json.load(open(out_path, encoding="utf-8"))
    except FileNotFoundError:
        ui = {"_note": "UI-only GHL numbers (totals only), read in Chrome on the NBR location. Updated by scripts/ui_update.py.",
              "days": {}, "blocks": {}}
    for d, v in (new.get("days") or {}).items():
        ui["days"][d] = dict(zip(FIELDS, v)) if isinstance(v, list) else v
    ui["blocks"].update(new.get("blocks") or {})
    for k in ("top_blocks", "sending", "today_so_far"):
        if k in new:
            ui[k] = new[k]
    s = ui.get("sending") or {}
    if s.get("until"):
        u = datetime.fromisoformat(s["until"]).astimezone(ZoneInfo("America/New_York"))
        s["until_label"] = f"notice says sending resumes {u:%a %b} {u.day}, {u:%-I:%M %p} ET"
    ui["read_at"] = new.get("read_at") or datetime.now(ZoneInfo("America/New_York")).isoformat()
    ui["days"] = dict(sorted(ui["days"].items())[-60:])          # keep the last 60 days
    json.dump(ui, open(out_path, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"ui.json updated: {len(ui['days'])} days, read at {ui['read_at']}")


if __name__ == "__main__":
    main(sys.argv[1])
