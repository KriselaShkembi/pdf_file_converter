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


def extract_by_order_of(line):
    """Extract ByOrderOf value from line using various patterns"""
    if not line:
        return ""
    
    txt = line.lower().strip()
    line_original = line.strip()
    
    # Pattern: "Ft - By Order Of VALUE" (case insensitive, flexible spacing)
    # Match: "ft - by order of", "ft- by order of", "ft -by order of", etc.
    pattern = re.search(r'ft\s*-\s*by\s+order\s+of\s*[:-]?\s*(.+)', txt, re.IGNORECASE)
    if pattern:
        value = pattern.group(1).strip().lstrip("-:").strip()
        if value:
            return value
    
    # Pattern: "By Order Of: VALUE" or "By Order Of VALUE" (case insensitive)
    pattern = re.search(r'by\s+order\s+of\s*[:-]?\s*(.+)', txt, re.IGNORECASE)
    if pattern:
        value = pattern.group(1).strip().lstrip("-:").strip()
        if value:
            return value
    
    # Pattern: "Order Of: VALUE" or "Order Of VALUE" (but not "By Order Of")
    if "by order of" not in txt:
        pattern = re.search(r'order\s+of\s*[:-]?\s*(.+)', txt, re.IGNORECASE)
        if pattern:
            value = pattern.group(1).strip().lstrip("-:").strip()
            if value:
                return value
    
    return ""


