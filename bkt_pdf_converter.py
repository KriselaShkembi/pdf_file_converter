import fitz  # PyMuPDF
import pandas as pd
import re
from datetime import datetime
import csv

pdf_path = "10.2025 Bkt ALL.pdf"
doc = fitz.open(pdf_path)

# ---------- READ FULL PDF ----------
full_text = ""
for page in doc:
    full_text += page.get_text()

lines = full_text.split("\n")

# ---------- PATTERNS ----------
date_pattern = re.compile(r"\d{2}-[A-Za-z]{3}-\d{2}")
amount_pattern = r"[\d,]+\.\d{2}"

# ---------- HEADER ----------
header = {
    "Name": "",
    "IBAN": "",
    "BIC": "",
    "StatementDate": "",
    "FromDate": "",
    "ToDate": "",
    "AccountNumber": "",
    "Currency": ""
}

# ---------- TRANSACTION STORAGE ----------
transactions = []
current = None
previous_balance = None


def flush_current():
    global current, previous_balance

    if current:
        amount = current["Debit"] or current["Kredi"]
        amount = amount.replace(",", "") if amount else None

        # Verify correctness using the balance difference logic
        if previous_balance is not None and current["Balance"]:
            curr_bal = float(current["Balance"].replace(",", ""))
            prev_bal = float(previous_balance.replace(",", ""))

            diff = round(curr_bal - prev_bal, 2)

            if amount:
                amt = float(amount)

                # If diff is negative, it should be a Debit
                if diff < 0:
                    current["Debit"] = current["Debit"] or current["Kredi"]
                    current["Kredi"] = ""
                else:
                    current["Kredi"] = current["Kredi"] or current["Debit"]
                    current["Debit"] = ""

        previous_balance = current["Balance"]
        transactions.append(current)

    current = None


# ---------- CLEAN LINE ----------
def clean_text(line):
    # remove all weird unicode
    return re.sub(r"[^\x00-\x7F]+", "", line).strip()


# ---------- PARSE PDF LINES ----------
for line in lines:
    line = clean_text(line)

    # ---------- HEADER FIELDS ----------
    if line.startswith("IBAN:"):
        header["IBAN"] = line.replace("IBAN:", "").strip()
        continue
    elif "BIC/Swift code:" in line:
        header["BIC"] = line.replace("BIC/Swift code:", "").strip()
        continue
    elif "DATE OF STATEMENT" in line:
        header["StatementDate"] = line.replace("DATE OF STATEMENT", "").strip()
        continue
    elif "FROM(NGA DATA)" in line:
        parts = line.replace("FROM(NGA DATA):", "").replace("TO(NE DATEN):", "").split()
        if len(parts) >= 2:
            header["FromDate"] = parts[0]
            header["ToDate"] = parts[-1]
        continue
    elif line.startswith("433"):
        header["AccountNumber"] = line.strip()
        header["Currency"] = "ALL"
        continue
    elif "PF" in line and header["Name"] == "":
        header["Name"] = line.strip()
        continue

    # ---------- OPENING BALANCE ----------
    if "OPENING BALANCE" in line:
        amt = re.findall(amount_pattern, line)
        if amt:
            previous_balance = amt[-1]
            transactions.append({
                "Date": header["FromDate"],
                "Pershkrimi": "OPENING BALANCE",
                "Debit": "",
                "Kredi": "",
                "Balance": previous_balance
            })
        continue

    # ---------- NEW TRANSACTION ----------
    if len(line) > 10 and date_pattern.match(line[:10]):
        flush_current()

        date = line[:10]
        money_values = re.findall(amount_pattern, line)
        description = re.sub(amount_pattern, "", line[10:]).strip()

        debit = ""
        credit = ""
        balance = ""

        if len(money_values) == 1:
            credit = money_values[0]  # fallback
        elif len(money_values) == 2:
            debit = money_values[0]
            balance = money_values[1]
        elif len(money_values) >= 3:
            debit, credit, balance = money_values[:3]

        current = {
            "Date": date,
            "Pershkrimi": description,
            "Debit": debit,
            "Kredi": credit,
            "Balance": balance
        }
        continue

    # ---------- MULTILINE DESCRIPTION ----------
    if current and line:
        current["Pershkrimi"] += " | " + line

flush_current()


# ---------- CLEAN FILENAME ----------
def clean_filename_value(value):
    value = re.sub(r"[^\x00-\x7F]+", "", value)            # remove unicode junk
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)           # keep only alphanumerics
    value = re.sub(r"_+", "_", value)                      # collapse "___" → "_"
    return value.strip("_")                                # trim edges


timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

name_part = re.split(r"\s{3,}", header["Name"])[0].strip()

name_clean = clean_filename_value(name_part)
iban_clean = clean_filename_value(header["IBAN"])
from_clean = clean_filename_value(header["FromDate"])
to_clean = clean_filename_value(header["ToDate"])

filename = f"{name_clean}_{iban_clean}_{from_clean}_{to_clean}_{timestamp}.csv"

# ---------- SAVE CSV ----------
df = pd.DataFrame(transactions)
df.to_csv(filename, index=False, quoting=csv.QUOTE_ALL)

print(f"Done! File created:\n{filename}")
