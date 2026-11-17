import io
import os
import random
import string
import resend
import boto3
import psycopg2
from psycopg2.extras import DictCursor
from pathlib import Path
from datetime import datetime

import fitz  # PyMuPDF
import qrcode
from flask import Flask, render_template, request, send_file, session, redirect, url_for, flash

app = Flask(__name__)

# --- SECURITY & CONFIG ---
app.secret_key = os.environ.get("SECRET_KEY", "super_secret_admin_key_12345")
SUPER_ADMIN_EMAIL = "isaacnjuguna518@gmail.com"
DATABASE_URL = os.environ.get("DATABASE_URL")

# RESEND API SETUP
resend.api_key = os.environ.get("RESEND_API_KEY")

# CLOUDFLARE R2 SETUP
R2_ACCESS_KEY = os.environ.get("R2_ACCESS_KEY")
R2_SECRET_KEY = os.environ.get("R2_SECRET_KEY")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "pdf-storage")
R2_ENDPOINT = os.environ.get("R2_ENDPOINT")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL")

s3_client = boto3.client(
    's3',
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
    region_name='auto'
)

TEMPLATE_PDF = Path("certificate_template.pdf")

# --- DATABASE INITIALIZATION (PostgreSQL) ---
def get_db_connection():
    if "?" in DATABASE_URL:
        url = DATABASE_URL + "&sslmode=require"
    else:
        url = DATABASE_URL + "?sslmode=require"
    return psycopg2.connect(url)

