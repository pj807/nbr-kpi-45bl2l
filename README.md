# NBR daily KPIs

A private-link dashboard for NBR Education (GHL location "Natural Beaded Rows (NBR)" only).
Page: https://pj807.github.io/nbr-kpi-45bl2l/ (not indexed: robots.txt + noindex).

**Read-only.** `scripts/pull.py` only reads GHL. Its guard refuses any call that is not a GET (the one exception
is the read-only contact search) and any request for a location other than NBR. Nothing in GHL is created, edited,
saved, published, paused, scheduled or sent. Only totals are published: no names, emails, phone numbers,
contact or order IDs, coupon codes or keys. `scripts/privacy_scan.py` checks every file before each publish
and stops the publish if it finds any.

## What each number means and where it comes from

Time zone: America/New_York. "Yesterday" = the full day before today, set by the ~6 AM run.
"Last 7 days" = the 7 full days before today. Today's sales and sends show up tomorrow.

| Section | Number | Source |
|---|---|---|
| 1 Email | Sent, delivered, opened, clicked, bounced, failed per day | GHL **Email Analytics** screen (UI-only). Once the token exists, days not yet read on the screen are filled from the API message export. |
| 1 Email | Complaints, unsubscribes per day | Email Analytics screen (UI-only) |
| 1 Email | Provider blocks (ESP/ISP blocks), top block reasons | **Bounce Classification** screen (UI-only) |
| 1 Email | GHL sending status (normal / limited / blocked + until when) | The red notice GHL shows in the app (UI-only, read in the logged-in GHL session) |
| 1 Email | Gmail domain reputation | Not connected yet (GHL shows it under Postmaster Tools; Google Postmaster later) |
| 2 Buyers | Paid orders in the last 7 days per product, matched to the access automation | API: payments transactions + order details; proof = the automation's first email reached the buyer within 24 h (message export, any status), or its tag is on the contact (wigs) |
| 3 Money | Sales count + revenue by brand and product, yesterday / 7 / 30 days | API: payments transactions (paid, live, minus refunds); $0 orders and failed payments are listed but not counted |
| 3 Money | Email clicks per send, last 7 days | API: message export, grouped by subject |
| 3 Money | ISLA store sales (islahair.com) | Shopify not connected yet |
| 4 Next | Scheduled email campaigns, next 7 days: date, time, name, brand, audience | API: email campaigns schedule. GHL fixes a campaign's list when it starts, so audience = "about N" from `config.json` `audience_hints` until then |
| 4 Next | Scheduled SMS | Not readable through the API (Bulk Actions) |

Rates use GHL's own Email Analytics base (per email sent). GHL's Bounce Classification "delivery rate" also counts
failed (blocked) emails, so it is lower; the page shows failed separately.

### Status colours (rules in `config.json`)
- **Email** = the worst of: bounces >2% amber, >4% red · complaints >0.1% amber, >0.3% red · provider blocks >1% red ·
  GHL sending blocked = red, limited = amber. Each is checked for yesterday and for 7 days. Days with fewer than 50 sent are not rated.
- **Buyer access**: red if any buyer in 7 days has no access automation; green if all matched; grey if no buyers.
- **Money**: green with a sale in 7 days, amber with none in 7 days, red with none in 30 days.
- **Next sends**: red if a send over 3,000 contacts is scheduled while email is amber/red; amber for a gap of more
  than 5 days with no send, a send that is overdue, or nothing scheduled.
- **Daily report** (Headline / Win / Watch): fixed rules in `config.json` → `report_rules`. "Copy daily report" copies `report.txt`.

## How it updates
- **GitHub Action "Daily refresh"**: about 6 AM, noon, 6 PM and midnight ET (starts ~45 min early). If a pull fails it
  waits 3 minutes and tries once more; if it fails again nothing is published, the last good numbers stay, and
  GitHub emails the repo owner. Run it by hand: repo → Actions → Daily refresh → Run workflow.
  - Daylight saving: the cron times are UTC for EST. After Mar 8, 2027 (EDT), move each cron one hour earlier.
- **UI-only numbers** (email screens, provider blocks, sending notice) can't be read by the Action. Re-read them on the Mac:
  in Claude Code in the "Claude NBR" folder say **"nbr kpi: read the GHL email screens"**. Claude opens Settings → Email
  Services on the NBR location in Chrome (view only: it only changes date filters), saves the totals to
  `data/raw/ui_read_<date>.json` and runs `./refresh.sh --ui data/raw/ui_read_<date>.json`. The page shows when these were last read.
- **Manual run on the Mac**: `./refresh.sh` (uses the Keychain item `ghl-nbr-kpi` if you add the token there; otherwise the last API read).

## Files
`index.html` page · `data.json` totals · `report.txt` daily report · `config.json` rules and product map ·
`ui.json` UI-only totals · `scripts/pull.py` read + build · `scripts/ui_update.py` merge a UI read ·
`scripts/privacy_scan.py` publish check · `.github/workflows/daily-refresh.yml` schedule · `refresh.sh` Mac run.
`data/raw/` is a private cache on the Mac only (blocked by `.gitignore` and by the scan).

## Adding later
- **Shopify (ISLA store)**: create a custom app in Shopify admin with the `read_orders` scope only, add its Admin API token as the
  repo secret `SHOPIFY_TOKEN`, set `config.json` → `shopify.enabled: true`, then add the Shopify read in `pull.py` (one GET to
  `/admin/api/<version>/orders.json?status=any&created_at_min=…`), totals only.
- **Google Postmaster**: needs a Google Cloud service account with Postmaster Tools read access for the sending domain;
  store its key as a repo secret, set `config.json` → `postmaster.enabled: true`.
