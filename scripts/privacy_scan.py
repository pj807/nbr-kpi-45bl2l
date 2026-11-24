"""Privacy scan: stops a publish if any file about to be committed contains an email address, phone number,
GHL contact/order ID, coupon code, key or token. Usage: python3 -I scripts/privacy_scan.py [files...]
(no files = the files staged in git). Exit code 1 = something found: nothing gets published."""
import hashlib, re, subprocess, sys

# Coupon codes are stored as hashes so this public file never shows them (first 16 hex of sha256 of the code in capitals).
# To add one: python3 -c "import hashlib;print(hashlib.sha256(b'CODE').hexdigest()[:16])"
COUPON_HASHES = {"2af043c9dc532ddb", "1344ccabf821999f", "69bc819b171220c4", "ba0e71482feef640", "416438bed87b0c86", "d32cf1566386a74f"}
WORD = re.compile(r"[A-Za-z0-9]{4,20}")
PATTERNS = {
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone number": re.compile(r"(?<![\w.])\+?1?[\s.-]?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\w)|\+\d{10,15}\b"),
    "GHL token": re.compile(r"\bpit-[0-9a-f]{8}-|\beyJ[A-Za-z0-9_-]{20,}\.|\bBearer\s+[A-Za-z0-9._-]{20,}", re.I),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{20,}"),
    "Shopify token": re.compile(r"\bshp(at|ca|pa|ss)_[0-9a-f]{20,}", re.I),
    "Stripe ID or key": re.compile(r"\b(pi|ch|cus|sk_live|rk_live|acct)_[A-Za-z0-9]{10,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "contact/order field": re.compile(r'"(contactId|contact_id|contactName|contactEmail|customerId|orderId|order_id)"\s*:|"(phone|email|firstName|lastName|fullName)"\s*:\s*"', re.I),
    # GHL contact IDs: 20 letters+digits with upper, lower and a digit. Order/transaction IDs: 24 hex.
    "GHL contact ID": re.compile(r"(?<![A-Za-z0-9/_-])(?=[A-Za-z0-9]{20}(?![A-Za-z0-9]))(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{20}(?![A-Za-z0-9])"),
    "GHL order/record ID": re.compile(r"(?<![A-Za-z0-9])[0-9a-f]{24}(?![A-Za-z0-9])"),
}
# Strings that look like a match but are safe to publish
ALLOW = {"JResmiMkYCKiyhmQ3Aa3",                                   # the NBR location ID (in GHL links)
         "41898282+github-actions[bot]@users.noreply.github.com"}


def staged():
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"], capture_output=True, text=True).stdout
    return [f for f in out.splitlines() if f]


def main():
    files = sys.argv[1:] or staged()
    hits = []
    for f in files:
        if f.startswith("data/raw/") or f.endswith(".env") or "/raw/" in f:
            hits.append((f, 0, "private cache or secret file staged", f)); continue
        try:
            lines = open(f, encoding="utf-8", errors="ignore").read().splitlines()
        except (IsADirectoryError, FileNotFoundError):
            continue
        for i, line in enumerate(lines, 1):
            for w in WORD.findall(line):
                if hashlib.sha256(w.upper().encode()).hexdigest()[:16] in COUPON_HASHES:
                    hits.append((f, i, "coupon code", w[:2] + "…"))
            for label, rx in PATTERNS.items():
                for m in rx.finditer(line):
                    if m.group(0) not in ALLOW:
                        hits.append((f, i, label, m.group(0)[:4] + "…"))
    if hits:
        print("PRIVACY SCAN FAILED. Nothing was published. Found:")
        for f, i, label, snip in hits[:50]:
            print(f"  {f}:{i}  {label}  ({snip})")
        sys.exit(1)
    print(f"Privacy scan passed ({len(files)} files).")


if __name__ == "__main__":
    main()
