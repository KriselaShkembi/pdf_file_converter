import os
from flask import Flask, render_template, request, send_file
from datetime import datetime
import fitz
import pandas as pd
import csv
import re

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
RESULT_FOLDER = "results"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(RESULT_FOLDER, exist_ok=True)

# --------------------------------------------------------------------
#  UTILS
# --------------------------------------------------------------------

def clean_amount(x):
    """Convert string amount to float, handling PDF format: 14,700.00 (comma=thousands, dot=decimal)"""
    if not x:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    x_str = str(x).strip()
    # PDF format: 14,700.00 (comma for thousands, dot for decimal)
    # Remove comma (thousands separator), keep dot as decimal
    x_str = x_str.replace(",", "")
    try:
        return float(x_str)
    except:
        return 0.0


def clean_filename_value(value):
    """Clean value for use in filename"""
    value = re.sub(r"[^\x00-\x7F]+", "", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_")

# --------------------------------------------------------------------
#  POS PARSER
# --------------------------------------------------------------------

def convert_pos_pdf_to_csv(pdf_path, original_filename=None):
    """
    Convert POS merchant settlement PDF to CSV.
    Balance formula: Opening Balance - Debit + Credit = New Balance
    """
    doc = fitz.open(pdf_path)
    rows = []
    current = None
    opening_balance = None
    running_balance = None  # This will track: Opening Balance - Debit + Credit

    # Match PDF format: 14,700.00 (comma=thousands separator, dot=decimal) or 700.00
    AMOUNT_REGEX = r"[-]?\d{1,3}(?:,\d{3})*\.\d{2}|[-]?\d+\.\d{2}"

    def extract_amounts(line):
        return re.findall(AMOUNT_REGEX, line)

    lines = []
    for page in doc:
        lines.extend(page.get_text("text").split("\n"))

    for i, raw in enumerate(lines):
        raw = raw.strip()
        low = raw.lower()

        # OPENING BALANCE
        if raw.lower().startswith("opening balance"):
            m = re.search(AMOUNT_REGEX + r"$", raw)
            if m:
                opening_balance = clean_amount(m.group(0))
                running_balance = opening_balance
            continue

        # New transaction
        date_match = re.match(r"(\d{1,2}-[A-Za-z]{3}-\d{2})", raw)
        if date_match:
            # Save previous transaction (ensure balance is calculated)
            if current:
                # Finalize balance if not already calculated
                if not current["Balance"] and running_balance is not None:
                    debit_val = clean_amount(current["Debit"]) if current["Debit"] else 0.0
                    credit_val = clean_amount(current["Kredi"]) if current["Kredi"] else 0.0
                    running_balance = running_balance - debit_val + credit_val
                    current["Balance"] = f"{running_balance:,.2f}"
                rows.append(current)

            # Extract amounts from the transaction line
            amounts = extract_amounts(raw)
            
            current = {
                "SDate": date_match.group(1),
                "Pershkrimi": raw,  # Will accumulate all text here
                "TYPE": "",
                "ByOrderOf": "",
                "Beneficiary": "",
                "Debit": "",
                "Kredi": "",
                "Balance": ""  # Will be calculated after debit/credit are determined
            }

            # Determine transaction type from next line
            tx_type = ""
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip().lower()
                if "settlement" in next_line:
                    tx_type = "SETTLEMENT"
                elif "commission" in next_line:
                    tx_type = "COMMISSION"
            
            current["TYPE"] = tx_type

            # Parse amounts: typically 2 amounts (transaction amount, balance)
            if len(amounts) >= 2:
                # First amount is the transaction amount
                trans_amount = clean_amount(amounts[0])
                # Second amount is the balance from PDF
                pdf_balance = clean_amount(amounts[1])
                
                # Assign debit/credit based on type
                if tx_type == "SETTLEMENT":
                    current["Kredi"] = f"{trans_amount:,.2f}" if trans_amount else ""
                    current["Debit"] = ""
                elif tx_type == "COMMISSION":
                    current["Debit"] = f"{trans_amount:,.2f}" if trans_amount else ""
                    current["Kredi"] = ""
                else:
                    # If no type, try to infer from amount sign or context
                    if trans_amount < 0:
                        current["Debit"] = f"{abs(trans_amount):,.2f}"
                        current["Kredi"] = ""
                    else:
                        current["Kredi"] = f"{trans_amount:,.2f}" if trans_amount else ""
                        current["Debit"] = ""

                # Calculate balance using formula: Opening Balance - Debit + Credit
                # But first, use the balance from PDF if available (it's the closing balance)
                if pdf_balance:
                    current["Balance"] = f"{pdf_balance:,.2f}"
                    # Update running balance for next transaction
                    running_balance = pdf_balance
                elif running_balance is not None:
                    running_balance = running_balance - (trans_amount if tx_type == "COMMISSION" or (tx_type == "" and trans_amount < 0) else 0) + (trans_amount if tx_type == "SETTLEMENT" or (tx_type == "" and trans_amount >= 0) else 0)
                    current["Balance"] = f"{running_balance:,.2f}"

            elif len(amounts) == 1:
                # Only one amount found - use type to determine debit/credit
                trans_amount = clean_amount(amounts[0])
                if tx_type == "SETTLEMENT":
                    current["Kredi"] = f"{trans_amount:,.2f}" if trans_amount else ""
                    current["Debit"] = ""
                elif tx_type == "COMMISSION":
                    current["Debit"] = f"{trans_amount:,.2f}" if trans_amount else ""
                    current["Kredi"] = ""
                
                # Calculate balance
                if running_balance is not None:
                    debit_val = trans_amount if tx_type == "COMMISSION" else 0.0
                    credit_val = trans_amount if tx_type == "SETTLEMENT" else 0.0
                    running_balance = running_balance - debit_val + credit_val
                    current["Balance"] = f"{running_balance:,.2f}"

            continue

        # Additional lines - append to description and extract metadata
        if current:
            current["Pershkrimi"] += " " + raw
            
            # POS/Ref info is included in Pershkrimi for verification

            # Also check for TYPE in continuation lines (only if not already set)
            if "settlement" in low and not current["TYPE"]:
                current["TYPE"] = "SETTLEMENT"
                # If we haven't assigned debit/credit yet, do it now
                if not current["Kredi"] and not current["Debit"]:
                    amounts = extract_amounts(current["Pershkrimi"])
                    if amounts:
                        amount = clean_amount(amounts[0])
                        current["Kredi"] = f"{amount:,.2f}" if amount else ""
                        current["Debit"] = ""
                        # Calculate balance: Opening Balance - Debit + Credit
                        if running_balance is not None:
                            running_balance = running_balance - 0.0 + amount
                            current["Balance"] = f"{running_balance:,.2f}"
                elif not current["Balance"] and running_balance is not None:
                    # TYPE found but balance not calculated yet - recalculate
                    debit_val = clean_amount(current["Debit"]) if current["Debit"] else 0.0
                    credit_val = clean_amount(current["Kredi"]) if current["Kredi"] else 0.0
                    running_balance = running_balance - debit_val + credit_val
                    current["Balance"] = f"{running_balance:,.2f}"
            elif "commission" in low and not current["TYPE"]:
                current["TYPE"] = "COMMISSION"
                # If we haven't assigned debit/credit yet, do it now
                if not current["Kredi"] and not current["Debit"]:
                    amounts = extract_amounts(current["Pershkrimi"])
                    if amounts:
                        amount = clean_amount(amounts[0])
                        current["Debit"] = f"{amount:,.2f}" if amount else ""
                        current["Kredi"] = ""
                        # Calculate balance: Opening Balance - Debit + Credit
                        if running_balance is not None:
                            running_balance = running_balance - amount + 0.0
                            current["Balance"] = f"{running_balance:,.2f}"
                elif not current["Balance"] and running_balance is not None:
                    # TYPE found but balance not calculated yet - recalculate
                    debit_val = clean_amount(current["Debit"]) if current["Debit"] else 0.0
                    credit_val = clean_amount(current["Kredi"]) if current["Kredi"] else 0.0
                    running_balance = running_balance - debit_val + credit_val
                    current["Balance"] = f"{running_balance:,.2f}"

    # Save last transaction (ensure balance is calculated)
    if current:
        # Finalize balance if not already calculated
        if not current["Balance"] and running_balance is not None:
            debit_val = clean_amount(current["Debit"]) if current["Debit"] else 0.0
            credit_val = clean_amount(current["Kredi"]) if current["Kredi"] else 0.0
            running_balance = running_balance - debit_val + credit_val
            current["Balance"] = f"{running_balance:,.2f}"
        rows.append(current)

    # Add opening balance row at the beginning
    if opening_balance is not None:
        rows.insert(0, {
            "SDate": "",
            "Pershkrimi": "Opening Balance",
            "TYPE": "",
            "ByOrderOf": "",
            "Beneficiary": "",
            "Debit": "",
            "Kredi": "",
            "Balance": f"{opening_balance:,.2f}"
        })

    # Save CSV with proper column order
    df = pd.DataFrame(rows)
    # Ensure columns are in the correct order: SDate, Pershkrimi, TYPE, ByOrderOf, Beneficiary, Debit, Kredi, Balance
    columns_order = ["SDate", "Pershkrimi", "TYPE", "ByOrderOf", "Beneficiary", "Debit", "Kredi", "Balance"]
    df = df[[col for col in columns_order if col in df.columns]]
    
    # Generate filename: original_filename + date_processed
    if original_filename:
        # Get base name without extension
        base_name = os.path.splitext(os.path.basename(original_filename))[0]
        base_name_clean = clean_filename_value(base_name)
        date_processed = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{base_name_clean}_{date_processed}.csv"
    else:
        filename = "bkt_pos_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
    
    out_file = os.path.join(RESULT_FOLDER, filename)
    df.to_csv(out_file, index=False, quoting=csv.QUOTE_ALL)
    return out_file


# --------------------------------------------------------------------
#  BANK STATEMENT PARSER
# --------------------------------------------------------------------

def convert_bank_pdf_to_csv(pdf_path, original_filename=None):
    """
    Convert bank statement PDF to CSV.
    Balance formula: Opening Balance - Debit + Credit = New Balance
    """
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text()

    lines = full_text.split("\n")

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
    opening_balance = None
    running_balance = None
    current = None

    def flush():
        nonlocal current, running_balance
        if current:
            # Use balance from PDF if available (it's the closing/end balance)
            if current["Balance"]:
                balance_val = clean_amount(current["Balance"])
                current["Balance"] = f"{balance_val:,.2f}"  # Format it properly
                # Update running balance for next transaction
                running_balance = balance_val
            elif running_balance is not None:
                # Calculate balance if not provided in PDF: Opening Balance - Debit + Credit
                debit_val = clean_amount(current["Debit"]) if current["Debit"] else 0.0
                credit_val = clean_amount(current["Kredi"]) if current["Kredi"] else 0.0
                new_balance = running_balance - debit_val + credit_val
                current["Balance"] = f"{new_balance:,.2f}"
                running_balance = new_balance
            
            transactions.append(current)
        current = None

    for line in lines:
        line = line.strip()

        # HEADER
        if line.startswith("IBAN:"):
            header["IBAN"] = line.replace("IBAN:", "").strip()
        elif "BIC/Swift code:" in line:
            header["BIC"] = line.replace("BIC/Swift code:", "").strip()
        elif "DATE OF STATEMENT" in line:
            header["StatementDate"] = line.replace("DATE OF STATEMENT", "").strip()
        elif "FROM(NGA DATA)" in line or "FROM" in line.upper():
            parts = line.replace("FROM(NGA DATA):", "").replace("TO(NE DATEN):", "").replace("FROM:", "").replace("TO:", "").split()
            if len(parts) >= 2:
                header["FromDate"] = parts[0]
                header["ToDate"] = parts[-1]
        elif line.startswith("433"):
            header["AccountNumber"] = line.strip()
            header["Currency"] = "ALL"
        elif "PF" in line and header["Name"] == "":
            header["Name"] = line

        # Look for opening balance
        if "OPENING BALANCE" in line.upper() or "OPENING" in line.upper():
            money = re.findall(amount_pattern, line)
            if money:
                opening_balance = clean_amount(money[-1])
                running_balance = opening_balance

        # TRANSACTIONS
        if date_pattern.match(line[:10]):
            flush()

            date = line[:10]
            money = re.findall(amount_pattern, line)
            desc = re.sub(amount_pattern, "", line[10:]).strip()

            debit = ""
            credit = ""
            balance = ""

            if len(money) == 1:
                # Only one amount - need to determine if debit or credit
                amount = money[0]
                amount_val = clean_amount(amount)
                
                # If we have running balance, we can infer
                if running_balance is not None:
                    # Try to find balance elsewhere or calculate
                    # For now, default to credit
                    credit = amount
                else:
                    credit = amount
                    
            elif len(money) == 2:
                # Two amounts: transaction amount and balance
                amount = money[0]
                balance = money[1]
                
                amount_val = clean_amount(amount)
                balance_val = clean_amount(balance)
                
                # Determine if debit or credit using the formula
                # Formula: new_balance = prev_balance - debit + credit
                # So: diff = new_balance - prev_balance = -debit + credit
                # If diff > 0: credit transaction
                # If diff < 0: debit transaction
                
                if running_balance is not None:
                    # Calculate what the transaction should be
                    diff = balance_val - running_balance
                    
                    # If difference equals amount, it's a credit
                    if abs(abs(diff) - amount_val) < 0.01:
                        if diff > 0:
                            credit = amount
                            debit = ""
                        else:
                            debit = amount
                            credit = ""
                    else:
                        # Use amount sign as fallback
                        if amount_val >= 0:
                            credit = amount
                            debit = ""
                        else:
                            debit = amount.replace("-", "")
                            credit = ""
                else:
                    # No running balance, default to credit
                    credit = amount
                    debit = ""
                
            else:
                continue

            current = {
                "SDate": date,
                "Pershkrimi": desc,  # Will accumulate all text here
                "TYPE": "",  # Bank statements may not have explicit TYPE
                "ByOrderOf": "",
                "Beneficiary": "",
                "Debit": debit,
                "Kredi": credit,
                "Balance": balance
            }

        elif current:
            txt = line.lower()

            if "by order of" in txt:
                current["ByOrderOf"] = line.split(":", 1)[1].strip().lstrip(":")
                current["Pershkrimi"] += " | " + line  # Also add to description
            elif "beneficiary" in txt or "ben:" in txt:
                current["Beneficiary"] = line.split(":", 1)[1].strip().lstrip(":")
                current["Pershkrimi"] += " | " + line  # Also add to description
            else:
                current["Pershkrimi"] += " | " + line  # Put everything in description

    flush()

    # Add opening balance row if found
    if opening_balance is not None:
        transactions.insert(0, {
            "SDate": "",
            "Pershkrimi": "Opening Balance",
            "TYPE": "",
            "ByOrderOf": "",
            "Beneficiary": "",
            "Debit": "",
            "Kredi": "",
            "Balance": f"{opening_balance:,.2f}"
        })

    # OUTPUT
    # Generate filename: original_filename + date_processed
    if original_filename:
        # Get base name without extension
        base_name = os.path.splitext(os.path.basename(original_filename))[0]
        base_name_clean = clean_filename_value(base_name)
        date_processed = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{base_name_clean}_{date_processed}.csv"
    else:
        # Fallback to old naming if no original filename provided
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name_clean = clean_filename_value(header["Name"])
        iban_clean = clean_filename_value(header["IBAN"])
        from_clean = clean_filename_value(header["FromDate"])
        to_clean = clean_filename_value(header["ToDate"])
        filename = f"{name_clean}_{iban_clean}_{from_clean}_{to_clean}_{timestamp}.csv"
    
    output_path = os.path.join(RESULT_FOLDER, filename)

    df = pd.DataFrame(transactions)
    # Ensure columns are in the correct order: SDate, Pershkrimi, TYPE, ByOrderOf, Beneficiary, Debit, Kredi, Balance
    columns_order = ["SDate", "Pershkrimi", "TYPE", "ByOrderOf", "Beneficiary", "Debit", "Kredi", "Balance"]
    df = df[[col for col in columns_order if col in df.columns]]
    
    df.to_csv(output_path, index=False, quoting=csv.QUOTE_ALL)

    return output_path


# --------------------------------------------------------------------
#  AUTO DETECT
# --------------------------------------------------------------------

def convert_pdf_to_csv(pdf_path, mode="auto", original_filename=None):
    """Auto-detect PDF type and convert"""
    txt = fitz.open(pdf_path)[0].get_text().lower()

    if mode == "bank":
        return convert_bank_pdf_to_csv(pdf_path, original_filename)
    if mode == "pos":
        return convert_pos_pdf_to_csv(pdf_path, original_filename)

    # auto-detect
    if "pos" in txt and "settlement" in txt:
        return convert_pos_pdf_to_csv(pdf_path, original_filename)

    return convert_bank_pdf_to_csv(pdf_path, original_filename)


# --------------------------------------------------------------------
#  ROUTES
# --------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    source = request.form.get("source", "local")
    mode = request.form.get("mode", "auto")

    if source == "server":
        filename = request.form.get("server_file")
        if not filename:
            return "No server file provided."
        pdf_path = os.path.join(UPLOAD_FOLDER, filename)
        if not os.path.exists(pdf_path):
            return f"File not found on server: {pdf_path}"
        original_filename = filename
    else:
        file = request.files.get("pdf")
        if not file:
            return "No file uploaded."

        original_filename = file.filename
        pdf_path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(pdf_path)

    output_csv = convert_pdf_to_csv(pdf_path, mode, original_filename)
    return send_file(output_csv, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True)
