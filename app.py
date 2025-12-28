# app.py
import streamlit as st
import os
import json
import csv
from io import StringIO
from collections import Counter
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, firestore

st.set_page_config(page_title="GBA 468 In-Class Responses", layout="centered")

COURSE = "GBA 468"

QUESTIONS_DIR = "questions"

# Firestore doc id for state (you can make this COURSE + section later if needed)
STATE_DOC_ID = COURSE

INSTRUCTOR_KEY = os.environ.get("INSTRUCTOR_KEY", "change-me")  # set on host later


# ----------------- Firestore -----------------

def get_firestore_client():
    # Cache across reruns
    if "firestore_client" in st.session_state:
        return st.session_state["firestore_client"]

    # Initialize Firebase Admin once per server process
    if not firebase_admin._apps:
        if "FIREBASE_SERVICE_ACCOUNT" in st.secrets:
            sa = json.loads(st.secrets["FIREBASE_SERVICE_ACCOUNT"])
            cred = credentials.Certificate(sa)
            firebase_admin.initialize_app(cred)
        else:
            # Cloud Run later (ADC), or local dev (gcloud ADC)
            firebase_admin.initialize_app()

    db = firestore.client()
    st.success("✅ Firestore connected")

    st.session_state["firestore_client"] = db
    return db


def response_doc_id(course: str, lecture: str, question_id: str, netid: str) -> str:
    # Deterministic doc id enforces: 1 submission per netid per question
    return f"{course}__{lecture}__{question_id}__{netid}".replace("/", "_")


# ----------------- Helpers -----------------

def get_query_params():
    # Streamlit 1.30+ uses st.query_params (dict-like)
    qp = st.query_params
    mode = (qp.get("mode", "student") or "student").lower()
    key = qp.get("key", "")
    return mode, key


def is_instructor_authorized(mode: str, key: str) -> bool:
    return (mode == "instructor") and (key == INSTRUCTOR_KEY)


def questions_path(lecture: str) -> str:
    # lecture like "lecture_01"
    return os.path.join(QUESTIONS_DIR, f"questions_{lecture}.json")


def load_questions(lecture: str) -> dict:
    path = questions_path(lecture)
    if not os.path.exists(path):
        return {
            "course": COURSE,
            "lecture": lecture,
            "title": f"(Missing {path})",
            "questions": []
        }
    try:
        return json.loads(open(path, "r", encoding="utf-8").read())
    except json.JSONDecodeError as e:
        return {
            "course": COURSE,
            "lecture": lecture,
            "title": f"(Bad JSON in {path}: {e})",
            "questions": []
        }


def get_question_by_id(questions_doc: dict, qid: str):
    for q in questions_doc.get("questions", []):
        if q.get("question_id") == qid:
            return q
    return None


def available_lectures() -> list[str]:
    os.makedirs(QUESTIONS_DIR, exist_ok=True)
    lectures = []
    for fn in os.listdir(QUESTIONS_DIR):
        if fn.startswith("questions_lecture_") and fn.endswith(".json"):
            # questions_lecture_01.json -> lecture_01
            lectures.append(fn.replace("questions_", "").replace(".json", ""))
    return sorted(set(lectures))


# ----------------- State in Firestore (Cloud Run friendly) -----------------

def load_state() -> dict:
    db = get_firestore_client()
    ref = db.collection("class_state").document(STATE_DOC_ID)
    snap = ref.get()

    if not snap.exists:
        default = {
            "current_lecture": "lecture_01",
            "active_question_id": None,
            "show_results_to_students": False
        }
        ref.set(default)
        return default

    s = snap.to_dict() or {}
    s.setdefault("current_lecture", "lecture_01")
    s.setdefault("active_question_id", None)
    s.setdefault("show_results_to_students", False)
    return s


def save_state(state: dict):
    db = get_firestore_client()
    db.collection("class_state").document(STATE_DOC_ID).set(state, merge=True)


# ----------------- Responses in Firestore -----------------

def has_submitted(lecture: str, question_id: str, netid: str) -> bool:
    db = get_firestore_client()
    doc_id = response_doc_id(COURSE, lecture, question_id, netid)
    return db.collection("responses").document(doc_id).get().exists


def append_row_if_new(row: dict) -> bool:
    """
    Writes only if doc doesn't exist yet (course/lecture/question/netid).
    Returns True if written, False if blocked.
    """
    db = get_firestore_client()
    doc_id = response_doc_id(row["course"], row["lecture"], row["question_id"], row["netid"])
    ref = db.collection("responses").document(doc_id)

    @firestore.transactional
    def _create_if_missing(txn):
        snap = ref.get(transaction=txn)
        if snap.exists:
            return False
        txn.create(ref, row)
        return True

    return _create_if_missing(db.transaction())


