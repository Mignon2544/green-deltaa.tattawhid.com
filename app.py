import io
import os
from pathlib import Path
from datetime import datetime

import fitz  # PyMuPDF
import qrcode
from flask import Flask, render_template, request, send_file, session, redirect, url_for, send_from_directory

app = Flask(__name__)

# --- SECURITY & CONFIG ---
app.secret_key = "super_secret_admin_key_12345" # Change this in production
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "root001"

TEMPLATE_PDF = Path("certificate_template.pdf")

# Create the base directory for PDFs
PDF_SAVE_DIR = Path("generated_pdfs")
PDF_SAVE_DIR.mkdir(exist_ok=True)


def make_qr_image(data: str) -> bytes:
    """Generates a QR code image in memory."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def validate_form(form: dict) -> list[str]:
    """Checks that all required fields are filled out."""
    errors = []
    required = [
        "no", "insured_name", "gender", "dob", "nationality",
        "policy_no", "area_of_travel", "departure_date",
        "validity", "issue_date", "plan_type", "return_date",
        "passport_no",
    ]
    for key in required:
        if not form.get(key, "").strip():
            errors.append(f"{key} is required")
    return errors


def generate_pdf(form: dict, qr_link: str) -> io.BytesIO:
    """Generates the filled PDF and returns it as a BytesIO object."""
    policy_no = form["policy_no"]

    # Map EXACT TEXT in the template PDF -> NEW text from the HTML form
    replacements = {
        "T2P-2026-BDB2B-0000961": policy_no,
        "T2P-2026-BDB2B-0000962": policy_no, 
        
        "2026-06-21": form["issue_date"],
        "2026-08-01": form["departure_date"],
        "2027-07-31": form["return_date"],
        "Annual Plan (Return)": form["validity"],
        "EM0358082": form["passport_no"],
        "Outbound, Silver (Covid Plus)": form["plan_type"],
        
        # Handling the two lines for Area of Travel
        "Worldwide": form["area_of_travel"].split(" (")[0],
        "(Excluding USA/CAN)": "(" + form["area_of_travel"].split(" (")[1] if " (" in form["area_of_travel"] else "",
        
        # Insured Table
        "SAIRAJ HASAN FIROZ": form["insured_name"],
        "MALE": form["gender"],
        "1996-10-04": form["dob"],
        "Bangladesh": form["nationality"],
    }

    doc = fitz.open(str(TEMPLATE_PDF))
    page = doc[0]  # Target page 1

    # STEP 1: Find the locations of all old text and store them
    text_positions = []
    for old_text, new_value in replacements.items():
        hits = page.search_for(old_text)
        for rect in hits:
            if rect.width > 0 and rect.height > 0 and not rect.is_infinite:
                text_positions.append((rect, new_value))

    # STEP 2: Delete the old text (WITHOUT painting white over the background)
    for rect, _ in text_positions:
        page.add_redact_annot(rect) 
    
    page.apply_redactions() 

    # STEP 3: Insert the new text exactly where the old text was
    for rect, new_value in text_positions:
        if not new_value:
            continue
            
        fontsize = rect.height * 0.75
        if fontsize < 5: 
            fontsize = 5
        
        baseline_y = rect.y1 - (rect.height * 0.2)
        
        page.insert_text(
            (rect.x0, baseline_y),
            str(new_value),
            fontsize=fontsize,
            fontname="helv",
            color=(0, 0, 0),
        )

    # STEP 4: Insert the Top QR Code using your tuned coordinates
    qr_bytes = make_qr_image(qr_link)
    page.insert_image(fitz.Rect(276.00, 102.00, 338.00, 164.00), stream=qr_bytes)

    # Save to memory
    output = io.BytesIO()
    doc.save(output, garbage=4, deflate=True)
    doc.close()
    output.seek(0)
    return output


# --- ROUTES ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Invalid username or password")
            
    if session.get('logged_in'):
        return redirect(url_for('index'))
        
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))


@app.route("/", methods=["GET"])
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    if not session.get('logged_in'):
        return "Unauthorized. Please log in.", 403

    form = request.form.to_dict()
    errors = validate_form(form)
    
    if errors:
        return "Validation failed:<br>" + "<br>".join(errors), 400

    try:
        # Get the base URL (e.g., https://green-delta.onrender.com)
        base_url = request.host_url.rstrip('/')
        policy_no = form['policy_no']
        
        # 1. Get current Year and Month
        now = datetime.now()
        year_str = now.strftime("%Y")
        month_str = now.strftime("%m")
        
        # 2. Build the nested directory path: wp-content/uploads/YYYY/MM/
        sub_dir = PDF_SAVE_DIR / "wp-content" / "uploads" / year_str / month_str
        sub_dir.mkdir(parents=True, exist_ok=True) 
        
        # 3. Create the dynamic QR link pointing to the live server file
        qr_link = f"{base_url}/wp-content/uploads/{year_str}/{month_str}/{policy_no}.pdf"
        
        # 4. Generate the PDF
        pdf_bytes = generate_pdf(form, qr_link)
        
        # 5. Save the file to the server
        filepath = sub_dir / f"{policy_no}.pdf"
        with open(filepath, "wb") as f:
            f.write(pdf_bytes.getvalue())

        # 6. Show success page
        download_link = f"/wp-content/uploads/{year_str}/{month_str}/{policy_no}.pdf"
        return f"""
        <div style="font-family: Arial; text-align: center; margin-top: 50px;">
            <h2 style="color: green;">Certificate Generated Successfully!</h2>
            <a href="{download_link}" style="padding: 10px 20px; background: #0b7a4b; color: white; text-decoration: none; border-radius: 5px; font-weight: bold;">View / Download PDF</a>
            <br><br>
        </div>
        """
    except Exception as e:
        return f"An error occurred: {e}", 500


@app.route("/wp-content/uploads/<year>/<month>/<filename>")
def serve_pdf(year, month, filename):
    """Serves the PDFs from the simulated WordPress folder structure."""
    file_path = PDF_SAVE_DIR / "wp-content" / "uploads" / year / month
    return send_from_directory(file_path, filename)


if __name__ == "__main__":
    app.run(debug=True)