def extract_beneficiary(line):
    """Extract Beneficiary value from line using various patterns"""
    if not line:
        return ""
    
    txt = line.lower().strip()
    line_original = line.strip()
    
    # Pattern: "Ft - Ben -VALUE" or "Ft - Ben - VALUE" (case insensitive, flexible spacing)
    # This handles "Ft - Ben -MC DONALD S  SHPK" -> extracts "MC DONALD S  SHPK"
    pattern = re.search(r'ft\s*-\s*ben\s*-\s*(.+)', txt, re.IGNORECASE)
    if pattern:
        value = pattern.group(1).strip().lstrip("-:").strip()
        if value:
            return value
    
    # Pattern: "Ft - Ben VALUE" (without dash after ben)
    pattern = re.search(r'ft\s*-\s*ben\s+[:-]?\s*(.+)', txt, re.IGNORECASE)
    if pattern:
        value = pattern.group(1).strip().lstrip("-:").strip()
        if value:
            return value
    
    # Pattern: "Beneficiary: VALUE" or "Beneficiary VALUE" (case insensitive)
    pattern = re.search(r'beneficiary\s*[:-]?\s*(.+)', txt, re.IGNORECASE)
    if pattern:
        value = pattern.group(1).strip().lstrip("-:").strip()
        if value:
            return value
    
    # Pattern: "Ben: VALUE" or "Ben - VALUE" or "Ben VALUE" (but not "Beneficiary")
    if "beneficiary" not in txt:
        # Try "Ben -" pattern first
        pattern = re.search(r'\bben\s*-\s*(.+)', txt, re.IGNORECASE)
        if pattern:
            value = pattern.group(1).strip().lstrip("-:").strip()
            if value:
                return value
        
        # Try "Ben:" or "Ben " pattern
        pattern = re.search(r'\bben\s*[:-]?\s*(.+)', txt, re.IGNORECASE)
        if pattern:
            value = pattern.group(1).strip().lstrip("-:").strip()
            if value:
                return value
    
    return ""

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
                    calculated_balance = running_balance - debit_val + credit_val
                    current["Balance"] = f"{calculated_balance:,.2f}"
                    
                    # Calculate difference if closing balance exists
                    if current["Closing Balance"]:
                        closing_val = clean_amount(current["Closing Balance"])
                        diff = calculated_balance - closing_val
                        current["Difference"] = f"{diff:,.2f}"
                    
                    running_balance = calculated_balance
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
                "Balance": "",  # Calculated: Opening Balance - Debit + Credit
                "Closing Balance": "",  # From PDF/CSV
                "Difference": ""  # Should be 0
            }

            # Determine transaction type from next line
            tx_type = ""
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip().lower()
                if "settlement" in next_line:
                    tx_type = "SETTLEMENT"
                elif "commission" in next_line:
                    tx_type = "COMMISSION"
                elif "cash withdrawal" in next_line or "withdrawal" in next_line:
                    tx_type = "CASH WITHDRAWAL"
                elif "cash deposit" in next_line or ("cash" in next_line and "deposit" in next_line):
                    tx_type = "CASH DEPOSIT"
            
            current["TYPE"] = tx_type

            # Parse amounts: typically 2 amounts (transaction amount, balance)
            if len(amounts) >= 2:
                # First amount is the transaction amount
                trans_amount = clean_amount(amounts[0])
                # Second amount is the balance from PDF
                pdf_balance = clean_amount(amounts[1])
                
                # Assign debit/credit based on type
                if tx_type == "SETTLEMENT" or tx_type == "CASH DEPOSIT":
                    current["Kredi"] = f"{trans_amount:,.2f}" if trans_amount else ""
                    current["Debit"] = ""
                elif tx_type == "COMMISSION" or tx_type == "CASH WITHDRAWAL":
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

                # Store closing balance from PDF (source of truth)
                if pdf_balance:
                    current["Closing Balance"] = f"{pdf_balance:,.2f}"
                
                # Calculate balance: Opening Balance - Debit + Credit
                # Note: Debit values are always positive (amounts are stored as positive), so use abs() if needed
                if running_balance is not None:
                    if tx_type == "COMMISSION" or tx_type == "CASH WITHDRAWAL":
                        debit_val = abs(trans_amount)
                        credit_val = 0.0
                    elif tx_type == "SETTLEMENT" or tx_type == "CASH DEPOSIT":
                        debit_val = 0.0
                        credit_val = abs(trans_amount)
                    elif trans_amount < 0:
                        debit_val = abs(trans_amount)
                        credit_val = 0.0
                    else:
                        debit_val = 0.0
                        credit_val = abs(trans_amount)
                    calculated_balance = running_balance - debit_val + credit_val
                    current["Balance"] = f"{calculated_balance:,.2f}"
                    
                    # Calculate difference: should be 0 if calculations are correct
                    if pdf_balance:
                        diff = calculated_balance - pdf_balance
                        # Round to 2 decimal places to handle floating point precision
                        diff_rounded = round(diff, 2)
                        current["Difference"] = f"{diff_rounded:,.2f}"
                    else:
                        current["Difference"] = ""
                    
                    # Update running balance for next transaction (use calculated)
                    running_balance = calculated_balance
                elif pdf_balance:
                    # No running balance, use PDF balance
                    current["Balance"] = f"{pdf_balance:,.2f}"
                    current["Difference"] = "0.00"

            elif len(amounts) == 1:
                # Only one amount found - use type to determine debit/credit
                trans_amount = clean_amount(amounts[0])
                if tx_type == "SETTLEMENT" or tx_type == "CASH DEPOSIT":
                    current["Kredi"] = f"{trans_amount:,.2f}" if trans_amount else ""
                    current["Debit"] = ""
                elif tx_type == "COMMISSION" or tx_type == "CASH WITHDRAWAL":
                    current["Debit"] = f"{trans_amount:,.2f}" if trans_amount else ""
                    current["Kredi"] = ""
                
                # Calculate balance
                if running_balance is not None:
                    if tx_type == "COMMISSION" or tx_type == "CASH WITHDRAWAL":
                        debit_val = abs(trans_amount)
                        credit_val = 0.0
                    elif tx_type == "SETTLEMENT" or tx_type == "CASH DEPOSIT":
                        debit_val = 0.0
                        credit_val = abs(trans_amount)
                    else:
                        debit_val = 0.0
                        credit_val = 0.0
                    calculated_balance = running_balance - debit_val + credit_val
                    current["Balance"] = f"{calculated_balance:,.2f}"
                    current["Closing Balance"] = ""  # No closing balance from PDF
                    current["Difference"] = ""
                    running_balance = calculated_balance

            continue

        # Additional lines - append to description and extract metadata
        if current:
            # Extract ByOrderOf using unified extraction if not already found
            if not current["ByOrderOf"]:
                by_order = extract_by_order_of(raw)
                if by_order:
                    current["ByOrderOf"] = by_order
            
            # Extract Beneficiary using unified extraction if not already found
            if not current["Beneficiary"]:
                beneficiary = extract_beneficiary(raw)
                if beneficiary:
                    current["Beneficiary"] = beneficiary
            
            current["Pershkrimi"] += " " + raw

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
                            calculated_balance = running_balance - 0.0 + amount
                            current["Balance"] = f"{calculated_balance:,.2f}"
                            if current["Closing Balance"]:
                                closing_val = clean_amount(current["Closing Balance"])
                                diff = calculated_balance - closing_val
                                current["Difference"] = f"{diff:,.2f}"
                            running_balance = calculated_balance
                elif not current["Balance"] and running_balance is not None:
                    # TYPE found but balance not calculated yet - recalculate
                    debit_val = clean_amount(current["Debit"]) if current["Debit"] else 0.0
                    credit_val = clean_amount(current["Kredi"]) if current["Kredi"] else 0.0
                    calculated_balance = running_balance - debit_val + credit_val
                    current["Balance"] = f"{calculated_balance:,.2f}"
                    if current["Closing Balance"]:
                        closing_val = clean_amount(current["Closing Balance"])
                        diff = calculated_balance - closing_val
                        current["Difference"] = f"{diff:,.2f}"
                    running_balance = calculated_balance
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
                            calculated_balance = running_balance - amount + 0.0
                            current["Balance"] = f"{calculated_balance:,.2f}"
                            if current["Closing Balance"]:
                                closing_val = clean_amount(current["Closing Balance"])
                                diff = calculated_balance - closing_val
                                current["Difference"] = f"{diff:,.2f}"
                            running_balance = calculated_balance
                elif not current["Balance"] and running_balance is not None:
                    # TYPE found but balance not calculated yet - recalculate
                    debit_val = clean_amount(current["Debit"]) if current["Debit"] else 0.0
                    credit_val = clean_amount(current["Kredi"]) if current["Kredi"] else 0.0
                    calculated_balance = running_balance - debit_val + credit_val
                    current["Balance"] = f"{calculated_balance:,.2f}"
                    if current["Closing Balance"]:
                        closing_val = clean_amount(current["Closing Balance"])
                        diff = calculated_balance - closing_val
                        current["Difference"] = f"{diff:,.2f}"
                    running_balance = calculated_balance
            elif ("cash withdrawal" in low or "withdrawal" in low) and not current["TYPE"]:
                current["TYPE"] = "CASH WITHDRAWAL"
                # If we haven't assigned debit/credit yet, do it now
                if not current["Kredi"] and not current["Debit"]:
                    amounts = extract_amounts(current["Pershkrimi"])
                    if amounts:
                        amount = clean_amount(amounts[0])
                        current["Debit"] = f"{amount:,.2f}" if amount else ""
                        current["Kredi"] = ""
                        # Calculate balance: Opening Balance - Debit + Credit
                        if running_balance is not None:
                            calculated_balance = running_balance - amount + 0.0
                            current["Balance"] = f"{calculated_balance:,.2f}"
                            if current["Closing Balance"]:
                                closing_val = clean_amount(current["Closing Balance"])
                                diff = calculated_balance - closing_val
                                current["Difference"] = f"{diff:,.2f}"
                            running_balance = calculated_balance
                elif not current["Balance"] and running_balance is not None:
                    # TYPE found but balance not calculated yet - recalculate
                    debit_val = clean_amount(current["Debit"]) if current["Debit"] else 0.0
                    credit_val = clean_amount(current["Kredi"]) if current["Kredi"] else 0.0
                    calculated_balance = running_balance - debit_val + credit_val
                    current["Balance"] = f"{calculated_balance:,.2f}"
                    if current["Closing Balance"]:
                        closing_val = clean_amount(current["Closing Balance"])
                        diff = calculated_balance - closing_val
                        current["Difference"] = f"{diff:,.2f}"
                    running_balance = calculated_balance
            elif ("cash deposit" in low or ("cash" in low and "deposit" in low)) and not current["TYPE"]:
                current["TYPE"] = "CASH DEPOSIT"
                # If we haven't assigned debit/credit yet, do it now
                if not current["Kredi"] and not current["Debit"]:
                    amounts = extract_amounts(current["Pershkrimi"])
                    if amounts:
                        amount = clean_amount(amounts[0])
                        current["Kredi"] = f"{amount:,.2f}" if amount else ""
                        current["Debit"] = ""
                        # Calculate balance: Opening Balance - Debit + Credit
                        if running_balance is not None:
                            calculated_balance = running_balance - 0.0 + amount
                            current["Balance"] = f"{calculated_balance:,.2f}"
                            if current["Closing Balance"]:
                                closing_val = clean_amount(current["Closing Balance"])
                                diff = calculated_balance - closing_val
                                current["Difference"] = f"{diff:,.2f}"
                            running_balance = calculated_balance
                elif not current["Balance"] and running_balance is not None:
                    # TYPE found but balance not calculated yet - recalculate
                    debit_val = clean_amount(current["Debit"]) if current["Debit"] else 0.0
                    credit_val = clean_amount(current["Kredi"]) if current["Kredi"] else 0.0
                    calculated_balance = running_balance - debit_val + credit_val
                    current["Balance"] = f"{calculated_balance:,.2f}"
                    if current["Closing Balance"]:
                        closing_val = clean_amount(current["Closing Balance"])
                        diff = calculated_balance - closing_val
                        current["Difference"] = f"{diff:,.2f}"
                    running_balance = calculated_balance

        # Save last transaction (ensure balance is calculated)
        if current:
            # Finalize balance if not already calculated
            if not current["Balance"] and running_balance is not None:
                debit_val = clean_amount(current["Debit"]) if current["Debit"] else 0.0
                credit_val = clean_amount(current["Kredi"]) if current["Kredi"] else 0.0
                calculated_balance = running_balance - debit_val + credit_val
                current["Balance"] = f"{calculated_balance:,.2f}"
                if current["Closing Balance"]:
                    closing_val = clean_amount(current["Closing Balance"])
                    diff = calculated_balance - closing_val
                    current["Difference"] = f"{diff:,.2f}"
                running_balance = calculated_balance
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
            "Balance": f"{opening_balance:,.2f}",
            "Closing Balance": f"{opening_balance:,.2f}",
            "Difference": "0.00"
        })

    # Save CSV with proper column order
    df = pd.DataFrame(rows)
    # Ensure columns are in the correct order: SDate, Pershkrimi, TYPE, ByOrderOf, Beneficiary, Debit, Kredi, Balance, Closing Balance, Difference
    columns_order = ["SDate", "Pershkrimi", "TYPE", "ByOrderOf", "Beneficiary", "Debit", "Kredi", "Balance", "Closing Balance", "Difference"]
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
            # Calculate balance: Opening Balance - Debit + Credit
            if running_balance is not None:
                debit_val = clean_amount(current["Debit"]) if current["Debit"] else 0.0
                credit_val = clean_amount(current["Kredi"]) if current["Kredi"] else 0.0
                calculated_balance = running_balance - debit_val + credit_val
                current["Balance"] = f"{calculated_balance:,.2f}"
                
                # Format closing balance from PDF if available
                if current["Closing Balance"]:
                    closing_val = clean_amount(current["Closing Balance"])
                    current["Closing Balance"] = f"{closing_val:,.2f}"
                    # Calculate difference
                    diff = calculated_balance - closing_val
                    current["Difference"] = f"{diff:,.2f}"
                else:
                    current["Difference"] = ""
                
                # Update running balance for next transaction (use calculated)
                running_balance = calculated_balance
            
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
                "Balance": "",  # Calculated: Opening Balance - Debit + Credit
                "Closing Balance": balance,  # From PDF/CSV
                "Difference": ""  # Should be 0
            }

        elif current:
            txt = line.lower()
            line_clean = line.strip()

            # Extract ByOrderOf using unified extraction if not already found
            if not current["ByOrderOf"]:
                by_order = extract_by_order_of(line)
                if by_order:
                    current["ByOrderOf"] = by_order
            
            # Extract Beneficiary using unified extraction if not already found
            if not current["Beneficiary"]:
                beneficiary = extract_beneficiary(line)
                if beneficiary:
                    current["Beneficiary"] = beneficiary
            
            # Always add to description for verification
            current["Pershkrimi"] += " | " + line_clean

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
            "Balance": f"{opening_balance:,.2f}",
            "Closing Balance": f"{opening_balance:,.2f}",
            "Difference": "0.00"
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
    # Ensure columns are in the correct order: SDate, Pershkrimi, TYPE, ByOrderOf, Beneficiary, Debit, Kredi, Balance, Closing Balance, Difference
    columns_order = ["SDate", "Pershkrimi", "TYPE", "ByOrderOf", "Beneficiary", "Debit", "Kredi", "Balance", "Closing Balance", "Difference"]
    df = df[[col for col in columns_order if col in df.columns]]
    
    df.to_csv(output_path, index=False, quoting=csv.QUOTE_ALL)

    return output_path


