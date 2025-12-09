import os
from flask import Flask, render_template, request, send_file
from datetime import datetime
import pandas as pd
import fitz
import re
import csv

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
RESULT_FOLDER = "results"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULT_FOLDER, exist_ok=True)

# ---------------- CLEAN FILENAME ----------------
def clean_filename_value(value):
    value = re.sub(r"[^\x00-\x7F]+", "", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_")

# ---------------- PDF CONVERTER FUNCTION ----------------
def convert_pdf_to_csv(pdf_path):
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text()

    lines = full_text.split("\n")

    # Regex
    date_pattern = re.compile(r"\d{2}-[A-Za-z]{3}-\d{2}")
    amount_pattern = r"[\d,]+\.\d{2}"

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

    transactions = []
    prev_balance = None
    current = None

    def flush():
        nonlocal current
        if current:
            transactions.append(current)
        current = None

    for line in lines:
        line = line.strip()

        # HEADER EXTRACTION
        if line.startswith("IBAN:"):
            header["IBAN"] = line.replace("IBAN:", "").strip()
        elif "BIC/Swift code:" in line:
            header["BIC"] = line.replace("BIC/Swift code:", "").strip()
        elif "DATE OF STATEMENT" in line:
            header["StatementDate"] = line.replace("DATE OF STATEMENT", "").strip()
        elif "FROM(NGA DATA)" in line:
            parts = line.replace("FROM(NGA DATA):", "").replace("TO(NE DATEN):", "").split()
            if len(parts) >= 2:
                header["FromDate"] = parts[0]
                header["ToDate"] = parts[-1]
        elif line.startswith("433"):
            header["AccountNumber"] = line.strip()
            header["Currency"] = "ALL"
        elif "PF" in line and header["Name"] == "":
            header["Name"] = line

        # --- TRANSACTIONS ---
        if date_pattern.match(line[:10]):
            flush()

            date = line[:10]
            money = re.findall(amount_pattern, line)
            desc = re.sub(amount_pattern, "", line[10:]).strip()

            debit = ""
            credit = ""
            balance = ""

            if len(money) == 1:
                amount = money[0]
                balance = None
            elif len(money) == 2:
                amount = money[0]
                balance = money[1]
            else:
                continue

            # Determine debit or credit using balance math
            if prev_balance and balance:
                try:
                    prev_val = float(prev_balance.replace(",", ""))
                    bal_val = float(balance.replace(",", ""))
                    amt_val = float(amount.replace(",", ""))

                    if abs(prev_val - amt_val - bal_val) < 0.01:
                        debit = amount
                    else:
                        credit = amount
                except:
                    credit = amount
            else:
                credit = amount

            prev_balance = balance

            current = {
                "Date": date,
                "Pershkrimi": desc,
                "Debit": debit,
                "Kredi": credit,
                "Balance": balance or ""
            }

        elif current:
            current["Pershkrimi"] += " | " + line

    flush()

    # Create filename
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    name_part = re.split(r"\s{3,}", header["Name"])[0].strip()
    name_clean = clean_filename_value(name_part)

    iban_clean = clean_filename_value(header["IBAN"])
    from_clean = clean_filename_value(header["FromDate"])
    to_clean = clean_filename_value(header["ToDate"])

    filename = f"{name_clean}_{iban_clean}_{from_clean}_{to_clean}_{timestamp}.csv"
    output_path = os.path.join(RESULT_FOLDER, filename)

    df = pd.DataFrame(transactions)
    df.to_csv(output_path, index=False, quoting=csv.QUOTE_ALL)

    return output_path


# ---------------- ROUTES ----------------

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload():
    file = request.files["pdf"]
    if not file:
        return "No file uploaded."

    path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(path)

    output_csv = convert_pdf_to_csv(path)

    return send_file(output_csv, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True)
