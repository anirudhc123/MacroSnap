import json
import re

import streamlit as st
from google import genai
from google.genai import types
from twilio.rest import Client as TwilioClient

from prompts import SUMMARY_PROMPT, SYSTEM_PROMPT

MODEL = "gemini-2.5-flash"
PHONE_RE = re.compile(r"^\+\d{8,15}$")

st.set_page_config(page_title="MacroSnap", page_icon="🥗", layout="centered")


# ---------- helpers ----------
@st.cache_resource
def get_gemini():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"])


def to_contents(messages):
    """Convert our stored messages into Gemini's Content objects."""
    contents = []
    for m in messages:
        parts = []
        if m.get("image"):
            parts.append(types.Part.from_bytes(data=m["image"], mime_type=m["mime"]))
        if m.get("text"):
            parts.append(types.Part.from_text(text=m["text"]))
        role = "user" if m["role"] == "user" else "model"
        contents.append(types.Content(role=role, parts=parts))
    return contents


def ask_gemini(messages):
    resp = get_gemini().models.generate_content(
        model=MODEL,
        contents=to_contents(messages),
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    return resp.text or "Sorry, I couldn't come up with an answer. Try again?"


def make_summary(messages):
    contents = to_contents(messages) + [
        types.Content(role="user", parts=[types.Part.from_text(text=SUMMARY_PROMPT)])
    ]
    resp = get_gemini().models.generate_content(model=MODEL, contents=contents)
    return clean_for_template(resp.text or "")


def clean_for_template(text, limit=800):
    """WhatsApp template variables can't hold newlines, tabs or 4+ spaces."""
    text = re.sub(r"[\r\n\t]+", " | ", text)
    text = re.sub(r" {2,}", " ", text).replace("*", "").strip()
    return text[:limit]


def send_whatsapp(name, number, summary):
    client = TwilioClient(st.secrets["TWILIO_ACCOUNT_SID"], st.secrets["TWILIO_AUTH_TOKEN"])
    return client.messages.create(
        from_=st.secrets["TWILIO_WHATSAPP_FROM"],
        to=f"whatsapp:{number}",
        content_sid=st.secrets["TWILIO_CONTENT_SID"],
        content_variables=json.dumps({"1": name, "2": summary}),
    )


# ---------- session state ----------
st.session_state.setdefault("profile", None)
st.session_state.setdefault("messages", [])

# ---------- onboarding ----------
if st.session_state.profile is None:
    st.title("🥗 MacroSnap")
    st.caption("Snap your meal. Get your macros. Send them to WhatsApp.")
    with st.form("onboarding"):
        name = st.text_input("Your name")
        number = st.text_input("WhatsApp number (with country code)", placeholder="+919876543210")
        st.caption("You must have joined the Twilio sandbox from this number first.")
        go = st.form_submit_button("Start chatting", type="primary")
    if go:
        number = number.replace(" ", "")
        if not name.strip():
            st.error("Please enter your name.")
        elif not PHONE_RE.match(number):
            st.error("Number must start with + and country code, e.g. +919876543210")
        else:
            st.session_state.profile = {"name": name.strip(), "number": number}
            st.rerun()
    st.stop()

profile = st.session_state.profile

# ---------- sidebar ----------
with st.sidebar:
    st.header(f"Hi, {profile['name']} 👋")
    st.caption(f"WhatsApp: {profile['number']}")
    can_send = len(st.session_state.messages) >= 2
    if st.button("📲 Send summary to WhatsApp", type="primary",
                 disabled=not can_send, use_container_width=True):
        try:
            with st.spinner("Writing your summary..."):
                summary = make_summary(st.session_state.messages)
            with st.spinner("Sending..."):
                msg = send_whatsapp(profile["name"], profile["number"], summary)
            st.success("Sent! Check WhatsApp.")
            st.caption(f"Preview: {summary}")
        except Exception as e:
            st.error(f"Couldn't send: {e}")
    if st.button("🗑️ Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()
    if st.button("✏️ Change details", use_container_width=True):
        st.session_state.profile = None
        st.session_state.messages = []
        st.rerun()

# ---------- chat ----------
st.title("🥗 MacroSnap")

if not st.session_state.messages:
    st.info("Type what you ate, or attach a photo of your meal 📸")

user_input = st.chat_input(
    "Describe a meal or attach a photo...",
    accept_file=True,
    file_type=["jpg", "jpeg", "png", "webp"],
)

if user_input:
    text = (user_input.text or "").strip()
    files = user_input.files or []
    msg = {"role": "user", "text": text}
    if files:
        msg["image"] = files[0].getvalue()
        msg["mime"] = files[0].type or "image/jpeg"
        if not text:
            msg["text"] = "Estimate the calories and macros in this meal."
    st.session_state.messages.append(msg)
    try:
        with st.spinner("Crunching the macros..."):
            reply = ask_gemini(st.session_state.messages)
    except Exception as e:
        reply = f"⚠️ Something went wrong talking to Gemini: {e}"
    st.session_state.messages.append({"role": "assistant", "text": reply})

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        if m.get("image"):
            st.image(m["image"], width=260)
        st.markdown(m["text"])
