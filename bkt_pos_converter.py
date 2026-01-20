import re
import fitz
import pandas as pd

pdf_path = "pos.pdf"

doc = fitz.open(pdf_path)

# ---------------------------------------------------------
# Read raw text but preserve line order
# ---------------------------------------------------------
raw_lines = []
for page in doc:
    raw_lines.extend(page.get_text().split("\n"))

# ---------------------------------------------------------
# Normalize: combine broken lines and remove number spaces
# ---------------------------------------------------------

cleaned_lines = []
buffer = ""

for line in raw_lines:
    # Remove spacing inside numbers e.g. "37 567 . 82" → "37567.82"
    fixed = re.sub(r"(\d)\s+(\d)", r"\1\2", line)          # join digits split by space
    fixed = re.sub(r"(\d),\s+(\d)", r"\1,\2", fixed)        # join 37, 567
    fixed = re.sub(r"\s+(\.\d{2})", r"\1", fixed)           # join ". 82"

    # Join lines that belong together
    if "OPENING BALANCE" in fixed.upper():
        buffer = fixed
        continue
    if buffer:
        fixed = buffer + " " + fixed
        buffer = ""

    cleaned_lines.append(fixed)

lines = cleaned_lines


# ---------------------------------------------------------
# Parse lines
# ---------------------------------------------------------

results = []
opening_balance = None
transactions_started = False

date_pattern = r"(\d{2}-[A-Z]{3}-\d{2})"

for raw in lines:

    # 1) Detect OPENING BALANCE with flexible regex
    if "OPENING BALANCE" in raw.upper():
        match = re.search(r"OPENING BALANCE[: ]+([\d,]+\.\d{2})", raw)
        if match:
            opening_balance = float(match.group(1).replace(",", ""))
        continue

    # 2) After BOOKING DATE → transactions start
    if "BOOKING DATE" in raw.upper():
        transactions_started = True
        continue

    if not transactions_started:
        continue

    # 3) Detect transaction row
    date_match = re.search(date_pattern, raw)
    if not date_match:
        continue

    date = date_match.group(1)

    # 4) Extract all numeric fields
    amounts = re.findall(r"([\d,]+\.\d{2})", raw)

    debit = ""
    credit = ""
    balance = None

    if len(amounts) >= 1:
        amount = float(amounts[0].replace(",", ""))

        # detect sign by looking before number
        before_amount = raw.split(amounts[0])[0]
        if "-" in before_amount:
            debit = amount
        else:
            credit = amount

    if len(amounts) >= 2:
        balance = float(amounts[1].replace(",", ""))

    # 5) If balance missing → calculate
    if balance is None and opening_balance is not None:
        if debit:
            opening_balance -= debit
        if credit:
            opening_balance += credit
        balance = opening_balance

    results.append({
        "Date": date,
        "Description": raw.strip(),
        "Type": "",
        "Other": "",
        "Debit": debit,
        "Credit": credit,
        "Balance": balance
    })

# Save to CSV
df = pd.DataFrame(results)
df.to_csv("pos_output.csv", index=False)

print("POS conversion completed → pos_output.csv")
