# app.py
import streamlit as st
import os
import json
import csv
from io import StringIO
from collections import Counter
from datetime import datetime, timezone
from google.cloud.firestore_v1 import FieldFilter


import firebase_admin
from firebase_admin import firestore

st.set_page_config(page_title="GBA 468 In-Class Responses", layout="centered")

st.markdown("""
<style>
/* Slightly round buttons for a modern, touch-friendly feel */
.stButton > button {
    border-radius: 0.75rem;
}

/* Ensure logos/images keep sharp corners */
img {
    border-radius: 0 !important;
}
            
/* Hide Streamlit anchor (link) icons */
a[href^="#"] {
    display: none !important;
}
</style>
""", unsafe_allow_html=True)



COURSE = "GBA 468"
QUESTIONS_DIR = "questions"
STATE_DOC_ID = COURSE
INSTRUCTOR_KEY = os.environ.get("INSTRUCTOR_KEY", "change-me")  # set on host later


# ----------------- Firestore -----------------
@st.cache_resource
def get_firestore_client():
    # Initialize Firebase Admin once per server process (ADC)
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    return firestore.client()


def response_doc_id(course: str, lecture: str, session_id: str, question_id: str, netid: str) -> str:
    return f"{course}__{lecture}__{session_id}__{question_id}__{netid}".replace("/", "_")



# ----------------- Helpers -----------------

def _qp_get(qp, k, default=""):
    v = qp.get(k, default)
    if isinstance(v, list):
        v = v[0] if v else default
    return v



def get_query_params():
    qp = st.query_params
    mode = (_qp_get(qp, "mode", "student") or "student").lower()
    key = _qp_get(qp, "key", "")
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

def render_question_input(q_live: dict, disabled: bool):
    qtype = q_live.get("type")

    if qtype == "text":
        multiline = bool(q_live.get("multiline", True))
        if multiline:
            val = st.text_area("Your answer", disabled=disabled)
        else:
            val = st.text_input("Your answer", disabled=disabled)
        return qtype, val

    if qtype == "single_choice":
        options = q_live.get("options", [])
        val = st.radio("Select one:", options, index=None, disabled=disabled)
        return qtype, val

    if qtype == "multi_choice":
        options = q_live.get("options", [])
        val = st.multiselect("Select all that apply:", options, disabled=disabled)
        return qtype, val

    if qtype == "multi_text":
        answers = {}
        for f in q_live.get("fields", []):
            key = f.get("key")
            label = f.get("label", key)
            answers[key] = st.text_input(label, disabled=disabled)
        return qtype, answers

    st.error(f"Unknown question type: {qtype}")
    return qtype, None

def validate_response(q_live: dict, response_value):
    qtype = q_live.get("type")

    if qtype in ("text", "single_choice"):
        cleaned = "" if response_value is None else str(response_value).strip()
        return (bool(cleaned), cleaned)

    if qtype == "multi_choice":
        selected = response_value or []
        min_sel = int(q_live.get("min_selected", 0))
        max_sel = q_live.get("max_selected")
        if len(selected) < min_sel:
            return (False, selected)
        if max_sel is not None and len(selected) > int(max_sel):
            return (False, selected)
        return (len(selected) > 0 or min_sel == 0, selected)

    if qtype == "multi_text":
        fields = q_live.get("fields", [])
        response_value = response_value or {}
        cleaned = {f["key"]: (response_value.get(f["key"], "") or "").strip() for f in fields}
        # require all non-empty by default
        require_all = bool(q_live.get("require_all", True))
        ok = all(v for v in cleaned.values()) if require_all else any(v for v in cleaned.values())
        return (ok, cleaned)

    return (False, response_value)



# ----------------- State in Firestore (Cloud Run friendly) -----------------

