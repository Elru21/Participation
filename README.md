# GBA 468 In-Class Participation App

This repository contains a Streamlit application used for **in-class participation** in GBA 468.  
Students submit responses during lecture; instructors control live questions and view results in real time.  
Responses and state are stored in **Google Firestore**, and the app is deployed to **Google Cloud Run**.

---

## Features

- Student check-in via NetID (session-based)
- Live instructor-controlled questions
- Multiple question types (MCQ, free text)
- Firestore-backed persistence (Cloud Run friendly)
- Instructor dashboard with:
  - Lecture selection
  - Live question control
  - Optional live results for students
  - CSV export of responses

---

## Project Structure

```
Participation/
  app.py                 # Main Streamlit app
  Dockerfile             # Cloud Run container definition
  requirements.txt       # Python dependencies (pinned)
  .gitignore
  .dockerignore
  questions/
    questions_lecture_01.json
    questions_lecture_02.json
```

> `deploy.ps1` exists locally for convenience but is **intentionally not committed**.

---

## Local Development

### 1. Activate your Python environment

Use the existing `Participation` environment:

```powershell
conda activate Participation
```

(or your equivalent activation command)

---

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

---

### 3. Authenticate to Google Cloud (ADC)

```powershell
gcloud auth application-default login
```

---

### 4. Set instructor passcode (local session)

```powershell
$env:INSTRUCTOR_KEY="GBA468"
```

---

### 5. Run the app locally

```powershell
streamlit run app.py
```

---

## App Modes

### Student mode (default)
```
http://localhost:8501
```

### Instructor mode
```
http://localhost:8501/?mode=instructor
```

Shortcut (local dev only):
```
http://localhost:8501/?mode=instructor&key=GBA468
```

---

## Firestore Notes

- Local and Cloud Run use the **same Firestore project**
- Instructor actions affect all users
- Responses are written once per NetID per question

---

## Deployment to Cloud Run

Deployment is done manually from the repo root using a local script.

```powershell
.\deploy.ps1
```

---

## Security & Secrets

- No service account JSON keys are used
- Authentication uses **Application Default Credentials (ADC)**
- Secrets are excluded via `.gitignore`

---

## Status

This app is under active development (pre-production).

---

## Maintainer

Elizabeth Mohr  
GBA 468 – Business Analytics