def init_db():
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (id SERIAL PRIMARY KEY, 
                      username VARCHAR(255) UNIQUE NOT NULL, 
                      password VARCHAR(255) NOT NULL,
                      last_ip VARCHAR(255),
                      last_login VARCHAR(255),
                      device TEXT)''')
        c.execute("ALTER TABLE admins ADD COLUMN IF NOT EXISTS device TEXT")
        c.execute("SELECT COUNT(*) FROM admins")
        if c.fetchone()[0] == 0:
            c.execute("INSERT INTO admins (username, password) VALUES (%s, %s)", ("admin", "password123"))
        conn.commit()
        conn.close()
        print("✅ Database connected and initialized successfully.")
    except Exception as e:
        print(f"❌ DATABASE INITIALIZATION ERROR: {e}")

init_db()

def generate_key():
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
        if not form.get(key, "").strip(): 
            errors.append(f"{key} is required")
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
    for rect, _, _ in text_positions: 
        page.add_redact_annot(rect) 
    page.apply_redactions() 
    for rect, new_value, old_text in text_positions:
        if not new_value: 
            continue
        fontsize = rect.height * 0.75
        if fontsize < 5: 
            fontsize = 5
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
        
        try:
            conn = get_db_connection()
            c = conn.cursor(cursor_factory=DictCursor)
            c.execute("SELECT * FROM admins WHERE username=%s AND password=%s", (username, password))
            user = c.fetchone()
            
            if user:
                client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
                if client_ip and ',' in client_ip:
                    client_ip = client_ip.split(',')[0].strip()
                user_agent = request.headers.get('User-Agent', 'Unknown Device')
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                c.execute("UPDATE admins SET last_ip=%s, last_login=%s, device=%s WHERE username=%s", 
                          (client_ip, now, user_agent, username))
                conn.commit()
                conn.close()
                
                session['logged_in'] = True
                session['admin_username'] = username 
                return redirect(url_for('index'))
            else:
                conn.close()
                return render_template('login.html', error="Invalid username or password")
        except Exception as e:
            return f"<h1>Database Connection Error</h1><p>{e}</p>", 500
            
    if session.get('logged_in'): return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    session.pop('super_admin', None)
    session.pop('admin_username', None)
    return redirect(url_for('login'))

@app.route("/")
def index():
    if not session.get('logged_in'): 
        return redirect(url_for('login'))
    return render_template("index.html")

@app.route('/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    if not session.get('logged_in'): 
        return redirect(url_for('login'))
        
    message = None
    error = None
    current_username = session.get('admin_username')
    
    conn = get_db_connection()
    c = conn.cursor()
    
    if request.method == 'POST':
        new_username = request.form.get('username')
        new_password = request.form.get('password')
        
        if new_username and new_password:
            try:
                c.execute("UPDATE admins SET username=%s, password=%s WHERE username=%s", 
                          (new_username, new_password, current_username))
                conn.commit()
                session['admin_username'] = new_username
                current_username = new_username
                message = "Your credentials have been updated successfully!"
            except psycopg2.IntegrityError:
                conn.rollback()
                error = "That username is already taken. Please choose another."
            except Exception as e:
                conn.rollback()
                error = f"Database error: {e}"
                
    try:
        c.execute("SELECT username, password FROM admins WHERE username=%s", (current_username,))
        current_admin = c.fetchone() 
    except Exception:
        current_admin = None
        
    conn.close()
    
    if not current_admin:
        session.clear()
        return redirect(url_for('login'))
    
    return render_template('admin_settings.html', admin=current_admin, message=message, error=error)

@app.route("/generate", methods=["POST"])
def generate():
    if not session.get('logged_in'): return "Unauthorized", 403
    form = request.form.to_dict()
    form['insured_name'] = form.get('insured_name', '').upper()
    form['passport_no'] = form.get('passport_no', '').upper()
    errors = validate_form(form)
    if errors: return "Validation failed:<br>" + "<br>".join(errors), 400
    try:
        policy_no = form['policy_no']
        now = datetime.now()
        year_str, month_str = now.strftime("%Y"), now.strftime("%m")
        object_key = f"wp-content/uploads/{year_str}/{month_str}/{policy_no}.pdf"
        
        # USE THE NEW CUSTOM DOMAIN FOR THE QR CODE
        qr_link = f"https://green-deltainsurance.com/{object_key}"
        
        pdf_bytes = generate_pdf(form, qr_link)
        s3_client.put_object(Bucket=R2_BUCKET_NAME, Key=object_key, Body=pdf_bytes.getvalue(), ContentType='application/pdf')
        
        return f"""<div style="font-family: Arial; text-align: center; margin-top: 50px;">
            <h2 style="color: green;">Certificate Generated </h2>
            <a href="{qr_link}" target="_blank" style="padding: 10px 20px; background: #0b7a4b; color: white; text-decoration: none; border-radius: 5px;">View / Download PDF</a>
            <br><br><a href="/" style="color: #333;">← Back to Admin Panel</a></div>"""
    except Exception as e: return f"An error occurred: {e}", 500

# 👇 NEW ROUTE TO SERVE PDFs VIA YOUR CUSTOM DOMAIN 👇
@app.route("/wp-content/uploads/<year>/<month>/<filename>")
def serve_pdf(year, month, filename):
    object_key = f"wp-content/uploads/{year}/{month}/{filename}"
    try:
        response = s3_client.get_object(Bucket=R2_BUCKET_NAME, Key=object_key)
        return send_file(
            io.BytesIO(response['Body'].read()),
            mimetype='application/pdf',
            as_attachment=False,
            download_name=filename
        )
    except Exception as e:
        return f"PDF not found or error: {e}", 404
# 👆 END OF NEW ROUTE 👆

@app.route("/generated")
def generated_files():
    if not session.get('logged_in'): return redirect(url_for('login'))
    files_list = []
    try:
        response = s3_client.list_objects_v2(Bucket=R2_BUCKET_NAME)
        if 'Contents' in response:
            for obj in response['Contents']:
                if obj['Key'].endswith('.pdf'):
                    files_list.append({"name": obj['Key'].split('/')[-1], "rel_path": obj['Key'], "size": round(obj['Size'] / 1024, 1), "date": obj['LastModified'].strftime("%b %d, %Y %H:%M")})
        files_list.sort(key=lambda x: x['date'], reverse=True)
    except Exception as e: return f"Error loading files from R2: {e}", 500
    return render_template("generated.html", files=files_list)

@app.route("/delete-file", methods=["POST"])
def delete_file():
    if not session.get('logged_in'): return "Unauthorized", 403
    files_to_delete = request.form.getlist("selected_files")
    try:
        for object_key in files_to_delete: s3_client.delete_object(Bucket=R2_BUCKET_NAME, Key=object_key)
    except Exception as e: return f"Error deleting files: {e}", 500
    return redirect(url_for('generated_files'))

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

('/super-admin/forgot', methods=['POST'])
def super_admin_forgot():
    new_key = generate_key()
    session['super_key'] = new_key 
    try:
        resend.Emails.send({
            "from": "onboarding@resend.dev", "to": SUPER_ADMIN_EMAIL, "subject": "Super Admin Recovery Key",
            "html": f"<p>Your new 16-digit Super Admin Key is:</p><h2>{new_key}</h2><p>Use this to log in.</p>"
        })
        return render_template('super_login.html', message=f"A new key has been sent to {SUPER_ADMIN_EMAIL}!")
    except Exception as e: 
        return render_template('super_login.html', error=f"Failed to send email: {e}")

@app.route('/super-admin/dashboard', methods=['GET'])
def super_admin_dashboard():
    if not session.get('super_admin'): 
        return redirect(url_for('super_admin_login'))
    
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM admins")
        admins_raw = c.fetchall()
        conn.close()
        
        admins = []
        now = datetime.now()
        
        for admin in admins_raw:
            admin_list = list(admin) # Convert tuple to list so we can append
            
            # admin[4] is the last_login string (e.g. "2026-11-18 10:30:00")
            last_login_str = admin_list[4]
            status = "inactive"
            
            if last_login_str:
                try:
                    last_login_time = datetime.strptime(last_login_str, "%Y-%m-%d %H:%M:%S")
                    # If the difference is less than 15 minutes, they are active
                    if (now - last_login_time).total_seconds() < 900:
                        status = "active"
                except ValueError:
                    pass # Handle parsing errors safely
            
            admin_list.append(status) # admin[6] will now be the status
            admins.append(admin_list)
            
    except Exception as e:
        return f"Database error: {e}", 500
    
    return render_template('super_dashboard.html', admins=admins)

@app.route('/super-admin/add-admin', methods=['POST'])
def add_admin():
    if not session.get('super_admin'): return redirect(url_for('super_admin_login'))
    username = request.form.get('username')
    password = request.form.get('password')
    
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO admins (username, password) VALUES (%s, %s)", (username, password))
        conn.commit()
        conn.close()
        flash("Admin added successfully!", "success")
    except psycopg2.IntegrityError:
        conn.rollback()
        conn.close()
        flash("Username already exists.", "error")
    return redirect(url_for('super_admin_dashboard'))

@app.route('/super-admin/edit-admin/<int:id>', methods=['POST'])
def edit_admin(id):
    if not session.get('super_admin'): return redirect(url_for('super_admin_login'))
    new_username = request.form.get('username')
    new_password = request.form.get('password')
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE admins SET username=%s, password=%s WHERE id=%s", (new_username, new_password, id))
    conn.commit()
    conn.close()
    flash("Admin updated successfully!", "success")
    return redirect(url_for('super_admin_dashboard'))

@app.route('/super-admin/delete-admin/<int:id>', methods=['POST'])
def delete_admin(id):
    if not session.get('super_admin'): return redirect(url_for('super_admin_login'))
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE id=%s", (id,))
    conn.commit()
    conn.close()
    flash("Admin deleted successfully!", "success")
    return redirect(url_for('super_admin_dashboard'))

@app.route('/super-admin/logout')
def super_admin_logout():
    session.pop('super_admin', None)
    return redirect(url_for('login'))

if __name__ == "__main__":
    app.run(debug=True)
