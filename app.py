import io
from pathlib import Path

import fitz  # PyMuPDF
import qrcode
from flask import Flask, render_template, request, send_file, session, redirect, url_for

app = Flask(__name__)

# Required for Flask sessions to work. Change this to a long random string!
app.secret_key = "super_secret_admin_key_12345"

# --- ADMIN CREDENTIALS ---
# Change these to whatever you want your login to be
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "1234"

TEMPLATE_PDF = Path("certificate_template.pdf")


def make_qr_image(data: str) -> bytes:
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


def generate_pdf(form: dict) -> io.BytesIO:
    policy_no = form["policy_no"]
    qr_link = f"https://yourwebsite.com/policies/{policy_no}.pdf"

    replacements = {
        "T2P-2026-BDB2B-0000961": policy_no,
        "T2P-2026-BDB2B-0000962": policy_no, 
        "2026-06-21": form["issue_date"],
        "2026-08-01": form["departure_date"],
        "2027-07-31": form["return_date"],
        "Annual Plan (Return)": form["validity"],
        "EM0358082": form["passport_no"],
        "Outbound, Silver (Covid Plus)": form["plan_type"],
        "Worldwide": form["area_of_travel"].split(" (")[0],
        "(Excluding USA/CAN)": "(" + form["area_of_travel"].split(" (")[1] if " (" in form["area_of_travel"] else "",
        "SAIRAJ HASAN FIROZ": form["insured_name"],
        "MALE": form["gender"],
        "1996-10-04": form["dob"],
        "Bangladesh": form["nationality"],
    }

    doc = fitz.open(str(TEMPLATE_PDF))
    page = doc[0]

    text_positions = []
    for old_text, new_value in replacements.items():
        hits = page.search_for(old_text)
        for rect in hits:
            if rect.width > 0 and rect.height > 0 and not rect.is_infinite:
                text_positions.append((rect, new_value))

    for rect, _ in text_positions:
        page.add_redact_annot(rect) 
    
    page.apply_redactions() 

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

    qr_bytes = make_qr_image(qr_link)
    page.insert_image(fitz.Rect(276.00, 102.00, 338.00, 164.00), stream=qr_bytes)

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
            
    # If already logged in, skip to index
    if session.get('logged_in'):
        return redirect(url_for('index'))
        
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))


@app.route("/", methods=["GET"])
def index():
    # Protect the main page
    if not session.get('logged_in'):
        return redirect(url_for('login'))
        
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    # Protect the PDF generation
    if not session.get('logged_in'):
        return "Unauthorized. Please log in.", 403

    form = request.form.to_dict()
    errors = validate_form(form)
    
    if errors:
        return "Validation failed:<br>" + "<br>".join(errors), 400

    try:
        pdf = generate_pdf(form)
        filename = f"{form['policy_no']}.pdf"
        
        return send_file(
            pdf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return f"An error occurred: {e}", 500


if __name__ == "__main__":
    app.run(debug=True)
