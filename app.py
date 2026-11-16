import io
import os
import json
import random
import string
import resend
from pathlib import Path
from datetime import datetime

import fitz  # PyMuPDF
import qrcode
from flask import Flask, render_template, request, send_file, session, redirect, url_for, send_from_directory

app = Flask(__name__)

# --- SECURITY & CONFIG ---
app.secret_key = "super_secret_admin_key_12345"
SUPER_ADMIN_EMAIL = "isaacnjuguna518@gmail.com"

# RESEND API SETUP
resend.api_key = "RESEND_API_KEY" # <--- CHANGE THIS TO A NEW KEY

TEMPLATE_PDF = Path("certificate_template.pdf")
PDF_SAVE_DIR = Path("generated_pdfs")
PDF_SAVE_DIR.mkdir(exist_ok=True)
USERS_FILE = Path("users.json")

# Ensure users.json exists
if not USERS_FILE.exists():
    with open(USERS_FILE, "w") as f:
        json.dump({"admin_username": "admin", "admin_password": "password123"}, f)

def get_admin_creds():
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_admin_creds(username, password):
    with open(USERS_FILE, "w") as f:
        json.dump({"admin_username": username, "admin_password": password}, f)

def generate_key():
    """Generates a 16-digit key in the format XXXX-XXXX-XXXX-XXXX"""
    parts = []
    for _ in range(4):
        part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        parts.append(part)
    return "-".join(parts)

def make_qr_image(data: str) -> bytes:
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

def validate_form(form: dict) -> list[str]:
    errors = []
    required = ["no", "insured_name", "gender", "dob", "nationality", "policy_no", "area_of_travel", "departure_date", "validity", "issue_date", "plan_type", "return_date", "passport_no"]
    for key in required:
        if not form.get(key, "").strip(): errors.append(f"{key} is required")
    return errors

def generate_pdf(form: dict, qr_link: str) -> io.BytesIO:
    policy_no = form["policy_no"]
    replacements = {
        "T2P-2026-BDB2B-0000961": policy_no, "T2P-2026-BDB2B-0000962": policy_no, 
        "2026-06-21": form["issue_date"], "2026-08-01": form["departure_date"],
        "2027-07-31": form["return_date"], "Annual Plan (Return)": form["validity"],
        "EM0358082": form["passport_no"], "Outbound, Silver (Covid Plus)": form["plan_type"],
        "Worldwide": form["area_of_travel"].split(" (")[0],
        "(Excluding USA/CAN)": "(" + form["area_of_travel"].split(" (")[1] if " (" in form["area_of_travel"] else "",
        "SAIRAJ HASAN FIROZ": form["insured_name"], "MALE": form["gender"],
        "1996-10-04": form["dob"], "Bangladesh": form["nationality"],
    }
    doc = fitz.open(str(TEMPLATE_PDF))
    page = doc[0]
    text_positions = []
    for old_text, new_value in replacements.items():
        hits = page.search_for(old_text)
        for rect in hits:
            if rect.width > 0 and rect.height > 0 and not rect.is_infinite:
                text_positions.append((rect, new_value, old_text))
    for rect, _, _ in text_positions: page.add_redact_annot(rect) 
    page.apply_redactions() 
    for rect, new_value, old_text in text_positions:
        if not new_value: continue
        fontsize = rect.height * 0.75
        if fontsize < 5: fontsize = 5
        date_fields = ["2026-06-21", "2026-08-01", "2027-07-31", "Annual Plan (Return)", "EM0358082"]
        if old_text in date_fields:
            fontsize = fontsize - 0.5 
            x_pos = rect.x0 + (rect.width / 2) - (fitz.get_text_length(str(new_value), fontname="helv", fontsize=fontsize) / 2)
            baseline_y = rect.y1 - (rect.height * 0.25)
        else:
            x_pos = rect.x0
            baseline_y = rect.y1 - (rect.height * 0.2)
        page.insert_text((x_pos, baseline_y), str(new_value), fontsize=fontsize, fontname="helv", color=(0, 0, 0))
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
        creds = get_admin_creds()
        if username == creds["admin_username"] and password == creds["admin_password"]:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Invalid username or password")
    if session.get('logged_in'): return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('super_admin', None)
    return redirect(url_for('login'))

