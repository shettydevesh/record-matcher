import pdfplumber
import re
import json
import os
import argparse
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime

# ── schema ──

@dataclass
class Record:
    source_file: str
    collection: str
    doc_type: str
    supplier: str = "Unknown"
    doc_number: str = None
    customer_ref: str = None
    cross_refs: list = field(default_factory=list)
    date: str = None
    amount: float = None
    raw_text: str = ""
    extraction_method: str = "regex"
    fields_found: list = field(default_factory=list)

@dataclass
class Match:
    left: str
    right: str
    score: float
    confidence: str
    signals: list
    warnings: list
    needs_review: bool = False


DEFAULT_PATTERNS = {
    "Applelec": {
        "name_match": r"applelec",
        "dn_number": r"Delivery no\.\s*(\d+)",
        "invoice_number": r"Invoice number\s*(\d+)",
        "customer_ref": r"Your ref:\s*([\w\-]+)",
        "date": r"(?:Delivery date|Invoice date):?\s*([\d/]+\s*\w*\s*\d*)",
    },
    "Formed": {
        "name_match": r"formed",
        "dn_number": r"Delivery Note\s+(\d+)",
        "invoice_number": r"Invoice\s*No:\s*(\d+)",
        "customer_ref": r"Order Ref:\s*([\w\-]+)",
        "date": r"(?:Despatch|Invoice) Date:\s*([\d/]+)",
    },
    "James Latham": {
        "name_match": r"latham",
        "dn_number": r"Delivery Note (?:Number|No)\s*(\d+)",
        "invoice_number": r"Invoice Number\s*(\d+)",
        "customer_ref": r"(?:Customer Ref|Your Ref):\s*([\d][\w\-]+)",
        "date": r"(?:Delivery|Invoice) Date\s*([\d\-/]+\w*\-?\w*\-?\d*)",
    },
    "Aalco": {
        "name_match": r"aalco|harrimans",
        "dn_number": r"(SHP\w+)",
        "invoice_number": r"(ARD\w+)",
        "customer_ref": r"Your Reference\s*([\d][\w\-]+)",
    },
    "Halifax Glass": {
        "name_match": r"halifax\s*glass",
        "dn_number": r"DELIVERY NOTE\s*(\d{5,})",
        "invoice_number": r"INVOICE\s*(\d+)",
        "customer_ref": r"Order No:\s*([\d][\w\-]+)",
        "date": r"Delivery Date\s*([\d/]+)",
    },
    "Microkerf": {
        "name_match": r"micro\s*kerf",
        "dn_number": r"Delivery Note No\.\s*(\d+)",
        "invoice_number": r"Invoice No\.\s*(\d+)",
        "customer_ref": r"Customer Order No\s*:?\s*([\w\-]+)",
        "date": r"(?:Despatch|Invoice) Date\s*:?\s*([\d/]+)",
    },
    "Ottima": {
        "name_match": r"ottima",
        "dn_number": r"Delivery Note\s*.*?(\d{5})",
        "invoice_number": r"(?:Document No|Invoice):\s*(\d+)",
        "customer_ref": r"(?:Your Order No|Order No):\s*([\d][\w\-]+)",
        "date": r"(?:Date/Tax Point|Date):\s*([\d/]+)",
    },
    "Woodworking Machinery": {
        "name_match": r"woodworking\s*machinery",
        "invoice_number": r"Invoice No\s*(\d+)",
        "customer_ref": r"Order No\s*([\d][\w\-]+)",
        "date": r"Invoice Date\s*([\d/]+)",
    },
    "Lawcris": {
        "name_match": r"lawcris",
        "dn_number": r"Delivery Note No\s*(\w+)",
        "invoice_number": r"Invoice No\s*(\d+)",
        "customer_ref": r"Customer Ref:\s*([\w\-]+)",
        "date": r"(?:Delivery|Invoice) Date\s*([\d/]+)",
    },
    "Worldwide Express": {
        "name_match": r"worldwide\s*express",
        "dn_number": r"COLLECTION\s*(\d+)",
        "invoice_number": r"INVOICE\s*(?:NO)?\s*(\d+)",
        "customer_ref": r"Cust Ref \d\s*([\w\-]+)",
        "date": r"(\d{2}-\w{3}-\d{4})",
    },
}