def load_state() -> dict:
    db = get_firestore_client()
    ref = db.collection("class_state").document(STATE_DOC_ID)
    snap = ref.get()

    if not snap.exists:
        default = {
            "current_lecture": "lecture_01",
            "session_id": datetime.now().strftime("%Y-%m-%d"),
            "active_question_id": None
        }
        ref.set(default)
        return default

    s = snap.to_dict() or {}
    s.setdefault("current_lecture", "lecture_01")
    s.setdefault("session_id", datetime.now().strftime("%Y-%m-%d"))
    s.setdefault("active_question_id", None)
    return s


def save_state(state: dict):
    db = get_firestore_client()
    db.collection("class_state").document(STATE_DOC_ID).set(state, merge=True)


# ----------------- Responses in Firestore -----------------

def has_submitted(lecture: str, session_id: str, question_id: str, netid: str) -> bool:
    db = get_firestore_client()
    doc_id = response_doc_id(COURSE, lecture, session_id, question_id, netid)
    return db.collection("responses").document(doc_id).get().exists


def append_row_if_new(row: dict) -> bool:
    """
    Writes only if doc doesn't exist yet (course/lecture/question/netid).
    Returns True if written, False if blocked.
    """
    db = get_firestore_client()
    doc_id = response_doc_id(row["course"], row["lecture"], row["session_id"], row["question_id"], row["netid"])
    ref = db.collection("responses").document(doc_id)

    @firestore.transactional
    def _create_if_missing(txn):
        snap = ref.get(transaction=txn)
        if snap.exists:
            return False
        txn.create(ref, row)
        return True

    return _create_if_missing(db.transaction())


def rows_for_question(lecture: str, session_id: str, question_id: str) -> list[dict]:
    if not lecture or not question_id:
        return []

    db = get_firestore_client()
    q = (
        db.collection("responses")
        .where(filter=FieldFilter("course", "==", COURSE))
        .where(filter=FieldFilter("lecture", "==", lecture))
        .where(filter=FieldFilter("session_id", "==", session_id))
        .where(filter=FieldFilter("question_id", "==", question_id))
        .order_by("timestamp")
    )
    return [d.to_dict() for d in q.stream()]


def csv_safe(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def export_responses_csv_for_lecture(lecture: str, session_id: str | None) -> bytes | None:
    db = get_firestore_client()

    q = (
        db.collection("responses")
        .where(filter=FieldFilter("course", "==", COURSE))
        .where(filter=FieldFilter("lecture", "==", lecture))
    )

    # If session_id is provided, restrict to that session
    if session_id:
        q = q.where(filter=FieldFilter("session_id", "==", session_id))

    q = q.order_by("timestamp")

    docs = [d.to_dict() for d in q.stream()]
    if not docs:
        return None

    fieldnames = [
        "timestamp", "course", "lecture", "session_id", "netid",
        "question_id", "question_type", "question_prompt", "response",
    ]

    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in docs:
        writer.writerow({k: csv_safe(r.get(k, "")) for k in fieldnames})

    return buf.getvalue().encode("utf-8")



# ----------------- Header -----------------

mode, key = get_query_params()
state = load_state()

lecture = state.get("current_lecture", "lecture_01")
questions_doc = load_questions(lecture)

LOGO_PATH = "assets/UnivRoch-Simon-vert-navyRGB.svg"

with st.container():
    left, right = st.columns([1.3, 3], vertical_alignment="center")
    with left:
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=110)  # tuned for phones
    with right:
        st.markdown("### GBA 468 — In-Class Participation")
        st.caption(f"{lecture} • {state.get('session_id','')}")

st.write("")  # small spacer


# ----------------- Main controls (mobile-friendly) -----------------

