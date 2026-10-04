# Classmark QR Attendance

A Flask QR attendance app with a teacher dashboard, roster import, short-lived QR links, same-browser repeat-scan blocking, GPS distance checks, and downloadable CSV reports.

## Run locally in VS Code (Windows)

Open this project folder in VS Code, then run these commands in **Terminal → New Terminal**:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set a strong admin password and secret key. Start the app:

```powershell
.\.venv\Scripts\python.exe attendance_app.py
```

Open `http://127.0.0.1:5000`. Localhost supports location prompts. Phones need the deployed HTTPS site to use GPS; a phone cannot access the teacher's `localhost` address.

## Roster

Upload a CSV or Excel `.xlsx` file with `Roll No` and `Name` headers. Extra columns are ignored.

## Attendance protections

- Each class session accepts check-ins for 5 minutes.
- The QR link rotates every 15 seconds. Old screenshots stop working after the QR rotates.
- Students enter a roll number. After attendance is marked, a persistent browser/device lock blocks any further attendance from that browser for the same session, including if the QR is scanned again.
- Student GPS must be within 50 metres of the location recorded by the teacher when starting the class.
- After a session closes or expires, that subject cannot start a new session for 12 hours. Other subjects have their own cooldown.
- The success page confirms attendance and asks the student to close the tab. Browsers may block a webpage from closing its own tab or opening the phone home screen.

The rotating QR and GPS checks deter ordinary sharing. Since there is no student identity verification, someone using another phone who knows a classmate's roll number may still try to mark attendance as that student. A web page also cannot reliably detect GPS spoofing.

## Put it on GitHub

Create an empty repository on GitHub. In the VS Code terminal, from this folder, run:

```powershell
git init
git add .
git commit -m "Initial QR attendance app"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

`.gitignore` excludes `.env`, the local database, and the virtual environment. Do not commit your real `.env` or student database.

## Deploy with Render

The included `render.yaml` is a Render Blueprint. Push the project to GitHub, choose **New → Blueprint** in Render, and connect the repository. During setup, enter `ADMIN_USERNAME` and a strong `ADMIN_PASSWORD`; the blueprint creates the web service and PostgreSQL database. The Blueprint sets GPS verification on, a 50 m radius, five-minute sessions, 15-second QR rotation, and a 12-hour per-subject cooldown. The app builds QR links from the hosted HTTPS address, so students do not need a localhost or example-domain URL.

The Blueprint uses Render's free plans for a first deployment preview. Render documents that free web services can sleep after inactivity and free PostgreSQL databases have limited lifetimes. Choose persistent paid service/database plans before relying on the site for ongoing attendance, and confirm Render's current pricing and limits.
