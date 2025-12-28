import streamlit as st
import csv
import os
import json
from collections import Counter
from datetime import datetime, date

st.set_page_config(page_title="GBA 468 In-Class Responses", layout="centered")

COURSE = "GBA 468"
DATA_FILE = "responses.csv"

QUESTIONS_DIR = "questions"
STATE_DIR = "state"

INSTRUCTOR_KEY = os.environ.get("INSTRUCTOR_KEY", "change-me")  # set on host later


# ----------------- Helpers -----------------

def is_instructor_authorized(mode: str, key: str) -> bool:
    # Only instructors with the correct key can download
    return (mode == "instructor") and (key == INSTRUCTOR_KEY)



def get_query_params():
    # Streamlit 1.30+ uses st.query_params (dict-like)
    qp = st.query_params
    mode = (qp.get("mode", "student") or "student").lower()
    class_date = qp.get("date", date.today().isoformat())
    key = qp.get("key", "")
    return mode, class_date, key


def questions_path(class_date: str) -> str:
    return os.path.join(QUESTIONS_DIR, f"questions_{class_date}.json")


def state_path(class_date: str) -> str:
    return os.path.join(STATE_DIR, f"state_{class_date}.json")


def load_questions(class_date: str) -> dict:
    path = questions_path(class_date)
    if not os.path.exists(path):
        return {
            "course": COURSE,
            "class_date": class_date,
            "title": f"(Missing {path})",
            "questions": []
        }
    try:
        return json.loads(open(path, "r", encoding="utf-8").read())
    except json.JSONDecodeError as e:
        return {
            "course": COURSE,
            "class_date": class_date,
            "title": f"(Bad JSON in {path}: {e})",
            "questions": []
        }


def load_state(class_date: str) -> dict:
    path = state_path(class_date)
    if not os.path.exists(path):
        return {"active_question_id": None, "show_results_to_students": False}
    try:
        return json.loads(open(path, "r", encoding="utf-8").read())
    except json.JSONDecodeError:
        return {"active_question_id": None, "show_results_to_students": False}


def save_state(class_date: str, state: dict):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = state_path(class_date)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def append_row(row: dict):
    file_exists = os.path.exists(DATA_FILE)
    with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def rows_for_question(question_id: str) -> list[dict]:
    if not os.path.exists(DATA_FILE) or not question_id:
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [r for r in reader if r.get("question_id") == question_id]


def get_question_by_id(questions_doc: dict, qid: str) -> dict | None:
    for q in questions_doc.get("questions", []):
        if q.get("question_id") == qid:
            return q
    return None

def has_submitted(class_date: str, question_id: str, netid: str) -> bool:
    if not os.path.exists(DATA_FILE):
        return False
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if (
                r.get("class_date") == class_date
                and r.get("question_id") == question_id
                and r.get("netid") == netid
            ):
                return True
    return False


def append_row_if_new(row: dict) -> bool:
    """
    Append the row only if there isn't already a submission for
    (class_date, question_id, netid). Returns True if written, False if blocked.
    """
    if has_submitted(row["class_date"], row["question_id"], row["netid"]):
        return False
    append_row(row)
    return True


# ----------------- Header -----------------

mode, class_date, key = get_query_params()
questions_doc = load_questions(class_date)
state = load_state(class_date)

title = f"{COURSE} — Participation"
date_str = datetime.fromisoformat(class_date).strftime("%A, %B %d, %Y") if class_date else ""

st.markdown(
    f"""
    <div style="padding: 0.75rem 1rem; border-radius: 0.75rem; border: 1px solid #e6e6e6;">
        <div style="font-size: 1.6rem; font-weight: 700;">{title}</div>
        <div style="font-size: 1rem; color: #666;">{date_str} • Mode: {mode}</div>
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
        if is_instructor_authorized(mode, key):
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "rb") as f:
                    st.download_button(
                        label="Download responses.csv",
                        data=f,
                        file_name=f"responses_{class_date}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
            else:
                st.button("Download responses.csv", disabled=True, use_container_width=True)
        else:
            # Students (and unauthorized instructor view) don't see download
            st.empty()



# ----------------- Instructor view -----------------

def instructor_view():
    # Simple protection: key in URL OR prompt (you can choose one)
    authorized = (key == INSTRUCTOR_KEY)

    if not authorized:
        st.warning("Instructor mode. Enter passcode to continue.")
        entered = st.text_input("Instructor passcode", type="password")
        if st.button("Unlock"):
            if entered == INSTRUCTOR_KEY:
                st.success("Unlocked. (Reloading)")
                # add key to URL so you don’t retype while testing
                st.query_params.update({"mode": "instructor", "date": class_date, "key": entered})
                st.rerun()
            else:
                st.error("Incorrect passcode.")
        st.stop()

    st.subheader("Instructor control panel")

    qs = questions_doc.get("questions", [])
    if not qs:
        st.error("No questions found. Add questions to the JSON file in /questions.")
        st.stop()

    labels = [f"{q.get('question_id')} — {q.get('type')} — {q.get('prompt')[:60]}" for q in qs]
    by_label = {labels[i]: qs[i] for i in range(len(qs))}

    current_qid = state.get("active_question_id")
    default_label = None
    for lab, q in by_label.items():
        if q.get("question_id") == current_qid:
            default_label = lab
            break

    selected_label = st.selectbox("Select the live question", labels, index=(labels.index(default_label) if default_label else 0))
    selected_q = by_label[selected_label]

    col1, col2 = st.columns([2, 2])
    with col1:
        if st.button("Make this question LIVE", type="primary", use_container_width=True):
            state["active_question_id"] = selected_q.get("question_id")
            save_state(class_date, state)
            st.success(f"Live: {state['active_question_id']}")
    with col2:
        show = st.checkbox("Show results to students", value=bool(state.get("show_results_to_students", False)))
        if show != bool(state.get("show_results_to_students", False)):
            state["show_results_to_students"] = show
            save_state(class_date, state)
            st.success("Updated student results visibility.")

    st.divider()
    st.markdown("### Live results (instructor)")
    q_live = get_question_by_id(questions_doc, state.get("active_question_id"))
    if not q_live:
        st.info("No active question set yet.")
        return

    st.write(f"**{q_live.get('question_id')}** — {q_live.get('prompt')}")
    rows = rows_for_question(q_live.get("question_id"))

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
    already = has_submitted(class_date, qid, st.session_state.netid)

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
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "course": COURSE,
            "class_date": class_date,
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
        rows = rows_for_question(qid)
        counts = Counter(r.get("response", "") for r in rows)
        chart_data = {opt: counts.get(opt, 0) for opt in q_live.get("options", [])}
        st.markdown("### Class results")
        st.bar_chart(chart_data)



# ----------------- Route -----------------

if mode == "instructor":
    instructor_view()
else:
    student_view()