# generic patterns — fallback for unknown suppliers
GENERIC = {
    "po_ref": [
        r'\b(\d{5}-\d{6}-\w{1,3})\b',
        r'\bPO[\s\-#:]*(\w{6,})\b',
    ],
    "doc_number": [
        r'(?:Invoice|Inv)[\s\.#:No]*(\d{4,})',
        r'(?:Delivery Note|DN)[\s\.#:No]*(\d{4,})',
    ],
    "date": [
        r'(\d{2}/\d{2}/\d{4})',
        r'(\d{2}-\w{3}-\d{4})',
        r'(\d{1,2}\s+\w+\s+\d{4})',
    ],
    "amount": [
        r'(?:Total|Net|Gross)[\s:£$]*(\d[\d,]*\.\d{2})',
        r'[£$]\s*(\d[\d,]*\.\d{2})',
    ],
}


# ── extraction helpers ──

def get_text(filepath):
    """pull text from pdf, fallback to pdftotext if pdfplumber chokes"""
    try:
        with pdfplumber.open(filepath) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception:
        result = subprocess.run(["pdftotext", "-layout", filepath, "-"], capture_output=True, text=True)
        return result.stdout


def find_supplier(text, patterns):
    text_lower = text.lower()
    for name, pats in patterns.items():
        if re.search(pats["name_match"], text_lower):
            return name, pats
    return "Unknown", {}


def grab(text, pattern):
    """run one regex, return first capture group or None"""
    if not pattern:
        return None
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    return m.group(1) if m.lastindex else m.group(0)


def grab_any(text, patterns):
    """try multiple patterns, return first hit"""
    for p in patterns:
        result = grab(text, p)
        if result:
            return result
    return None


def infer_doc_type(text):
    t = text.upper()
    if "INVOICE" in t: return "invoice"
    if "DELIVERY NOTE" in t or "DESPATCH NOTE" in t: return "delivery_note"
    if "COLLECTION" in t: return "collection_note"
    if "CREDIT NOTE" in t: return "credit_note"
    return "unknown"


# ── main extraction ──

def extract(filepath, collection, supplier_patterns):
    filename = os.path.basename(filepath)
    text = get_text(filepath)
    supplier, pats = find_supplier(text, supplier_patterns)
    is_right = collection == "right"

    rec = Record(
        source_file=filename,
        collection=collection,
        doc_type=infer_doc_type(text),
        supplier=supplier,
        raw_text=text[:800],
    )
    found = []

    # layer 1: supplier-specific
    if pats:
        if is_right:
            rec.doc_number = grab(text, pats.get("invoice_number"))
            dn_ref = grab(text, pats.get("dn_number"))
            if dn_ref:
                rec.cross_refs.append(dn_ref)
        else:
            rec.doc_number = grab(text, pats.get("dn_number"))
            inv_ref = grab(text, pats.get("invoice_number"))
            if inv_ref:
                rec.cross_refs.append(inv_ref)

        rec.customer_ref = grab(text, pats.get("customer_ref"))
        rec.date = grab(text, pats.get("date"))

        if rec.doc_number: found.append("doc_number")
        if rec.customer_ref: found.append("customer_ref")
        if rec.date: found.append("date")
        if rec.cross_refs: found.append("cross_ref")

    # layer 2: generic fallback
    if not rec.customer_ref:
        rec.customer_ref = grab_any(text, GENERIC["po_ref"])
        if rec.customer_ref: found.append("customer_ref:generic")

    if not rec.doc_number:
        rec.doc_number = grab_any(text, GENERIC["doc_number"])
        if rec.doc_number: found.append("doc_number:generic")

    if not rec.date:
        rec.date = grab_any(text, GENERIC["date"])
        if rec.date: found.append("date:generic")

    amt = grab_any(text, GENERIC["amount"])
    if amt:
        try:
            rec.amount = float(amt.replace(",", ""))
            found.append("amount")
        except ValueError:
            pass

    rec.fields_found = found
    return rec