# --------------------------------------------------------------------
#  UNIFIED MIXED PARSER (POS + Bank Transactions)
# --------------------------------------------------------------------

def convert_mixed_pdf_to_csv(pdf_path, original_filename=None):
    """
    Convert PDF containing both POS and bank transactions to CSV.
    Detects transaction type per transaction and applies appropriate parsing logic.
    Balance formula: Opening Balance - Debit + Credit = New Balance
    """
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text()
    
    lines = full_text.split("\n")
    
    # Match PDF format: 14,700.00 (comma=thousands separator, dot=decimal) or 700.00
    AMOUNT_REGEX = r"[-]?\d{1,3}(?:,\d{3})*\.\d{2}|[-]?\d+\.\d{2}"
    amount_pattern = r"[\d,]+\.\d{2}"
    # Date pattern with capturing group for consistency
    date_pattern = re.compile(r"(\d{2}-[A-Za-z]{3}-\d{2})")
    
    rows = []
    opening_balance = None
    running_balance = None
    current_transaction_lines = []
    current = None
    
    def extract_amounts(line):
        return re.findall(AMOUNT_REGEX, line)
    
    def is_pos_transaction(transaction_lines):
        """Check if transaction has specific type keywords (POS or cash transactions)"""
        for line in transaction_lines:
            low = line.lower()
            if any(keyword in low for keyword in ["settlement", "commission", "cash withdrawal", "cash deposit", "withdrawal", "deposit"]):
                return True
        return False
    
    def get_pos_transaction_type(transaction_lines):
        """Get transaction type (SETTLEMENT, COMMISSION, CASH WITHDRAWAL, CASH DEPOSIT)"""
        for line in transaction_lines:
            low = line.lower()
            if "settlement" in low:
                return "SETTLEMENT"
            elif "commission" in low:
                return "COMMISSION"
            elif "cash withdrawal" in low or "withdrawal" in low:
                return "CASH WITHDRAWAL"
            elif "cash deposit" in low or ("cash" in low and "deposit" in low):
                return "CASH DEPOSIT"
        return ""
    
    def parse_pos_transaction(transaction_lines, running_bal):
        """Parse POS transaction"""
        nonlocal running_balance
        
        # Join all lines to get full transaction text
        full_text = " | ".join(transaction_lines)
        
        # Extract date from first line
        first_line = transaction_lines[0] if transaction_lines else ""
        date_match = date_pattern.match(first_line[:10]) or re.match(r"(\d{1,2}-[A-Za-z]{3}-\d{2})", first_line)
        if date_match:
            try:
                date = date_match.group(1)
            except (IndexError, AttributeError):
                # Fallback to group(0) if group(1) doesn't exist
                date = date_match.group(0) if date_match else first_line[:10]
        else:
            date = first_line[:10] if len(first_line) >= 10 else ""
        
        # Get transaction type
        tx_type = get_pos_transaction_type(transaction_lines)
        
        # Extract amounts
        amounts = extract_amounts(full_text)
        
        trans = {
            "SDate": date,
            "Pershkrimi": full_text,
            "TYPE": tx_type,
            "ByOrderOf": "",
            "Beneficiary": "",
            "Debit": "",
            "Kredi": "",
            "Balance": "",
            "Closing Balance": "",
            "Difference": ""
        }
        
        if len(amounts) >= 2:
            trans_amount = clean_amount(amounts[0])
            pdf_balance = clean_amount(amounts[1])
            
            # Store closing balance from PDF (source of truth)
            if pdf_balance:
                trans["Closing Balance"] = f"{pdf_balance:,.2f}"
            
            # Assign debit/credit based on type
            if tx_type == "SETTLEMENT" or tx_type == "CASH DEPOSIT":
                trans["Kredi"] = f"{trans_amount:,.2f}" if trans_amount else ""
                trans["Debit"] = ""
            elif tx_type == "COMMISSION" or tx_type == "CASH WITHDRAWAL":
                trans["Debit"] = f"{trans_amount:,.2f}" if trans_amount else ""
                trans["Kredi"] = ""
            
            # Calculate balance using formula: Opening Balance - Debit + Credit
            # Note: Debit values are always positive (amounts are stored as positive)
            if running_bal is not None:
                if tx_type == "COMMISSION" or tx_type == "CASH WITHDRAWAL":
                    debit_val = abs(trans_amount)
                    credit_val = 0.0
                elif tx_type == "SETTLEMENT" or tx_type == "CASH DEPOSIT":
                    debit_val = 0.0
                    credit_val = abs(trans_amount)
                else:
                    debit_val = 0.0
                    credit_val = 0.0
                calculated_balance = running_bal - debit_val + credit_val
                trans["Balance"] = f"{calculated_balance:,.2f}"
                
                # Calculate difference: should be 0 if calculations are correct
                if pdf_balance:
                    diff = calculated_balance - pdf_balance
                    # Round to 2 decimal places to handle floating point precision
                    diff_rounded = round(diff, 2)
                    trans["Difference"] = f"{diff_rounded:,.2f}"
                else:
                    trans["Difference"] = ""
                
                # Update running balance (use calculated for next transaction)
                running_balance = calculated_balance
            elif pdf_balance:
                # No running balance, use PDF balance
                trans["Balance"] = f"{pdf_balance:,.2f}"
                trans["Difference"] = "0.00"
        
        return trans
    
    def parse_bank_transaction(transaction_lines, running_bal):
        """Parse bank transaction using bank statement logic"""
        nonlocal running_balance
        
        # First line has date and amounts
        first_line = transaction_lines[0] if transaction_lines else ""
        date_match = date_pattern.match(first_line[:10]) or re.match(r"(\d{1,2}-[A-Za-z]{3}-\d{2})", first_line)
        if date_match:
            try:
                date = date_match.group(1)
            except (IndexError, AttributeError):
                # Fallback to group(0) if group(1) doesn't exist
                date = date_match.group(0) if date_match else first_line[:10]
        else:
            date = first_line[:10] if len(first_line) >= 10 else ""
        
        # Extract amounts from first line
        money = re.findall(amount_pattern, first_line)
        desc = re.sub(amount_pattern, "", first_line[10:] if len(first_line) > 10 else first_line).strip()
        
        debit = ""
        credit = ""
        balance = ""
        
        if len(money) == 2:
            amount = money[0]
            balance = money[1]
            amount_val = clean_amount(amount)
            balance_val = clean_amount(balance)
            
            # Determine debit/credit from balance difference
            if running_bal is not None:
                diff = balance_val - running_bal
                
                if abs(abs(diff) - amount_val) < 0.01:
                    if diff > 0:
                        # Balance increased = credit
                        credit = amount
                        debit = ""
                    else:
                        # Balance decreased = debit (ensure positive value)
                        debit = amount.replace("-", "") if amount.startswith("-") else amount
                        credit = ""
                else:
                    # Use amount sign to determine debit/credit
                    if amount_val >= 0:
                        credit = amount
                        debit = ""
                    else:
                        # Negative amount = debit, store as positive value
                        debit = amount.replace("-", "")
                        credit = ""
            else:
                credit = amount
                debit = ""
        
        # Join all lines for description
        full_text = " | ".join(transaction_lines)
        
        trans = {
            "SDate": date,
            "Pershkrimi": full_text,
            "TYPE": "",
            "ByOrderOf": "",
            "Beneficiary": "",
            "Debit": debit,
            "Kredi": credit,
            "Balance": "",
            "Closing Balance": balance,
            "Difference": ""
        }
        
        # Extract ByOrderOf and Beneficiary from ALL lines in transaction block using unified extraction
        # Check all lines to ensure we catch all variations
        for line in transaction_lines:
            line_clean = line.strip()
            
            # Extract ByOrderOf if not already found (check all lines)
            if not trans["ByOrderOf"]:
                by_order = extract_by_order_of(line)
                if by_order:
                    trans["ByOrderOf"] = by_order
            
            # Extract Beneficiary if not already found (check all lines)
            if not trans["Beneficiary"]:
                beneficiary = extract_beneficiary(line)
                if beneficiary:
                    trans["Beneficiary"] = beneficiary
        
        # Calculate balance using formula: Opening Balance - Debit + Credit
        if running_bal is not None:
            debit_val = clean_amount(debit) if debit else 0.0
            credit_val = clean_amount(credit) if credit else 0.0
            calculated_balance = running_bal - debit_val + credit_val
            trans["Balance"] = f"{calculated_balance:,.2f}"
            
            # Calculate difference: should be 0 if calculations are correct
            if balance:
                closing_val = clean_amount(balance)
                diff = calculated_balance - closing_val
                # Round to 2 decimal places to handle floating point precision
                diff_rounded = round(diff, 2)
                trans["Difference"] = f"{diff_rounded:,.2f}"
            else:
                trans["Difference"] = ""
            
            # Update running balance (use calculated for next transaction)
            running_balance = calculated_balance
        elif balance:
            # No running balance, use PDF balance
            closing_val = clean_amount(balance)
            trans["Balance"] = f"{closing_val:,.2f}"
            trans["Difference"] = "0.00"
        
        return trans
    
    def flush_transaction():
        """Process and save collected transaction"""
        nonlocal current_transaction_lines, running_balance
        if current_transaction_lines:
            if is_pos_transaction(current_transaction_lines):
                trans = parse_pos_transaction(current_transaction_lines, running_balance)
            else:
                trans = parse_bank_transaction(current_transaction_lines, running_balance)
            rows.append(trans)
        current_transaction_lines = []
    
    # Process lines
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        
        low = line.lower()
        
        # OPENING BALANCE
        if low.startswith("opening balance") or "opening balance" in low:
            money = re.findall(amount_pattern, line)
            if money:
                opening_balance = clean_amount(money[-1])
                running_balance = opening_balance
            continue
        
        # New transaction detected (starts with date)
        date_match = date_pattern.match(line[:10]) or re.match(r"(\d{1,2}-[A-Za-z]{3}-\d{2})", line)
        if date_match:
            # Process previous transaction
            flush_transaction()
            
            # Start new transaction
            current_transaction_lines = [line]
            continue
        
        # Continuation line - add to current transaction
        if current_transaction_lines:
            current_transaction_lines.append(line)
    
    # Process last transaction
    flush_transaction()
    
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
            "Balance": f"{opening_balance:,.2f}",
            "Closing Balance": f"{opening_balance:,.2f}",
            "Difference": "0.00"
        })
    
    # Save CSV with proper column order
    df = pd.DataFrame(rows)
    columns_order = ["SDate", "Pershkrimi", "TYPE", "ByOrderOf", "Beneficiary", "Debit", "Kredi", "Balance", "Closing Balance", "Difference"]
    df = df[[col for col in columns_order if col in df.columns]]
    
    # Generate filename: original_filename + date_processed
    if original_filename:
        base_name = os.path.splitext(os.path.basename(original_filename))[0]
        base_name_clean = clean_filename_value(base_name)
        date_processed = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{base_name_clean}_{date_processed}.csv"
    else:
        filename = "bkt_mixed_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
    
    out_file = os.path.join(RESULT_FOLDER, filename)
    df.to_csv(out_file, index=False, quoting=csv.QUOTE_ALL)
    return out_file


# --------------------------------------------------------------------
#  AUTO DETECT
# --------------------------------------------------------------------

def convert_pdf_to_csv(pdf_path, mode="auto", original_filename=None):
    """Convert PDF to CSV - uses unified mixed parser for all modes to ensure consistent processing"""
    # Use unified mixed parser for all modes to ensure POS and other transactions are processed the same way
    return convert_mixed_pdf_to_csv(pdf_path, original_filename)


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
