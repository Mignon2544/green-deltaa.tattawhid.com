import io
import os
import shutil
from pathlib import Path
from datetime import datetime

import fitz  # PyMuPDF
import qrcode
from flask import Flask, render_template, request, send_file, session, redirect, url_for, send_from_directory

app = Flask(__name__)

# --- SECURITY & CONFIG ---
app.secret_key = "super_secret_admin_key_12345"
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "password123"

TEMPLATE_PDF = Path("certificate_template.pdf")
PDF_SAVE_DIR = Path("generated_pdfs")
PDF_SAVE_DIR.mkdir(exist_ok=True)

# ... (keep make_qr_image and validate_form exactly the same) ...

def generate_pdf(form: dict, qr_link: str) -> io.BytesIO:
    # ... (keep generate_pdf exactly the same as previous step) ...
    # [Make sure the smart alignment and QR insertion are here]
    pass # Replace 'pass' with the actual function body

# --- ROUTES ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    # ... (keep exactly the same) ...
    pass

@app.route('/logout')
def logout():
    # ... (keep exactly the same) ...
    pass

@app.route("/", methods=["GET"])
def index():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template("index.html")

@app.route("/generate", methods=["POST"])
def generate():
    # ... (keep exactly the same, it returns the success page) ...
    pass

# 👇 NEW ROUTES FOR GENERATED FILES 👇

@app.route("/generated")
def generated_files():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    
    files_list = []
    # Walk through the wp-content/uploads directory to find all PDFs
    for root, dirs, files in os.walk(PDF_SAVE_DIR):
        for file in files:
            if file.endswith(".pdf"):
                full_path = Path(root) / file
                # Get relative path from PDF_SAVE_DIR (e.g., wp-content/uploads/2026/11/file.pdf)
                rel_path = full_path.relative_to(PDF_SAVE_DIR).as_posix()
                
                # Get file size and modified time
                size_kb = round(full_path.stat().st_size / 1024, 1)
                mod_time = datetime.fromtimestamp(full_path.stat().st_mtime).strftime("%b %d, %Y %H:%M")
                
                files_list.append({
                    "name": file,
                    "rel_path": rel_path,
                    "size": size_kb,
                    "date": mod_time
                })
    
    # Sort by newest first
    files_list.sort(key=lambda x: x['date'], reverse=True)
    
    return render_template("generated.html", files=files_list)

@app.route("/delete-file", methods=["POST"])
def delete_file():
    if not session.get('logged_in'):
        return "Unauthorized", 403
        
    # Handle bulk delete
    files_to_delete = request.form.getlist("selected_files")
    if files_to_delete:
        for rel_path in files_to_delete:
            # Security check to prevent directory traversal
            safe_path = (PDF_SAVE_DIR / rel_path).resolve()
            if str(safe_path).startswith(str(PDF_SAVE_DIR.resolve())) and safe_path.exists():
                safe_path.unlink() # Deletes the file
                
    return redirect(url_for('generated_files'))

@app.route("/wp-content/uploads/<year>/<month>/<filename>")
def serve_pdf(year, month, filename):
    file_path = PDF_SAVE_DIR / "wp-content" / "uploads" / year / month
    return send_from_directory(file_path, filename)

if __name__ == "__main__":
    app.run(debug=True)