# ── matching ──

WEIGHTS = {
    "supplier": 0.15,
    "customer_ref": 0.35,
    "cross_ref": 0.20,
    "doc_in_text": 0.10,
    "date": 0.10,
    "amount": 0.10,
}

def norm(s):
    if not s: return ""
    return re.sub(r'[\s\-_./]+', '', s.strip().lower())


def parse_date(s):
    for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


def score_pair(left, right):
    total = 0.0
    signals = []
    warnings = []

    # supplier gate
    if left.supplier != "Unknown" and right.supplier != "Unknown":
        if left.supplier == right.supplier:
            total += WEIGHTS["supplier"]
            signals.append(f"Same supplier: {left.supplier}")
        else:
            return Match(left.source_file, right.source_file, 0, "NONE", [],
                        [f"Different suppliers: {left.supplier} vs {right.supplier}"])

    # customer ref
    if left.customer_ref and right.customer_ref:
        if norm(left.customer_ref) == norm(right.customer_ref):
            total += WEIGHTS["customer_ref"]
            signals.append(f"Customer ref match: {left.customer_ref}")
        else:
            warnings.append(f"Different refs: {left.customer_ref} vs {right.customer_ref}")
    elif left.customer_ref or right.customer_ref:
        warnings.append("Customer ref on only one side")

    # cross-document reference
    cross_hit = False
    if right.cross_refs and left.doc_number:
        for ref in right.cross_refs:
            if norm(ref) == norm(left.doc_number):
                total += WEIGHTS["cross_ref"]
                signals.append(f"Right doc references left doc#: {left.doc_number}")
                cross_hit = True
                break

    if not cross_hit and left.cross_refs and right.doc_number:
        for ref in left.cross_refs:
            if norm(ref) == norm(right.doc_number):
                total += WEIGHTS["cross_ref"]
                signals.append(f"Left doc references right doc#: {right.doc_number}")
                cross_hit = True
                break

    # doc number in raw text (fuzzy fallback)
    if not cross_hit:
        if left.doc_number and norm(left.doc_number) in norm(right.raw_text):
            total += WEIGHTS["doc_in_text"]
            signals.append(f"Left doc# ({left.doc_number}) found in right text")
        if right.doc_number and norm(right.doc_number) in norm(left.raw_text):
            total += WEIGHTS["doc_in_text"]
            signals.append(f"Right doc# ({right.doc_number}) found in left text")

    # date proximity
    if left.date and right.date:
        d1, d2 = parse_date(left.date), parse_date(right.date)
        if d1 and d2:
            delta = abs((d1 - d2).days)
            if delta <= 7:
                total += WEIGHTS["date"]
                signals.append(f"Dates within {delta} days")
            elif delta <= 30:
                total += WEIGHTS["date"] * 0.5
                signals.append(f"Dates within {delta} days (weak)")

    # amount match
    if left.amount and right.amount:
        if abs(left.amount - right.amount) < 0.01:
            total += WEIGHTS["amount"]
            signals.append(f"Amount match: {left.amount}")

    # classify
    if total >= 0.50: conf = "HIGH"
    elif total >= 0.25: conf = "MEDIUM"
    elif total > 0: conf = "LOW"
    else: conf = "NONE"

    return Match(
        left=left.source_file,
        right=right.source_file,
        score=round(total, 3),
        confidence=conf,
        signals=signals,
        warnings=warnings,
        needs_review=conf in ("MEDIUM", "LOW"),
    )