@app.route("/")
def index():
    if not session.get('logged_in'): return redirect(url_for('login'))
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate():
    if not session.get('logged_in'): return "Unauthorized", 403
    form = request.form.to_dict()
    errors = validate_form(form)
    if errors: return "Validation failed:<br>" + "<br>".join(errors), 400
    try:
        base_url = request.host_url.rstrip('/')
        policy_no = form['policy_no']
        now = datetime.now()
        year_str, month_str = now.strftime("%Y"), now.strftime("%m")
        sub_dir = PDF_SAVE_DIR / "wp-content" / "uploads" / year_str / month_str
        sub_dir.mkdir(parents=True, exist_ok=True) 
        qr_link = f"{base_url}/wp-content/uploads/{year_str}/{month_str}/{policy_no}.pdf"
        pdf_bytes = generate_pdf(form, qr_link)
        filepath = sub_dir / f"{policy_no}.pdf"
        with open(filepath, "wb") as f: f.write(pdf_bytes.getvalue())
        download_link = f"/wp-content/uploads/{year_str}/{month_str}/{policy_no}.pdf"
        return f"""<div style="font-family: Arial; text-align: center; margin-top: 50px;">
            <h2 style="color: green;">✅ PDF Generated Successfully!</h2>
            <a href="{download_link}" style="padding: 10px 20px; background: #0b7a4b; color: white; text-decoration: none; border-radius: 5px;">View / Download PDF</a>
            <br><br><a href="/" style="color: #333;">← Back to Admin Panel</a></div>"""
    except Exception as e: return f"An error occurred: {e}", 500

@app.route("/generated")
def generated_files():
    if not session.get('logged_in'): return redirect(url_for('login'))
    files_list = []
    for root, dirs, files in os.walk(PDF_SAVE_DIR):
        for file in files:
            if file.endswith(".pdf"):
                full_path = Path(root) / file
                files_list.append({
                    "name": file, "rel_path": full_path.relative_to(PDF_SAVE_DIR).as_posix(),
                    "size": round(full_path.stat().st_size / 1024, 1),
                    "date": datetime.fromtimestamp(full_path.stat().st_mtime).strftime("%b %d, %Y %H:%M")
                })
    files_list.sort(key=lambda x: x['date'], reverse=True)
    return render_template("generated.html", files=files_list)

@app.route("/delete-file", methods=["POST"])
def delete_file():
    if not session.get('logged_in'): return "Unauthorized", 403
    files_to_delete = request.form.getlist("selected_files")
    for rel_path in files_to_delete:
        safe_path = (PDF_SAVE_DIR / rel_path).resolve()
        if str(safe_path).startswith(str(PDF_SAVE_DIR.resolve())) and safe_path.exists():
            safe_path.unlink() 
    return redirect(url_for('generated_files'))

@app.route("/wp-content/uploads/<year>/<month>/<filename>")
def serve_pdf(year, month, filename):
    return send_from_directory(PDF_SAVE_DIR / "wp-content" / "uploads" / year / month, filename)

# --- SUPER ADMIN ROUTES ---

@app.route('/super-admin', methods=['GET', 'POST'])
def super_admin_login():
    if session.get('super_admin'): return redirect(url_for('super_admin_dashboard'))
    
    if request.method == 'POST':
        key = request.form.get('key')
        valid_key = session.get('super_key')
        
        if valid_key and key == valid_key:
            session['super_admin'] = True
            return redirect(url_for('super_admin_dashboard'))
        else:
            return render_template('super_login.html', error="Invalid or expired 16-digit key.")
            
    return render_template('super_login.html')

@app.route('/super-admin/forgot', methods=['POST'])
def super_admin_forgot():
    new_key = generate_key()
    session['super_key'] = new_key 
    
    try:
        resend.Emails.send({
            "from": "onboarding@resend.dev",
            "to": SUPER_ADMIN_EMAIL,
            "subject": "Super Admin Recovery Key",
            "html": f"<p>Your new 16-digit Super Admin Key is:</p><h2>{new_key}</h2><p>Use this to log in.</p>"
        })
        return render_template('super_login.html', message=f"A new key has been sent to {SUPER_ADMIN_EMAIL}!")
    except Exception as e:
        return render_template('super_login.html', error=f"Failed to send email: {e}")

@app.route('/super-admin/dashboard', methods=['GET', 'POST'])
def super_admin_dashboard():
    if not session.get('super_admin'): return redirect(url_for('super_admin_login'))
    
    if request.method == 'POST':
        new_username = request.form.get('username')
        new_password = request.form.get('password')
        if new_username and new_password:
            save_admin_creds(new_username, new_password)
            return render_template('super_dashboard.html', creds=get_admin_creds(), message="Admin credentials updated successfully!")
    
    return render_template('super_dashboard.html', creds=get_admin_creds())

@app.route('/super-admin/logout')
def super_admin_logout():
    session.pop('super_admin', None)
    return redirect(url_for('login'))

if __name__ == "__main__":
    app.run(debug=True)
