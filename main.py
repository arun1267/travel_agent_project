"""Streamlit UI for the Agentic AI Travel Planner (India only)."""

from __future__ import annotations

from datetime import date, timedelta

import streamlit as st
from dotenv import load_dotenv

from agent import TravelAgent
from log import log_step
from tools import TravelTools

load_dotenv()

# ------------------------------------------------------------------
# India-only city list (display name → search value)
# ------------------------------------------------------------------
INDIAN_CITIES = {
    "Agra": "Agra",
    "Ahmedabad": "Ahmedabad",
    "Amritsar": "Amritsar",
    "Bengaluru (BLR)": "Bengaluru",
    "Bhopal": "Bhopal",
    "Bhubaneswar": "Bhubaneswar",
    "Chandigarh": "Chandigarh",
    "Chennai (MAA)": "Chennai",
    "Coimbatore": "Coimbatore",
    "Dehradun": "Dehradun",
    "Delhi (DEL)": "Delhi",
    "Goa (GOI)": "Goa",
    "Guwahati": "Guwahati",
    "Hyderabad (HYD)": "Hyderabad",
    "Indore": "Indore",
    "Jaipur": "Jaipur",
    "Jammu": "Jammu",
    "Kochi (COK)": "Kochi",
    "Kolkata (CCU)": "Kolkata",
    "Lucknow": "Lucknow",
    "Madurai": "Madurai",
    "Mangaluru": "Mangaluru",
    "Mumbai (BOM)": "Mumbai",
    "Mysuru": "Mysuru",
    "Nagpur": "Nagpur",
    "Patna": "Patna",
    "Pune (PNQ)": "Pune",
    "Raipur": "Raipur",
    "Ranchi": "Ranchi",
    "Srinagar": "Srinagar",
    "Surat": "Surat",
    "Thiruvananthapuram": "Thiruvananthapuram",
    "Tiruchirappalli": "Tiruchirappalli",
    "Udaipur": "Udaipur",
    "Vadodara": "Vadodara",
    "Varanasi": "Varanasi",
    "Visakhapatnam": "Visakhapatnam",
}

CITY_OPTIONS = ["Select city..."] + sorted(INDIAN_CITIES.keys())

st.set_page_config(
    page_title="AI Travel Planner - India",
    page_icon="✈️",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {
        max-width: 1600px;
        padding-left: 1.2rem;
        padding-right: 2.5rem;
        padding-top: 1.4rem;
    }
    [data-testid="stSidebar"] {
        min-width: 320px;
        max-width: 360px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("✈️ Agentic AI Travel Planner")
st.caption(
    "Plan trips inside India only — flights, hotels, restaurants and attractions. "
    "Ask follow-up questions about the plan in the chat."
)

# ------------------------------------------------------------------
# Sidebar: trip form (India cities only)
# ------------------------------------------------------------------
today = date.today()

with st.sidebar:
    st.subheader("Quick trip details")
    st.caption("India only • Past dates are disabled.")

    origin_label = st.selectbox("From (city)", options=CITY_OPTIONS, index=0)
    destination_label = st.selectbox("To (city)", options=CITY_OPTIONS, index=0)

    trip_kind = st.radio(
        "Travel type",
        options=["Two-way (round trip)", "One-way"],
        index=0,
        help="Two-way includes return flights on the travel end date.",
    )
    is_round_trip = trip_kind.startswith("Two-way")

    travellers = st.number_input("Travellers", min_value=1, max_value=9, value=2, step=1)
    budget = st.text_input("Total budget (optional)", placeholder="e.g. 30000")

    col1, col2 = st.columns(2)
    with col1:
        departure = st.date_input(
            "Departure",
            value=today + timedelta(days=14),
            min_value=today,
            help="Past dates are not allowed.",
        )
    with col2:
        min_return = max(today, departure)
        return_date = st.date_input(
            "Return / end date",
            value=min_return + timedelta(days=3),
            min_value=min_return,
            help="Required for two-way trips. Used as the return flight date.",
            disabled=not is_round_trip,
        )

    plan_clicked = st.button("🔍 Plan Trip", type="primary", use_container_width=True)

    if st.button("Start a new trip", use_container_width=True):
        st.session_state.messages = []
        st.session_state.pending_reply = False
        st.rerun()

# ------------------------------------------------------------------
# Session + agent
# ------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_reply" not in st.session_state:
    st.session_state.pending_reply = False


@st.cache_resource
def get_agent() -> TravelAgent:
    log_step("ui", "creating travel agent")
    return TravelAgent(tools=TravelTools())


try:
    travel_agent = get_agent()
except Exception as exc:
    st.error(f"Could not initialize the travel agent: {exc}")
    st.stop()

# ------------------------------------------------------------------
# Handle "Plan Trip" button
# ------------------------------------------------------------------
if plan_clicked:
    if origin_label == "Select city..." or destination_label == "Select city...":
        st.warning("Please select both From and To cities.")
    elif origin_label == destination_label:
        st.warning("From and To cities must be different.")
    else:
        origin = INDIAN_CITIES[origin_label]
        destination = INDIAN_CITIES[destination_label]
        budget_part = f" under ₹{budget.strip()}" if budget.strip() else ""

        if is_round_trip:
            form_prompt = (
                f"Plan a two-way (round trip) from {origin} to {destination} "
                f"for {int(travellers)} people, departing {departure.isoformat()} "
                f"and returning {return_date.isoformat()}{budget_part}. "
                "Search trip_type=round_trip. Include outbound AND return flight "
                "times on the travel end date, cheaper hotel options, a destination "
                "snapshot, attractions, restaurants, a day-by-day itinerary through "
                "the return date, and a budget breakdown. Stay within the budget "
                "if the searched prices allow; if not, say so clearly."
            )
        else:
            form_prompt = (
                f"Plan a one-way trip from {origin} to {destination} "
                f"for {int(travellers)} people, departing {departure.isoformat()}"
                f"{budget_part}. "
                "Search trip_type=one_way. Do not invent a return flight. "
                "Include destination snapshot, attractions, outbound flights, "
                "optional hotel if useful, restaurants, itinerary, and budget."
            )

        st.session_state.messages.append({"role": "user", "content": form_prompt})
        st.session_state.pending_reply = True
        st.rerun()

# ------------------------------------------------------------------
# Chat history + free text
# ------------------------------------------------------------------
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if st.session_state.pending_reply and st.session_state.messages:
    st.session_state.pending_reply = False
    last_user = st.session_state.messages[-1]["content"]
    previous_messages = st.session_state.messages[:-1]
    with st.chat_message("assistant"):
        with st.spinner("Checking travel options and preparing your itinerary..."):
            history = TravelAgent.to_bedrock_history(previous_messages)
            answer = travel_agent.chat(last_user, history)
    st.session_state.messages.append({"role": "assistant", "content": answer})
    st.rerun()

user_prompt = st.chat_input(
    "Ask anything about this trip… e.g. cheaper hotels, return flight times, or a packing list"
)

if user_prompt:
    st.session_state.messages.append({"role": "user", "content": user_prompt})
    st.session_state.pending_reply = True
    st.rerun()

st.divider()
st.caption(
    "India only • Search only — this app does not purchase flights or reserve hotel rooms. "
    "Always verify prices and complete bookings with the provider."
)