def assign_matches(scores):
    """greedy 1:1 assignment — highest score first"""
    scores.sort(key=lambda m: m.score, reverse=True)
    used_l, used_r = set(), set()
    matches = []
    for m in scores:
        if m.score <= 0:
            continue
        if m.left not in used_l and m.right not in used_r:
            matches.append(m)
            used_l.add(m.left)
            used_r.add(m.right)
    return matches, used_l, used_r


# ── pipeline ──

def load_config(path):
    if not path or not os.path.exists(path):
        return DEFAULT_PATTERNS
    with open(path) as f:
        custom = json.load(f)
    merged = {**DEFAULT_PATTERNS, **custom}
    print(f"Loaded {len(custom)} custom patterns from {path}")
    return merged


def load_collection(directory, collection, patterns):
    records = []
    for f in sorted(os.listdir(directory)):
        if not f.lower().endswith('.pdf'):
            continue
        rec = extract(os.path.join(directory, f), collection, patterns)
        records.append(rec)

        icon = "✓" if len(rec.fields_found) >= 2 else "△" if rec.fields_found else "✗"
        print(f"  {icon} {f}")
        print(f"    supplier={rec.supplier} ref={rec.customer_ref} doc#={rec.doc_number}")
    return records


def run(left_dir, right_dir, config_path=None, output_path="match_results.json"):
    print("=" * 60)
    print(f"Left:  {left_dir}")
    print(f"Right: {right_dir}")
    print("=" * 60)

    patterns = load_config(config_path)

    print(f"\n-- left collection --")
    left = load_collection(left_dir, "left", patterns)

    print(f"\n-- right collection --")
    right = load_collection(right_dir, "right", patterns)

    # score every pair
    print(f"\n-- scoring {len(left)}x{len(right)} pairs --\n")
    all_scores = []
    for l in left:
        for r in right:
            result = score_pair(l, r)
            if result.score > 0:
                all_scores.append(result)

    matches, used_l, used_r = assign_matches(all_scores)

    # print results
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)

    for m in sorted(matches, key=lambda x: x.score, reverse=True):
        icon = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(m.confidence, "⚪")
        print(f"\n{icon} [{m.confidence}] {m.score}")
        print(f"   {m.left}  ↔  {m.right}")
        for s in m.signals:
            print(f"   + {s}")
        for w in m.warnings:
            print(f"   ⚠ {w}")

    # unmatched
    all_l = {r.source_file for r in left}
    all_r = {r.source_file for r in right}
    unmatched_l = all_l - used_l
    unmatched_r = all_r - used_r

    if unmatched_l or unmatched_r:
        print(f"\n-- unmatched --")
        for u in sorted(unmatched_l): print(f"   left:  {u}")
        for u in sorted(unmatched_r): print(f"   right: {u}")

    high = sum(1 for m in matches if m.confidence == "HIGH")
    med = sum(1 for m in matches if m.confidence == "MEDIUM")
    low = sum(1 for m in matches if m.confidence == "LOW")

    print(f"\n-- summary --")
    print(f"   matched: {len(matches)} ({high} high, {med} medium, {low} low)")
    print(f"   review:  {sum(1 for m in matches if m.needs_review)}")
    print(f"   unmatched: {len(unmatched_l)} left, {len(unmatched_r)} right")

    # save
    output = {
        "summary": {
            "left": len(left), "right": len(right), "matched": len(matches),
            "high": high, "medium": med, "low": low,
            "unmatched_left": len(unmatched_l), "unmatched_right": len(unmatched_r),
        },
        "matches": [asdict(m) for m in sorted(matches, key=lambda x: x.score, reverse=True)],
        "unmatched": {"left": sorted(unmatched_l), "right": sorted(unmatched_r)},
    }
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"   saved to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Match records across two document collections")
    parser.add_argument("--left", required=True)
    parser.add_argument("--right", required=True)
    parser.add_argument("--config", default=None, help="custom supplier patterns json")
    parser.add_argument("--output", default="match_results.json")
    args = parser.parse_args()
    run(args.left, args.right, args.config, args.output)
