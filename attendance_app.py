from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import math
import os
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import qrcode
from dotenv import load_dotenv
from flask import (
    Flask, Response, abort, current_app, flash, jsonify, redirect, render_template,
    request, session, url_for,
)
from flask_sqlalchemy import SQLAlchemy
from openpyxl import load_workbook
from sqlalchemy.exc import IntegrityError
from werkzeug.middleware.proxy_fix import ProxyFix


ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
db = SQLAlchemy()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    roll_no = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)


class AttendanceSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False, index=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    started_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    closed_at = db.Column(db.DateTime)
    admin_latitude = db.Column(db.Float)
    admin_longitude = db.Column(db.Float)
    subject = db.relationship(Subject)
    roster = db.relationship("SessionStudent", back_populates="attendance_session", cascade="all, delete-orphan")
    records = db.relationship("AttendanceRecord", back_populates="attendance_session", cascade="all, delete-orphan")

    @property
    def is_open(self) -> bool:
        return self.closed_at is None and self.expires_at > utcnow()


class SessionStudent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    attendance_session_id = db.Column(db.Integer, db.ForeignKey("attendance_session.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    roll_no = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    attendance_session = db.relationship(AttendanceSession, back_populates="roster")
    student = db.relationship(Student)
    __table_args__ = (db.UniqueConstraint("attendance_session_id", "student_id", name="uq_roster_session_student"),)


class AttendanceRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    attendance_session_id = db.Column(db.Integer, db.ForeignKey("attendance_session.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    marked_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    gps_distance_meters = db.Column(db.Float)
    attendance_session = db.relationship(AttendanceSession, back_populates="records")
    student = db.relationship(Student)
    __table_args__ = (db.UniqueConstraint("attendance_session_id", "student_id", name="uq_attendance_session_student"),)


class AttendanceDeviceLock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    attendance_session_id = db.Column(db.Integer, db.ForeignKey("attendance_session.id"), nullable=False, index=True)
    device_hash = db.Column(db.String(64), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    __table_args__ = (db.UniqueConstraint("attendance_session_id", "device_hash", name="uq_attendance_session_device"),)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static", instance_path=str(ROOT / "instance"))
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    database_url = os.getenv("DATABASE_URL", f"sqlite:///{(Path(app.instance_path) / 'attendance.sqlite3').as_posix()}")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "local-only-change-this-secret"),
        SQLALCHEMY_DATABASE_URI=database_url,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        ADMIN_USERNAME=os.getenv("ADMIN_USERNAME", "admin"),
        ADMIN_PASSWORD=os.getenv("ADMIN_PASSWORD", "admin123"),
        PUBLIC_BASE_URL=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
        QR_VALIDITY_MINUTES=int(os.getenv("QR_VALIDITY_MINUTES", "5")),
        QR_ROTATE_SECONDS=int(os.getenv("QR_ROTATE_SECONDS", "15")),
        SUBJECT_COOLDOWN_HOURS=int(os.getenv("SUBJECT_COOLDOWN_HOURS", "12")),
        LOCATION_REQUIRED=os.getenv("LOCATION_REQUIRED", "true").lower() == "true",
        GPS_RADIUS_METERS=int(os.getenv("GPS_RADIUS_METERS", "50")),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(days=365),
    )
    if test_config:
        app.config.update(test_config)
    # Render and most managed hosts sit behind one trusted reverse proxy.
    if os.getenv("TRUST_PROXY", "false").lower() == "true":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    db.init_app(app)
    with app.app_context():
        db.create_all()

    app.add_template_global(get_csrf_token, "csrf_token")
    register_routes(app)
    return app


def get_csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def check_csrf() -> None:
    submitted = str(request.form.get("csrf_token", ""))
    expected = str(session.get("csrf_token", ""))
    if not expected or not secrets.compare_digest(submitted, expected):
        abort(400, description="This form expired. Refresh the page and try again.")


def attendance_device_hash() -> str:
    device_id = session.get("attendance_device_id")
    if not device_id:
        device_id = secrets.token_urlsafe(32)
        session["attendance_device_id"] = device_id
    session.permanent = True
    return hashlib.sha256(device_id.encode("utf-8")).hexdigest()


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_authenticated"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def parse_location(form) -> tuple[float | None, float | None]:
    if not current_app.config["LOCATION_REQUIRED"]:
        return None, None
    try:
        latitude, longitude = float(form["latitude"]), float(form["longitude"])
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError
        return latitude, longitude
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Location permission nahi mili. Browser settings mein location allow karke try karein.") from exc


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radians = math.pi / 180
    dlat, dlon = (lat2-lat1)*radians, (lon2-lon1)*radians
    a = math.sin(dlat/2)**2 + math.cos(lat1*radians)*math.cos(lat2*radians)*math.sin(dlon/2)**2
    return 6_371_000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def normalized_header(value) -> str:
    return "".join(c for c in str(value or "").lower() if c.isalnum())


def normalize_roll(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def qr_code_for(attendance_session: AttendanceSession, at: datetime | None = None) -> str:
    at = at or utcnow()
    slot = int(at.replace(tzinfo=timezone.utc).timestamp()) // current_app.config["QR_ROTATE_SECONDS"]
    message = f"{attendance_session.id}:{attendance_session.token}:{slot}".encode("utf-8")
    key = current_app.config["SECRET_KEY"].encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:20]


def valid_qr_code(attendance_session: AttendanceSession, code: str) -> bool:
    expected = qr_code_for(attendance_session)
    return secrets.compare_digest(expected, code)


def utc_timestamp(value: datetime) -> float:
    """Interpret database timestamps as UTC even when the host uses local time."""
    return value.replace(tzinfo=timezone.utc).timestamp()


def current_scan_url(attendance_session: AttendanceSession) -> str:
    code = qr_code_for(attendance_session)
    base = current_app.config["PUBLIC_BASE_URL"]
    if base:
        return f"{base}{url_for('student_scan', token=attendance_session.token, code=code)}"
    return url_for("student_scan", token=attendance_session.token, code=code, _external=True)


def qr_data_uri(scan_url: str) -> str:
    qr = qrcode.make(scan_url)
    buffer = io.BytesIO()
    qr.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def roster_rows(file_storage):
    filename = (file_storage.filename or "").lower()
    if filename.endswith(".csv"):
        rows = list(csv.reader(io.StringIO(file_storage.stream.read().decode("utf-8-sig"))))
    elif filename.endswith(".xlsx"):
        workbook = load_workbook(file_storage, read_only=True, data_only=True)
        rows = list(workbook.active.iter_rows(values_only=True))
    else:
        raise ValueError("Roster file .csv ya .xlsx hona chahiye.")
    if len(rows) < 2:
        raise ValueError("File mein header aur kam se kam ek student row honi chahiye.")
    headers = [normalized_header(v) for v in rows[0]]
    roll_col = next((i for i, value in enumerate(headers) if value in {"roll", "rollno", "rollnumber", "studentid"}), None)
    name_col = next((i for i, value in enumerate(headers) if value in {"name", "studentname"}), None)
    if roll_col is None or name_col is None:
        raise ValueError("Header mein Roll No aur Name columns hone chahiye.")
    result = []
    for row in rows[1:]:
        if row is None or max(roll_col, name_col) >= len(row):
            continue
        roll, name = normalize_roll(row[roll_col]), str(row[name_col] or "").strip()
        if roll and name:
            result.append((roll, name))
    if not result:
        raise ValueError("File mein valid student rows nahi mili.")
    return result


def register_routes(app: Flask) -> None:
    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def home():
        return redirect(url_for("dashboard") if session.get("admin_authenticated") else url_for("admin_login"))

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if request.method == "POST":
            check_csrf()
            username, password = request.form.get("username", ""), request.form.get("password", "")
            valid = secrets.compare_digest(username, current_app.config["ADMIN_USERNAME"]) and secrets.compare_digest(password, current_app.config["ADMIN_PASSWORD"])
            if valid:
                session.clear()
                session["admin_authenticated"] = True
                return redirect(url_for("dashboard"))
            flash("Username ya password sahi nahi hai.", "error")
        return render_template("login.html")

    @app.post("/admin/logout")
    @admin_required
    def admin_logout():
        check_csrf()
        session.clear()
        return redirect(url_for("admin_login"))

    @app.get("/admin")
    @admin_required
    def dashboard():
        students = Student.query.order_by(Student.roll_no).all()
        subjects = Subject.query.order_by(Subject.name).all()
        recent = AttendanceSession.query.order_by(AttendanceSession.started_at.desc()).limit(12).all()
        return render_template("dashboard.html", students=students, subjects=subjects, recent=recent)

    @app.post("/admin/roster")
    @admin_required
    def import_roster():
        check_csrf()
        upload = request.files.get("roster_file")
        if not upload or not upload.filename:
            flash("Pehle roster ki CSV ya Excel file chuno.", "error")
            return redirect(url_for("dashboard"))
        try:
            items = roster_rows(upload)
            added = updated = 0
            for roll_no, name in items:
                student = Student.query.filter_by(roll_no=roll_no).first()
                if student:
                    student.name, student.is_active = name, True
                    updated += 1
                else:
                    student = Student(roll_no=roll_no, name=name)
                    db.session.add(student)
                    added += 1
            db.session.commit()
            flash(f"Roster import complete: {added} naye, {updated} updated.", "success")
        except Exception as exc:
            db.session.rollback()
            flash(f"Roster import nahi hua: {exc}", "error")
        return redirect(url_for("dashboard"))

    @app.post("/admin/subjects")
    @admin_required
    def add_subject():
        check_csrf()
        name = request.form.get("name", "").strip()
        if not name:
            flash("Subject ka naam likho.", "error")
        elif Subject.query.filter(db.func.lower(Subject.name) == name.lower()).first():
            flash("Yeh subject pehle se hai.", "error")
        else:
            db.session.add(Subject(name=name))
            db.session.commit()
            flash("Subject add ho gaya.", "success")
        return redirect(url_for("dashboard"))

    @app.post("/admin/session/start/<int:subject_id>")
    @admin_required
    def start_session(subject_id: int):
        check_csrf()
        subject = db.get_or_404(Subject, subject_id)
        previous = AttendanceSession.query.filter_by(subject_id=subject.id).order_by(AttendanceSession.started_at.desc()).first()
        if previous:
            if previous.is_open:
                flash(f"{subject.name} ka session abhi live hai. Pehle usse close karein.", "error")
                return redirect(url_for("dashboard"))
            closed_at = previous.closed_at or previous.expires_at
            available_at = closed_at + timedelta(hours=current_app.config["SUBJECT_COOLDOWN_HOURS"])
            if available_at > utcnow():
                wait_minutes = math.ceil((available_at - utcnow()).total_seconds() / 60)
                flash(f"{subject.name} ka QR dobara {wait_minutes // 60} ghante {wait_minutes % 60} minute baad start ho sakta hai (12-hour subject lock).", "error")
                return redirect(url_for("dashboard"))
        roster = Student.query.filter_by(is_active=True).order_by(Student.roll_no).all()
        if not roster:
            flash("Pehle student roster import karo.", "error")
            return redirect(url_for("dashboard"))
        try:
            latitude, longitude = parse_location(request.form)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("dashboard"))
        attendance_session = AttendanceSession(
            subject=subject,
            token=secrets.token_urlsafe(32),
            started_at=utcnow(),
            expires_at=utcnow() + timedelta(minutes=current_app.config["QR_VALIDITY_MINUTES"]),
            admin_latitude=latitude,
            admin_longitude=longitude,
        )
        attendance_session.roster = [SessionStudent(student=student, roll_no=student.roll_no, name=student.name) for student in roster]
        db.session.add(attendance_session)
        db.session.commit()
        return redirect(url_for("live_session", session_id=attendance_session.id))

    @app.get("/admin/session/<int:session_id>")
    @admin_required
    def live_session(session_id: int):
        attendance_session = db.get_or_404(AttendanceSession, session_id)
        scan_url = current_scan_url(attendance_session)
        qr_data = qr_data_uri(scan_url)
        present_count = AttendanceRecord.query.filter_by(attendance_session_id=attendance_session.id).count()
        return render_template("live.html", s=attendance_session, scan_url=scan_url, qr_data=qr_data, present_count=present_count)

    @app.get("/admin/session/<int:session_id>/qr.json")
    @admin_required
    def refresh_session_qr(session_id: int):
        attendance_session = db.get_or_404(AttendanceSession, session_id)
        if not attendance_session.is_open:
            return jsonify({"open": False}), 410
        scan_url = current_scan_url(attendance_session)
        response = jsonify({"open": True, "scan_url": scan_url, "qr_data": qr_data_uri(scan_url)})
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @app.post("/admin/session/<int:session_id>/close")
    @admin_required
    def close_session(session_id: int):
        check_csrf()
        attendance_session = db.get_or_404(AttendanceSession, session_id)
        attendance_session.closed_at = utcnow()
        db.session.commit()
        flash("Attendance session close kar di.", "success")
        return redirect(url_for("live_session", session_id=session_id))

    @app.get("/admin/session/<int:session_id>/report.csv")
    @admin_required
    def download_report(session_id: int):
        attendance_session = db.get_or_404(AttendanceSession, session_id)
        marked = {record.student_id: record for record in AttendanceRecord.query.filter_by(attendance_session_id=session_id).all()}
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Roll No", "Name", "Status", "Marked At"])
        for entry in sorted(attendance_session.roster, key=lambda row: row.roll_no):
            record = marked.get(entry.student_id)
            writer.writerow([entry.roll_no, entry.name, "PRESENT" if record else "ABSENT", record.marked_at.isoformat(sep=" ", timespec="seconds") if record else ""])
        filename = f"{attendance_session.subject.name.lower().replace(' ', '_')}_session_{session_id}.csv"
        return Response("\ufeff" + output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})

    @app.route("/scan/<token>/<code>", methods=["GET", "POST"])
    def student_scan(token: str, code: str):
        attendance_session = AttendanceSession.query.filter_by(token=token).first_or_404()
        device_hash = attendance_device_hash()

        def already_used_on_device() -> bool:
            if session.get("attendance_locked_session_id") == attendance_session.id:
                return True
            return AttendanceDeviceLock.query.filter_by(
                attendance_session_id=attendance_session.id,
                device_hash=device_hash,
            ).first() is not None

        if request.method == "GET":
            if already_used_on_device():
                return render_template("scan.html", s=attendance_session, result="Aapki present is device par pehle hi lag chuki hai. Aap kisi aur ki attendance nahi laga sakte.", success=False)
            if not attendance_session.is_open:
                return render_template("scan.html", s=attendance_session, result="Yeh QR expire ho chuka hai. Teacher se naya QR lo.", success=False)
            access_key = f"scan_access_{attendance_session.id}"
            access_until = session.get(access_key, 0)
            now_ts = datetime.now(timezone.utc).timestamp()
            if not valid_qr_code(attendance_session, code) and access_until <= now_ts:
                return render_template("scan.html", s=attendance_session, result="QR photo purani ho chuki hai. Teacher ke live screen se current QR scan karein.", success=False)
            session[access_key] = min(utc_timestamp(attendance_session.expires_at), now_ts + 300)
            return render_template("scan.html", s=attendance_session, code=code)
        check_csrf()
        if already_used_on_device():
            return render_template("scan.html", s=attendance_session, result="Aapki present is device par pehle hi lag chuki hai. Aap kisi aur ki attendance nahi laga sakte.", success=False)
        if not attendance_session.is_open:
            return render_template("scan.html", s=attendance_session, result="Session close ya expire ho gaya hai.", success=False)
        access_key = f"scan_access_{attendance_session.id}"
        if session.get(access_key, 0) <= datetime.now(timezone.utc).timestamp():
            return render_template("scan.html", s=attendance_session, result="QR access expire ho gaya. Live QR ko dobara scan karein.", success=False)
        roll_no = request.form.get("roll_no", "").strip()
        entry = SessionStudent.query.filter_by(attendance_session_id=attendance_session.id, roll_no=roll_no).first()
        if not entry:
            return render_template("scan.html", s=attendance_session, code=code, result="Yeh roll number class roster mein nahi mila.", success=False)
        if AttendanceRecord.query.filter_by(attendance_session_id=attendance_session.id, student_id=entry.student_id).first():
            return render_template("scan.html", s=attendance_session, result="Aapki present is subject mein pehle hi mark hai; 12 ghante baad hi dobara mark ho sakti hai.", success=False)
        distance = None
        if current_app.config["LOCATION_REQUIRED"]:
            try:
                latitude, longitude = parse_location(request.form)
            except ValueError as exc:
                return render_template("scan.html", s=attendance_session, code=code, result=str(exc), success=False)
            distance = haversine_meters(attendance_session.admin_latitude, attendance_session.admin_longitude, latitude, longitude)
            if distance > current_app.config["GPS_RADIUS_METERS"]:
                return render_template("scan.html", s=attendance_session, code=code, result=f"Aap out of range ho: class se {distance:.0f} m door. Attendance ke liye {current_app.config['GPS_RADIUS_METERS']} m ke andar aao.", success=False)
        db.session.add(AttendanceRecord(attendance_session=attendance_session, student_id=entry.student_id, gps_distance_meters=distance))
        db.session.add(AttendanceDeviceLock(attendance_session_id=attendance_session.id, device_hash=device_hash, student_id=entry.student_id))
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if AttendanceDeviceLock.query.filter_by(attendance_session_id=attendance_session.id, device_hash=device_hash).first():
                return render_template("scan.html", s=attendance_session, result="Aapki present is device par pehle hi lag chuki hai. Aap kisi aur ki attendance nahi laga sakte.", success=False)
            return render_template("scan.html", s=attendance_session, result="Attendance pehle hi mark ho chuki hai.", success=False)
        session["attendance_locked_session_id"] = attendance_session.id
        return render_template("scan.html", s=attendance_session, result=f"Present mark ho gaya. Welcome, {entry.name}.", success=True)


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
