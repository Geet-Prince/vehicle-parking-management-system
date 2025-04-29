
import os
import uuid
from datetime import datetime, timedelta
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, send_from_directory)
import mysql.connector
from werkzeug.security import generate_password_hash, check_password_hash
from fpdf import FPDF # For PDF generation




app = Flask(__name__)

app.secret_key = 'a_very_strong_random_secret_key_change_me' 

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'root'),            
    'password': os.getenv('DB_PASSWORD', 'prince'),       
    'database': os.getenv('DB_NAME', 'parking_system'), 
    'autocommit': True 
}

RECEIPT_FOLDER = os.path.join('static', 'receipts')
os.makedirs(RECEIPT_FOLDER, exist_ok=True) 

def get_db():
    """Establishes database connection."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as err:
        print(f"Database Connection Error: {err}")
        flash("Database connection error. Please try again later.", "danger")
        return None

def execute_query(query, params=None, fetchone=False, fetchall=False):
    """Executes SQL queries with dictionary cursor."""
    conn = get_db()
    if not conn: return None
    cursor = conn.cursor(dictionary=True, buffered=False)
    result = None
    try:
        cursor.execute(query, params or ())
        if fetchone: result = cursor.fetchone()
        elif fetchall: result = cursor.fetchall()
    except mysql.connector.Error as err:
        print(f"Query Error executing: {query} with params {params}\nError: {err}")
        flash(f"A database query error occurred. Please contact support.", "danger")
    finally:
        if cursor:
             try: cursor.close()
             except Exception as e: print(f"Error closing cursor: {e}")
        if conn and conn.is_connected():
             try: conn.close()
             except Exception as e: print(f"Error closing connection: {e}")
    return result

# --- Helper Functions ---
def get_standard_capacity(floor_number):
    """Returns capacity and vehicle type for a standard floor."""
    if floor_number == 0: return {'type': '2W', 'capacity': 100}
    elif floor_number in [1, 2]: return {'type': '4W', 'capacity': 100}
    else: return None

def calculate_cost(start_time, end_time, vehicle_type):
    """Basic cost calculation (e.g., ₹20/hr for 2W, ₹40/hr for 4W, min 1 hr)."""
    if isinstance(start_time, str):
        try: start_time = datetime.fromisoformat(start_time)
        except ValueError: return 0.0
    if isinstance(end_time, str):
         try: end_time = datetime.fromisoformat(end_time)
         except ValueError: return 0.0
    if not isinstance(start_time, datetime) or not isinstance(end_time, datetime): return 0.0
    duration_seconds = (end_time - start_time).total_seconds()
    if duration_seconds <= 0: duration_hours = 1
    else: duration_hours = max(1, (duration_seconds + 3599) // 3600) 
    rate = 20 if vehicle_type == '2W' else 40 
    return round(duration_hours * rate, 2)

def find_available_spot(lot_id, floor_number, start_time, end_time):
    """Finds an available spot number on a given floor for the time slot."""
    capacity_info = get_standard_capacity(floor_number)
    if not capacity_info: return None
    if isinstance(start_time, str): start_time = datetime.fromisoformat(start_time)
    if isinstance(end_time, str): end_time = datetime.fromisoformat(end_time)
    query = "SELECT spot_number FROM bookings WHERE lot_id = %s AND floor_number = %s AND is_active = TRUE AND (start_time < %s AND end_time > %s)"
    start_time_sql = start_time.strftime('%Y-%m-%d %H:%M:%S'); end_time_sql = end_time.strftime('%Y-%m-%d %H:%M:%S')
    booked_spots = execute_query(query, (lot_id, floor_number, end_time_sql, start_time_sql), fetchall=True)
    booked_spot_numbers = {spot['spot_number'] for spot in booked_spots} if booked_spots else set()
    print(f"--- Checking Lot {lot_id}, Floor {floor_number}. Booked spots overlapping {start_time_sql} - {end_time_sql}: {booked_spot_numbers}")
    for spot_num in range(1, capacity_info['capacity'] + 1):
        if spot_num not in booked_spot_numbers: print(f"--- Found available spot: {spot_num} on Floor {floor_number}"); return spot_num
    print(f"--- No available spot found for Lot {lot_id}, Floor {floor_number} between {start_time_sql} and {end_time_sql}"); return None
def generate_pdf_receipt(booking_details):
    """Generates a PDF receipt and saves it, ensuring font support for ₹."""
    filepath = None 
    try:
        pdf = FPDF()
        pdf.add_page()
        font_dir = os.path.join(os.path.dirname(__file__), 'fonts')
        font_regular_path = os.path.join(font_dir, 'DejaVuSans.ttf')
        font_bold_path = os.path.join(font_dir, 'DejaVuSans-Bold.ttf') 
        font_regular_loaded = False; font_bold_loaded = False

        if not os.path.exists(font_regular_path): print(f"---!!!--- Font file not found: {font_regular_path} ---!!!---")
        else:
            try: pdf.add_font('DejaVu', '', font_regular_path, uni=True); font_regular_loaded = True; print(f"--- Added font (Regular): {font_regular_path} ---")
            except RuntimeError as e: print(f"---!!!--- FPDF Error adding font (Regular): {e} ---!!!---")

        if not os.path.exists(font_bold_path): print(f"---!!!--- Font file not found: {font_bold_path}. Bold text might not render correctly. ---!!!---")
        else:
             try: pdf.add_font('DejaVu', 'B', font_bold_path, uni=True); font_bold_loaded = True; print(f"--- Added font (Bold): {font_bold_path} ---")
             except RuntimeError as e: print(f"---!!!--- FPDF Error adding font (Bold): {e} ---!!!---")

        use_dejavu = font_regular_loaded; use_dejavu_bold = font_regular_loaded and font_bold_loaded

        # --- PDF Title ---
        if use_dejavu_bold: pdf.set_font('DejaVu', 'B', 16)
        elif use_dejavu: pdf.set_font('DejaVu', '', 16)
        else: pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, "Parking Receipt", 0, 1, 'C'); pdf.ln(10)

        # --- PDF Details ---
        if use_dejavu: pdf.set_font('DejaVu', '', 10)
        else: pdf.set_font("Arial", size=10)

        details_map = {
            "Receipt ID": booking_details.get('receipt_id'), "Parking Lot": booking_details.get('lot_name'), "Location": booking_details.get('location'),
            "Floor Number": booking_details.get('floor_number'), "Spot Number": booking_details.get('spot_number'), "Vehicle Type": booking_details.get('vehicle_type'),
            "Vehicle Registration": booking_details.get('vehicle_reg_number'),
            "Scheduled Start": booking_details['start_time'].strftime('%Y-%m-%d %H:%M') if isinstance(booking_details.get('start_time'), datetime) else str(booking_details.get('start_time', 'N/A')),
            "Scheduled End": booking_details['end_time'].strftime('%Y-%m-%d %H:%M') if isinstance(booking_details.get('end_time'), datetime) else str(booking_details.get('end_time', 'N/A')),
            "Amount Paid": f"₹{booking_details.get('amount', 0.00):.2f}", 
            "Booked On": booking_details['created_at'].strftime('%Y-%m-%d %H:%M:%S') if isinstance(booking_details.get('created_at'), datetime) else str(booking_details.get('created_at', 'N/A')),
        }

        for key, value in details_map.items():
             if use_dejavu_bold: pdf.set_font('DejaVu', 'B', 10)
             elif use_dejavu: pdf.set_font('DejaVu', '', 10)
             else: pdf.set_font("Arial", 'B', 10)
             pdf.cell(50, 8, f"{key}:", 0, 0)
             if use_dejavu: pdf.set_font('DejaVu', '', 10)
             else: pdf.set_font("Arial", '', 10)
             pdf.cell(0, 8, str(value) if value is not None else 'N/A', 0, 1)

       
        filename = f"receipt_{booking_details.get('receipt_id', 'error')}.pdf"
        filepath = os.path.join(RECEIPT_FOLDER, filename)
        pdf.output(filepath, "F"); print(f"Receipt generated successfully: {filepath}"); return filename

    except Exception as e:
        err_filepath = filepath if filepath else "Unknown path"; print(f"---!!!--- Error generating PDF '{err_filepath}': {e} ---!!!---")
        import traceback; traceback.print_exc(); return None

@app.context_processor
def inject_now(): return {'now': datetime.utcnow()}
def login_required(role='user'):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user_role = session.get('role')
            if role == 'user': user_id = session.get('user_id'); login_url_name = 'user_login'
            elif role == 'admin': user_id = session.get('admin_id'); login_url_name = 'admin_login'
            else: user_id = None; login_url_name = 'index'
            if not user_id or user_role != role: flash(f"You must be logged in as a {role} to view this page.", "warning"); return redirect(url_for(login_url_name))
            return f(*args, **kwargs)
        return decorated_function
    return decorator
@app.route('/')
def index(): return render_template('index.html')
@app.route('/user/register', methods=['GET', 'POST'])
def user_register():
    if request.method == 'POST':
        username = request.form.get('username'); email = request.form.get('email'); password = request.form.get('password')
        if not all([username, email, password]): flash('All fields are required.', 'warning'); return redirect(url_for('user_register'))
        if len(password) < 8: flash('Password must be >= 8 characters.', 'warning'); return redirect(url_for('user_register'))
        hashed_pw = generate_password_hash(password); existing_user = execute_query("SELECT id FROM users WHERE username = %s OR email = %s", (username, email), fetchone=True)
        if existing_user: flash('Username or email already exists.', 'danger'); return redirect(url_for('user_register'))
        query = "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)"; conn = get_db(); cursor = None;
        if not conn: return redirect(url_for('user_register'))
        try: cursor = conn.cursor(); cursor.execute(query, (username, email, hashed_pw)); flash('Registration successful! Please login.', 'success'); return redirect(url_for('user_login'))
        except mysql.connector.Error as err: print(f"User Reg Error: {err}"); flash('Registration failed.', 'danger'); return redirect(url_for('user_register'))
        finally:
             if cursor: cursor.close()
             if conn: conn.close()
    return render_template('user/register.html')

@app.route('/user/login', methods=['GET', 'POST'])
def user_login():
    if request.method == 'POST':
        username = request.form.get('username'); password = request.form.get('password')
        if not username or not password: flash('Username and password required.', 'warning'); return redirect(url_for('user_login'))
        user = execute_query("SELECT * FROM users WHERE username = %s", (username,), fetchone=True)
        if user and check_password_hash(user['password_hash'], password):
            session.clear(); session['user_id'] = user['id']; session['username'] = user['username']; session['role'] = 'user'; flash('Login successful!', 'success'); return redirect(url_for('user_dashboard'))
        else: flash('Invalid username or password.', 'danger')
    return render_template('user/login.html')

@app.route('/user/logout')
@login_required(role='user')
def user_logout(): session.clear(); flash('You have been logged out.', 'info'); return redirect(url_for('user_login'))
@app.route('/admin/register', methods=['GET', 'POST'])
def admin_register():
    if request.method == 'POST':
        username = request.form.get('username'); email = request.form.get('email'); password = request.form.get('password')
        lot_name = request.form.get('lot_name'); lot_location = request.form.get('lot_location')
        if not all([username, email, password, lot_name, lot_location]): flash('All fields required.', 'warning'); return redirect(url_for('admin_register'))
        if len(password) < 8: flash('Password must be >= 8 characters.', 'warning'); return redirect(url_for('admin_register'))
        hashed_pw = generate_password_hash(password); existing_admin = execute_query("SELECT id FROM admins WHERE username = %s OR email = %s", (username, email), fetchone=True)
        if existing_admin: flash('Admin username or email already exists.', 'danger'); return redirect(url_for('admin_register'))
        conn = get_db(); cursor = None;
        if not conn: return redirect(url_for('admin_register'))
        original_autocommit = conn.autocommit; conn.autocommit = False
        try:
            cursor = conn.cursor(); admin_query = "INSERT INTO admins (username, email, password_hash) VALUES (%s, %s, %s)"; cursor.execute(admin_query, (username, email, hashed_pw))
            admin_id = cursor.lastrowid;
            if not admin_id: raise mysql.connector.Error("Failed to get new admin ID.")
            lot_query = "INSERT INTO parking_lots (admin_id, name, location) VALUES (%s, %s, %s)"; cursor.execute(lot_query, (admin_id, lot_name, lot_location))
            conn.commit(); flash('Admin & Lot registered successfully! Please login.', 'success'); return redirect(url_for('admin_login'))
        except mysql.connector.Error as err: conn.rollback(); print(f"Admin Reg Error: {err}"); flash(f'Registration failed: {err}', 'danger'); return redirect(url_for('admin_register'))
        finally:
            conn.autocommit = original_autocommit;
            if cursor: cursor.close()
            if conn: conn.close()
    return render_template('admin/register.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username'); password = request.form.get('password')
        if not username or not password: flash('Username and password required.', 'warning'); return redirect(url_for('admin_login'))
        admin = execute_query("SELECT * FROM admins WHERE username = %s", (username,), fetchone=True)
        if admin and check_password_hash(admin['password_hash'], password):
            session.clear(); session['admin_id'] = admin['id']; session['admin_username'] = admin['username']; session['role'] = 'admin'
            lot = execute_query("SELECT id FROM parking_lots WHERE admin_id = %s LIMIT 1", (admin['id'],), fetchone=True)
            if lot: session['admin_lot_id'] = lot['id']
            else: flash('Admin has no associated parking lot.', 'warning')
            flash('Admin login successful!', 'success'); return redirect(url_for('admin_dashboard'))
        else: flash('Invalid admin username or password.', 'danger')
    return render_template('admin/login.html')

@app.route('/admin/logout')
@login_required(role='admin')
def admin_logout(): session.clear(); flash('You have been logged out.', 'info'); return redirect(url_for('admin_login'))

# --- User Portal ---
@app.route('/user/dashboard')
@login_required(role='user')
def user_dashboard():
    user_id = session['user_id']; now_sql = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    query = "SELECT b.*, p.name as lot_name, p.location FROM bookings b JOIN parking_lots p ON b.lot_id = p.id WHERE b.user_id = %s AND b.is_active = TRUE AND b.end_time >= %s ORDER BY b.start_time DESC"
    active_bookings = execute_query(query, (user_id, now_sql), fetchall=True)
    return render_template('user/dashboard.html', active_bookings=active_bookings)

@app.route('/user/search', methods=['GET'])
@login_required(role='user')
def user_search_lots():
    lots_found = []; search_query = request.args.get('location', '').strip(); print(f"--- User Search: Query='{search_query}' ---")
    if search_query:
        print(f"--- Filtered search: {search_query} ---"); query_lots = "SELECT id, name, location FROM parking_lots WHERE location LIKE %s OR name LIKE %s ORDER BY name ASC"
        term = f"%{search_query}%"; lots_found = execute_query(query_lots, (term, term), fetchall=True)
        if not lots_found: flash("No lots found matching search.", "info")
    else:
        print("--- Fetching all lots ---"); query_lots = "SELECT id, name, location FROM parking_lots ORDER BY name ASC"; lots_found = execute_query(query_lots, fetchall=True)
    lots_with_availability = []
    if lots_found:
        print(f"--- Calculating availability for {len(lots_found)} lots ---"); now_time_sql = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        query_avail = "SELECT COUNT(*) as booked_count FROM bookings WHERE lot_id = %s AND is_active = TRUE AND start_time <= %s AND end_time > %s"
        total_capacity_per_lot = 300
        for lot in lots_found:
            booked_result = execute_query(query_avail, (lot['id'], now_time_sql, now_time_sql), fetchone=True)
            booked_count = booked_result['booked_count'] if booked_result else 0; available_spots = max(0, total_capacity_per_lot - booked_count); lot['availability_now'] = available_spots
            lots_with_availability.append(lot)
    return render_template('user/search_results.html', lots=lots_with_availability, search_query=search_query)

@app.route('/user/lot/<int:lot_id>/book')
@login_required(role='user')
def user_show_booking_form(lot_id):
    print(f"--- GET /user/lot/{lot_id}/book ---"); lot = execute_query("SELECT id, name, location FROM parking_lots WHERE id = %s", (lot_id,), fetchone=True)
    if not lot: flash("Lot not found.", "danger"); print(f"--- Lot {lot_id} not found ---"); return redirect(url_for('user_search_lots'))
    print(f"--- Found Lot: {lot['name']} ---"); availability = {'0': {'capacity': 100, 'available': 0}, '1': {'capacity': 100, 'available': 0}, '2': {'capacity': 100, 'available': 0}}
    now_time = datetime.now(); now_time_sql = now_time.strftime('%Y-%m-%d %H:%M:%S')
    query_avail = "SELECT floor_number, COUNT(*) as booked_count FROM bookings WHERE lot_id = %s AND is_active = TRUE AND start_time <= %s AND end_time > %s GROUP BY floor_number"
    print(f"--- Checking availability Lot {lot_id} at {now_time_sql} ---"); current_bookings = execute_query(query_avail, (lot_id, now_time_sql, now_time_sql), fetchall=True)
    print(f"--- Availability Result: {current_bookings} ---"); total_booked = 0
    if current_bookings:
        booked_counts = {b['floor_number']: b['booked_count'] for b in current_bookings}
        for floor_str, details in availability.items(): floor_int = int(floor_str); booked = booked_counts.get(floor_int, 0); availability[floor_str]['available'] = max(0, details['capacity'] - booked); total_booked += booked
    print(f"--- Availability: {availability}, Total Booked: {total_booked} ---"); print(f"--- Rendering book_spot.html ---")
    return render_template('user/book_spot.html', lot=lot, availability=availability, total_booked=total_booked)

@app.route('/user/lot/<int:lot_id>/submit_booking', methods=['POST'])
@login_required(role='user')
def user_submit_booking(lot_id):
    print(f"--- POST /user/lot/{lot_id}/submit_booking ---"); lot_exists = execute_query("SELECT id FROM parking_lots WHERE id = %s", (lot_id,), fetchone=True)
    if not lot_exists: flash("Lot not found.", "danger"); print("--- Lot not found on submit ---"); return redirect(url_for('user_search_lots'))
    try:
        vehicle_type = request.form.get('vehicle_type'); vehicle_reg = request.form.get('vehicle_reg_number', '').strip().upper()
        start_time_str = request.form.get('start_time'); end_time_str = request.form.get('end_time')
        print(f"--- Form Data: {vehicle_type}, {vehicle_reg}, {start_time_str}, {end_time_str} ---")
        if not all([vehicle_type, vehicle_reg, start_time_str, end_time_str]): flash("All fields required.", "warning"); return redirect(url_for('user_show_booking_form', lot_id=lot_id))
        if vehicle_type not in ['2W', '4W']: flash("Invalid vehicle type.", "warning"); return redirect(url_for('user_show_booking_form', lot_id=lot_id))
        try: start_time = datetime.fromisoformat(start_time_str); end_time = datetime.fromisoformat(end_time_str); print(f"--- Parsed Times: {start_time}, {end_time} ---")
        except ValueError: flash("Invalid date format.", "warning"); print("--- Invalid datetime ---"); return redirect(url_for('user_show_booking_form', lot_id=lot_id))
        now = datetime.now()
        if start_time < (now - timedelta(minutes=1)): flash("Start time past.", "warning"); print(f"--- Start time past ---"); return redirect(url_for('user_show_booking_form', lot_id=lot_id))
        if end_time <= start_time: flash("End time before start.", "warning"); print(f"--- End time invalid ---"); return redirect(url_for('user_show_booking_form', lot_id=lot_id))
        print("--- Finding spot ---"); floor_number = 0 if vehicle_type == '2W' else 1
        spot_number = find_available_spot(lot_id, floor_number, start_time, end_time)
        if spot_number is None and vehicle_type == '4W': print("--- F1 full, trying F2 ---"); floor_number = 2; spot_number = find_available_spot(lot_id, floor_number, start_time, end_time)
        if spot_number is None: flash(f"No spots for {vehicle_type} available.", "warning"); print("--- No spot found ---"); return redirect(url_for('user_show_booking_form', lot_id=lot_id))
        amount = calculate_cost(start_time, end_time, vehicle_type); receipt_id = f"RCPT-{uuid.uuid4().hex[:8].upper()}"; user_id = session['user_id']
        print(f"--- Spot Found: F{floor_number}, S{spot_number}, ₹{amount}, R{receipt_id} ---")
        insert_query = "INSERT INTO bookings (user_id, lot_id, floor_number, spot_number, vehicle_reg_number, vehicle_type, start_time, end_time, receipt_id, amount, is_active, entry_time) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, NOW())"
        start_time_sql = start_time.strftime('%Y-%m-%d %H:%M:%S'); end_time_sql = end_time.strftime('%Y-%m-%d %H:%M:%S')
        params = (user_id, lot_id, floor_number, spot_number, vehicle_reg, vehicle_type, start_time_sql, end_time_sql, receipt_id, amount)
        print(f"--- Inserting booking ---"); conn_insert = get_db(); cursor_insert = None
        if not conn_insert: flash("Booking failed: DB issue.", "danger"); return redirect(url_for('user_show_booking_form', lot_id=lot_id))
        original_autocommit_insert = conn_insert.autocommit; conn_insert.autocommit = False
        try:
            cursor_insert = conn_insert.cursor(); cursor_insert.execute(insert_query, params); conn_insert.commit()
            print(f"--- Booking Insert OK (ID: {cursor_insert.lastrowid}) ---"); flash("Booking successful!", "success"); return redirect(url_for('user_booking_receipt', receipt_id=receipt_id))
        except mysql.connector.Error as err: conn_insert.rollback(); print(f"--- Booking Insert Failed: {err} ---"); flash("Booking failed: DB error.", "danger"); return redirect(url_for('user_show_booking_form', lot_id=lot_id))
        finally:
            conn_insert.autocommit = original_autocommit_insert
            if cursor_insert: cursor_insert.close()
            if conn_insert: conn_insert.close()
    except Exception as e: flash(f"Unexpected error: {e}", "danger"); print(f"--- !!! Unexpected Error: {e} !!! ---"); import traceback; traceback.print_exc(); return redirect(url_for('user_show_booking_form', lot_id=lot_id))

@app.route('/user/receipt/<receipt_id>')
@login_required(role='user')
def user_booking_receipt(receipt_id):
    user_id = session['user_id']; query = "SELECT b.*, p.name as lot_name, p.location FROM bookings b JOIN parking_lots p ON b.lot_id = p.id WHERE b.receipt_id = %s AND b.user_id = %s"
    booking_details = execute_query(query, (receipt_id, user_id), fetchone=True)
    if not booking_details: flash("Receipt not found/denied.", "danger"); return redirect(url_for('user_dashboard'))
    pdf_filename = f"receipt_{receipt_id}.pdf"; pdf_filepath = os.path.join(RECEIPT_FOLDER, pdf_filename); generated_pdf_name = None
    if not os.path.exists(pdf_filepath):
         print(f"--- Generating PDF for {receipt_id} ---"); generated_pdf_name = generate_pdf_receipt(booking_details)
         if not generated_pdf_name: flash("Could not generate PDF receipt.", "warning"); pdf_filename = None
         else: pdf_filename = generated_pdf_name
    return render_template('user/booking_receipt.html', booking=booking_details, pdf_filename=pdf_filename)

@app.route('/download/receipt/<filename>')
@login_required(role='user')
def download_receipt(filename):
    if '..' in filename or filename.startswith('/'): flash("Invalid filename.", "danger"); return redirect(url_for('user_dashboard'))
    if not filename.startswith('receipt_') or not filename.endswith('.pdf'): flash("Invalid receipt format.", "danger"); return redirect(url_for('user_dashboard'))
    filepath = os.path.join(RECEIPT_FOLDER, filename); print(f"--- Downloading: {filepath} ---")
    if not os.path.exists(filepath) or not os.path.isfile(filepath): flash("Receipt file not found.", "danger"); print(f"--- File not found: {filepath} ---"); return redirect(url_for('user_dashboard'))
    try: print(f"--- Sending file: {filepath} ---"); return send_from_directory(RECEIPT_FOLDER, filename, as_attachment=True)
    except Exception as e: print(f"--- Error downloading {filename}: {e} ---"); flash("Error downloading receipt.", "danger"); return redirect(url_for('user_dashboard'))

@app.route('/user/history')
@login_required(role='user')
def user_parking_history():
    user_id = session['user_id']; print(f"--- Fetching history for User: {user_id} ---")
    query = "SELECT b.receipt_id, b.vehicle_reg_number, b.vehicle_type, b.floor_number, b.spot_number, b.start_time, b.end_time, b.exit_time, b.amount, b.created_at, p.name as lot_name, p.location FROM bookings b JOIN parking_lots p ON b.lot_id = p.id WHERE b.user_id = %s AND b.is_active = FALSE ORDER BY b.exit_time DESC, b.created_at DESC"
    history = execute_query(query, (user_id,), fetchall=True); print(f"--- Found {len(history) if history else 0} history records ---")
    return render_template('user/history.html', history=history, currency_symbol='₹') # Pass symbol


@app.route('/admin/dashboard')
@login_required(role='admin')
def admin_dashboard():
    admin_id = session['admin_id']; lot_id = session.get('admin_lot_id')
    if not lot_id: flash("No lot associated.", "warning"); return render_template('admin/dashboard.html', stats={}, lot_name="N/A")
    lot = execute_query("SELECT name FROM parking_lots WHERE id = %s AND admin_id = %s", (lot_id, admin_id), fetchone=True)
    if not lot: flash("Lot not found/denied.", "danger"); session.pop('admin_lot_id', None); return render_template('admin/dashboard.html', stats={}, lot_name="Error")
    lot_name = lot['name']
    stats = {'total_parked': 0, 'parked_2w': 0, 'parked_4w_f1': 0, 'parked_4w_f2': 0,'capacity_2w': 100, 'capacity_4w_f1': 100, 'capacity_4w_f2': 100,'total_earnings': 0.00}
    print(f"--- Admin Dashboard: All Active, Lot {lot_id} ---"); query_active = "SELECT floor_number, vehicle_type, COUNT(*) as count FROM bookings WHERE lot_id = %s AND is_active = TRUE GROUP BY floor_number, vehicle_type"
    active_counts = execute_query(query_active, (lot_id,), fetchall=True); print(f"--- Active Counts: {active_counts} ---")
    if active_counts:
        for item in active_counts:
            count = item['count']; stats['total_parked'] += count
            if item['floor_number'] == 0 and item['vehicle_type'] == '2W': stats['parked_2w'] = count
            elif item['floor_number'] == 1 and item['vehicle_type'] == '4W': stats['parked_4w_f1'] = count
            elif item['floor_number'] == 2 and item['vehicle_type'] == '4W': stats['parked_4w_f2'] = count
    query_earnings = "SELECT SUM(amount) as total FROM bookings WHERE lot_id = %s AND is_active = FALSE AND exit_time IS NOT NULL AND amount > 0"
    earnings_result = execute_query(query_earnings, (lot_id,), fetchone=True)
    if earnings_result and earnings_result['total'] is not None: stats['total_earnings'] = float(earnings_result['total'])
    return render_template('admin/dashboard.html', stats=stats, lot_name=lot_name, currency_symbol='₹') # Pass symbol

@app.route('/admin/vehicles_in', methods=['GET'])
@login_required(role='admin')
def admin_vehicles_in():
    lot_id = session.get('admin_lot_id'); admin_id = session.get('admin_id')
    if not lot_id or not admin_id: flash("Admin session error.", "warning"); return redirect(url_for('admin_login'))
    lot_check = execute_query("SELECT id FROM parking_lots WHERE id = %s AND admin_id = %s", (lot_id, admin_id), fetchone=True)
    if not lot_check: flash("Access denied.", "danger"); return redirect(url_for('admin_dashboard'))
    search_term = request.args.get('search', '').strip(); print(f"--- Admin Vehicles In: All Active, Lot {lot_id} ---")
    query = "SELECT b.id, b.floor_number, b.spot_number, b.vehicle_reg_number, b.vehicle_type, b.start_time, b.end_time, b.entry_time, u.username as owner_name FROM bookings b JOIN users u ON b.user_id = u.id WHERE b.lot_id = %s AND b.is_active = TRUE"
    params = [lot_id];
    if search_term: query += " AND b.vehicle_reg_number LIKE %s"; params.append(f"%{search_term}%")
    query += " ORDER BY b.start_time ASC, b.floor_number ASC, b.spot_number ASC"
    vehicles = execute_query(query, tuple(params), fetchall=True); print(f"--- Admin Vehicles In: Result: {vehicles} ---")
    return render_template('admin/vehicles_in.html', vehicles=vehicles, search_term=search_term)

@app.route('/admin/mark_exited/<int:booking_id>', methods=['POST'])
@login_required(role='admin')
def admin_mark_exited(booking_id):
    lot_id = session.get('admin_lot_id'); admin_id = session.get('admin_id')
    if not lot_id or not admin_id: flash("Admin session error.", "danger"); return redirect(url_for('admin_login'))
    booking = execute_query("SELECT b.id FROM bookings b JOIN parking_lots p ON b.lot_id = p.id WHERE b.id = %s AND b.lot_id = %s AND p.admin_id = %s AND b.is_active = TRUE", (booking_id, lot_id, admin_id), fetchone=True)
    if booking:
        exit_time_sql = datetime.now().strftime('%Y-%m-%d %H:%M:%S'); update_query = "UPDATE bookings SET is_active = FALSE, exit_time = %s WHERE id = %s"
        conn_exit = get_db(); cursor_exit = None;
        if not conn_exit: return redirect(url_for('admin_vehicles_in'))
        original_autocommit_exit = conn_exit.autocommit; conn_exit.autocommit = False
        try: cursor_exit = conn_exit.cursor(); cursor_exit.execute(update_query, (exit_time_sql, booking_id)); conn_exit.commit(); flash("Vehicle marked as exited.", "success")
        except mysql.connector.Error as err: conn_exit.rollback(); print(f"Mark Exited Error: {err}"); flash("DB error updating status.", "danger")
        finally:
             conn_exit.autocommit = original_autocommit_exit
             if cursor_exit: cursor_exit.close()
             if conn_exit: conn_exit.close()
    else: flash("Booking not found/denied/exited.", "warning")
    return redirect(url_for('admin_vehicles_in'))

@app.route('/admin/vehicles_out')
@login_required(role='admin')
def admin_vehicles_out():
    lot_id = session.get('admin_lot_id'); admin_id = session.get('admin_id')
    if not lot_id or not admin_id: flash("Admin session error.", "warning"); return redirect(url_for('admin_login'))
    lot_check = execute_query("SELECT id FROM parking_lots WHERE id = %s AND admin_id = %s", (lot_id, admin_id), fetchone=True)
    if not lot_check: flash("Access denied.", "danger"); return redirect(url_for('admin_dashboard'))
    query = "SELECT b.id, b.floor_number, b.spot_number, b.vehicle_reg_number, b.vehicle_type, b.entry_time, b.exit_time, b.amount, b.receipt_id, u.username as owner_name FROM bookings b JOIN users u ON b.user_id = u.id WHERE b.lot_id = %s AND b.is_active = FALSE AND b.exit_time IS NOT NULL ORDER BY b.exit_time DESC"
    vehicles = execute_query(query, (lot_id,), fetchall=True)
    return render_template('admin/vehicles_out.html', vehicles=vehicles, currency_symbol='₹') # Pass symbol

@app.route('/admin/earnings')
@login_required(role='admin')
def admin_earnings():
    lot_id = session.get('admin_lot_id'); admin_id = session.get('admin_id')
    if not lot_id or not admin_id: flash("Admin session error.", "warning"); return redirect(url_for('admin_login'))
    lot_check = execute_query("SELECT id FROM parking_lots WHERE id = %s AND admin_id = %s", (lot_id, admin_id), fetchone=True)
    if not lot_check: flash("Access denied.", "danger"); return redirect(url_for('admin_dashboard'))
    query_completed = "SELECT b.id, b.receipt_id, b.vehicle_reg_number, b.vehicle_type, b.entry_time, b.exit_time, b.amount, u.username as owner_name FROM bookings b JOIN users u ON b.user_id = u.id WHERE b.lot_id = %s AND b.is_active = FALSE AND b.exit_time IS NOT NULL AND b.amount > 0 ORDER BY b.exit_time DESC"
    completed_bookings = execute_query(query_completed, (lot_id,), fetchall=True)
    total_earnings = sum(b['amount'] for b in completed_bookings if b.get('amount') is not None)
    return render_template('admin/earnings.html', bookings=completed_bookings, total_earnings=total_earnings, currency_symbol='₹') # Pass symbol

@app.route('/admin/history')
@login_required(role='admin')
def admin_history():
    lot_id = session.get('admin_lot_id'); admin_id = session.get('admin_id')
    if not lot_id or not admin_id: flash("Admin session error.", "warning"); return redirect(url_for('admin_login'))
    lot_check = execute_query("SELECT id FROM parking_lots WHERE id = %s AND admin_id = %s", (lot_id, admin_id), fetchone=True)
    if not lot_check: flash("Access denied.", "danger"); return redirect(url_for('admin_dashboard'))
    query_all = "SELECT b.id, b.receipt_id, b.floor_number, b.spot_number, b.vehicle_reg_number, b.vehicle_type, b.start_time, b.end_time, b.entry_time, b.exit_time, b.amount, b.is_active, u.username as owner_name FROM bookings b JOIN users u ON b.user_id = u.id WHERE b.lot_id = %s ORDER BY b.created_at DESC"
    all_bookings = execute_query(query_all, (lot_id,), fetchall=True)
    return render_template('admin/history.html', bookings=all_bookings, currency_symbol='₹') # Pass symbol

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