controls = st.container()
with controls:
    c1, c2 = st.columns([1, 1])

    with c1:
        if mode in ("instructor", "results"):
            if st.button("Update", use_container_width=True):
                st.rerun()
        else:
            st.empty()


    with c2:
        if is_instructor_authorized(mode, key):
            with st.popover("Download responses"):
                choice = st.selectbox(
                    "Export scope",
                    ["Current session", "All sessions"],
                    index=0,
                )

                export_sid = state.get("session_id") if choice == "Current session" else None
                fname_scope = state.get("session_id") if export_sid else "all"

                csv_bytes = export_responses_csv_for_lecture(lecture, export_sid)

                if csv_bytes:
                    st.download_button(
                        "Download CSV",
                        data=csv_bytes,
                        file_name=f"responses_{lecture}_{fname_scope}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                else:
                    st.caption("No data available.")




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

    # ------------- Set Session ID -----------------
    session_id = st.text_input(
        "Session ID",
        value=state.get("session_id", ""),
        help="Example: 2025-12-29_SectionA",
    )

    if session_id != state.get("session_id"):
        state["session_id"] = session_id.strip()
        save_state(state)
        st.success(f"Session set to {state['session_id']}")
        st.rerun()


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


    st.divider()
    st.markdown("### Live results (instructor)")

    q_live = get_question_by_id(questions_doc_local, state.get("active_question_id"))
    if not q_live:
        st.info("No active question set yet.")
        return

    st.write(f"**{q_live.get('question_id')}** — {q_live.get('prompt')}")

    rows = rows_for_question(lecture_local, state.get("session_id", ""), q_live.get("question_id"))

    qtype_live = q_live.get("type")

    if qtype_live == "single_choice":
        options = q_live.get("options", [])
        counts = Counter(r.get("response", "") for r in rows)
        chart_data = {opt: counts.get(opt, 0) for opt in options}
        st.bar_chart(chart_data)

    elif qtype_live == "multi_choice":
        options = q_live.get("options", [])
        counts = Counter()
        for r in rows:
            for choice in (r.get("response") or []):
                counts[choice] += 1
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
    session_id = state.get("session_id", "")
    already = has_submitted(lecture, session_id, qid, st.session_state.netid)

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
    qtype, response_value = render_question_input(q_live, disabled=already)
    st.write("")

    # -------- Submit (full-width, always requires non-empty) --------
    if st.button("Submit", type="primary", disabled=already, use_container_width=True):
        ok, cleaned = validate_response(q_live, response_value)
        if not ok:
            st.warning("Please complete your answer before submitting.")
            return

        payload = {
            # Use UTC so ordering is clean across environments
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "course": COURSE,
            "lecture": lecture,
            "session_id": state.get("session_id", ""),
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

    st.write("")
    if st.button("Check for new question", type="secondary", use_container_width=True):
        st.rerun()


# ----------------- Projector view -----------------
def results_view():
    st.subheader("Live results")

    # Always use the currently selected lecture from Firestore state
    lecture_local = state.get("current_lecture", "lecture_01")
    questions_doc_local = load_questions(lecture_local)

    q_live = get_question_by_id(questions_doc_local, state.get("active_question_id"))
    if not q_live:
        st.info("No active question set yet.")
        return

    st.markdown(f"## {q_live.get('question_id')}")
    st.markdown(f"### {q_live.get('prompt')}")

    rows = rows_for_question(lecture_local, state.get("session_id", ""), q_live.get("question_id"))
    qtype_live = q_live.get("type")

    if qtype_live == "single_choice":
        options = q_live.get("options", [])
        counts = Counter(r.get("response", "") for r in rows)
        chart_data = {opt: counts.get(opt, 0) for opt in options}
        st.bar_chart(chart_data)
        st.caption(f"{len(rows)} responses")

    elif qtype_live == "multi_choice":
        options = q_live.get("options", [])
        counts = Counter()
        for r in rows:
            for choice in (r.get("response") or []):
                counts[choice] += 1
        chart_data = {opt: counts.get(opt, 0) for opt in options}
        st.bar_chart(chart_data)
        st.caption(f"{len(rows)} submissions")

    else:
        st.caption(f"{len(rows)} responses")
        with st.expander("Show recent responses"):
            for r in rows[-25:]:
                st.write(f"- {r.get('response')}")




# ----------------- Route -----------------

if mode == "instructor":
    instructor_view()
elif mode == "results":
    results_view()
else:
    student_view()