def rows_for_question(lecture: str, question_id: str) -> list[dict]:
    if not lecture or not question_id:
        return []

    db = get_firestore_client()
    q = (
        db.collection("responses")
        .where("course", "==", COURSE)
        .where("lecture", "==", lecture)
        .where("question_id", "==", question_id)
        .order_by("timestamp")
    )
    return [d.to_dict() for d in q.stream()]


def export_responses_csv_for_lecture(lecture: str) -> bytes | None:
    db = get_firestore_client()
    q = (
        db.collection("responses")
        .where("course", "==", COURSE)
        .where("lecture", "==", lecture)
        .order_by("timestamp")
    )
    docs = [d.to_dict() for d in q.stream()]
    if not docs:
        return None

    fieldnames = [
        "timestamp", "course", "lecture", "netid",
        "question_id", "question_type", "question_prompt", "response",
    ]
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in docs:
        writer.writerow({k: r.get(k, "") for k in fieldnames})

    return buf.getvalue().encode("utf-8")


# ----------------- Header -----------------

mode, key = get_query_params()
state = load_state()

lecture = state.get("current_lecture", "lecture_01")
questions_doc = load_questions(lecture)

title = f"{COURSE} — Participation"

st.markdown(
    f"""
    <div style="padding: 0.75rem 1rem; border-radius: 0.75rem; border: 1px solid #e6e6e6;">
        <div style="font-size: 1.6rem; font-weight: 700;">{title}</div>
        <div style="font-size: 1rem; color: #666;">Lecture: {lecture} • Mode: {mode}</div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.write("")

# ----------------- Main controls (mobile-friendly) -----------------

controls = st.container()
with controls:
    c1, c2 = st.columns([1, 1])

    with c1:
        if st.button("Refresh page", use_container_width=True):
            st.rerun()

    with c2:
        # Instructor-only download (now exports from Firestore)
        if is_instructor_authorized(mode, key):
            csv_bytes = export_responses_csv_for_lecture(lecture)
            if csv_bytes:
                st.download_button(
                    label="Download responses.csv",
                    data=csv_bytes,
                    file_name=f"responses_{lecture}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            else:
                st.button("Download responses.csv", disabled=True, use_container_width=True)
        else:
            st.empty()


# ----------------- Instructor view -----------------

def instructor_view():
    authorized = (key == INSTRUCTOR_KEY)

    if not authorized:
        st.warning("Instructor mode. Enter passcode to continue.")
        entered = st.text_input("Instructor passcode", type="password")
        if st.button("Unlock", use_container_width=True):
            if entered == INSTRUCTOR_KEY:
                st.success("Unlocked. (Reloading)")
                st.query_params.update({"mode": "instructor", "key": entered})
                st.rerun()
            else:
                st.error("Incorrect passcode.")
        st.stop()

    st.subheader("Instructor control panel")

    # -------- Lecture selector --------
    lectures = available_lectures()
    if not lectures:
        st.error("No question files found in /questions. Expected: questions_lecture_01.json, etc.")
        st.stop()

    current_lecture = state.get("current_lecture", lectures[0])
    idx = lectures.index(current_lecture) if current_lecture in lectures else 0
    selected_lecture = st.selectbox("Current lecture", lectures, index=idx)

    if selected_lecture != state.get("current_lecture"):
        state["current_lecture"] = selected_lecture
        state["active_question_id"] = None  # reset when switching lectures
        state["show_results_to_students"] = False
        save_state(state)
        st.success(f"Lecture set to {selected_lecture}")
        st.rerun()

    # Use questions for the currently selected lecture
    lecture_local = state.get("current_lecture", selected_lecture)
    questions_doc_local = load_questions(lecture_local)

    qs = questions_doc_local.get("questions", [])
    if not qs:
        st.error(f"No questions found for {lecture_local}. Add questions to {questions_path(lecture_local)}.")
        st.stop()

    labels = [f"{q.get('question_id')} — {q.get('type')} — {q.get('prompt')[:60]}" for q in qs]
    by_label = {labels[i]: qs[i] for i in range(len(qs))}

    current_qid = state.get("active_question_id")
    default_label = None
    for lab, q in by_label.items():
        if q.get("question_id") == current_qid:
            default_label = lab
            break

    selected_label = st.selectbox(
        "Select the live question",
        labels,
        index=(labels.index(default_label) if default_label else 0),
    )
    selected_q = by_label[selected_label]

    col1, col2 = st.columns([2, 2])
    with col1:
        if st.button("Make this question LIVE", type="primary", use_container_width=True):
            state["active_question_id"] = selected_q.get("question_id")
            save_state(state)
            st.success(f"Live: {state['active_question_id']}")
            st.rerun()

    with col2:
        show = st.checkbox(
            "Show results to students",
            value=bool(state.get("show_results_to_students", False)),
        )
        if show != bool(state.get("show_results_to_students", False)):
            state["show_results_to_students"] = show
            save_state(state)
            st.success("Updated student results visibility.")
            st.rerun()

    st.divider()
    st.markdown("### Live results (instructor)")

    q_live = get_question_by_id(questions_doc_local, state.get("active_question_id"))
    if not q_live:
        st.info("No active question set yet.")
        return

    st.write(f"**{q_live.get('question_id')}** — {q_live.get('prompt')}")

    rows = rows_for_question(lecture_local, q_live.get("question_id"))

    if q_live.get("type") == "mcq":
        options = q_live.get("options", [])
        counts = Counter(r.get("response", "") for r in rows)
        chart_data = {opt: counts.get(opt, 0) for opt in options}
        st.bar_chart(chart_data)
    else:
        st.write(f"{len(rows)} responses so far.")
        with st.expander("View latest responses"):
            for r in rows[-20:]:
                st.write(f"- `{r.get('netid')}`: {r.get('response')}")

    st.caption("")


# ----------------- Student view -----------------

def student_view():
    # Ensure netid exists in session state
    if "netid" not in st.session_state:
        st.session_state.netid = ""

    # -------- NetID screen (phone-friendly) --------
    if not st.session_state.netid:
        st.markdown("## Check in")
        st.caption("Enter your NetID once. It will be attached to every submission.")

        netid = st.text_input(
            "NetID",
            placeholder="e.g., abc123",
        ).strip().lower()

        if st.button("Save NetID", type="primary", use_container_width=True):
            if not netid:
                st.warning("Please enter your NetID.")
            else:
                st.session_state.netid = netid
                st.rerun()

        st.stop()

    # -------- Compact signed-in row --------
    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown(
            f"<div style='font-size: 0.95rem; color: #444;'>"
            f"Signed in as <span style='font-weight:700;'>{st.session_state.netid}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c2:
        if st.button("Change", use_container_width=True):
            st.session_state.netid = ""
            st.rerun()

    st.write("")

    # -------- Current live question --------
    q_live = get_question_by_id(questions_doc, state.get("active_question_id"))
    if not q_live:
        st.info("No question is live yet. Please wait.")
        return

    qid = q_live.get("question_id")
    already = has_submitted(lecture, qid, st.session_state.netid)

    if already:
        st.caption("✅ You already submitted a response for this question.")

    # Prompt as a card
    st.markdown("### Current question")
    st.markdown(
        f"<div style='padding: 0.9rem 1rem; border-radius: 0.9rem; border: 1px solid #e6e6e6;'>"
        f"<div style='font-size: 1.05rem; font-weight: 600; margin-bottom: 0.35rem;'>"
        f"{q_live.get('question_id', '')}"
        f"</div>"
        f"<div style='font-size: 1.05rem;'>"
        f"{q_live.get('prompt', '')}"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.write("")

    # -------- Response input --------
    response_value = None
    qtype = q_live.get("type")

    if qtype == "text":
        response_value = st.text_area(
            "Your answer",
            placeholder="Type your response here…",
            disabled=already,
        )

    elif qtype == "mcq":
        options = q_live.get("options", [])
        if not options:
            st.error("This multiple-choice question has no options configured.")
            return

        response_value = st.radio(
            "Select one:",
            options,
            index=None,
            disabled=already,
        )

    else:
        st.error(f"Unknown question type: {qtype}")
        return

    st.write("")

    # -------- Submit (full-width, always requires non-empty) --------
    if st.button("Submit", type="primary", disabled=already, use_container_width=True):
        cleaned = "" if response_value is None else str(response_value).strip()

        if not cleaned:
            st.warning("Please enter or select an answer before submitting.")
            return

        payload = {
            # Use UTC so ordering is clean across environments
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "course": COURSE,
            "lecture": lecture,
            "netid": st.session_state.netid,
            "question_id": qid,
            "question_type": qtype,
            "question_prompt": q_live.get("prompt"),
            "response": cleaned,
        }

        wrote = append_row_if_new(payload)
        if wrote:
            st.success("Saved ✅")
            st.rerun()
        else:
            st.info("✅ Already submitted (nothing changed).")

    # -------- Optional: show results to students (controlled by instructor) --------
    if bool(state.get("show_results_to_students", False)) and qtype == "mcq":
        rows = rows_for_question(lecture, qid)
        counts = Counter(r.get("response", "") for r in rows)
        chart_data = {opt: counts.get(opt, 0) for opt in q_live.get("options", [])}
        st.markdown("### Class results")
        st.bar_chart(chart_data)


# ----------------- Route -----------------

if mode == "instructor":
    instructor_view()
else:
    student_view()
