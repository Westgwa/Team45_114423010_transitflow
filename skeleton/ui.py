"""
TransitFlow — Gradio Web Interface
====================================
Run with:  python skeleton/ui.py
Then open: http://localhost:7860

Students: You do NOT need to change this file.
"""

# TASK 6 EXTENSION: Added an analytics dashboard panel to surface booking revenue data.

import sys
sys.path.insert(0, ".")

import gradio as gr
from skeleton.agent import run_agent
from skeleton.llm_provider import llm
from skeleton.config import GEMINI_CHAT_MODEL, OLLAMA_CHAT_MODEL
from databases.relational.queries import (
    login_user,
    register_user,
    get_user_secret_question,
    verify_secret_answer,
    update_password,
    query_booking_revenue_summary,
    query_trip_history,
    query_route_visualization,
)

SECRET_QUESTIONS = [
    "What is the name of your first pet?",
    "What is your mother's maiden name?",
    "What city were you born in?",
    "What was the name of your first school?",
    "What is your favourite book?",
    "What was the make of your first car?",
]


# ── Chat handler ───────────────────────────────────────────────────────────────

def chat(user_message: str, history_display: list, agent_history: list,
         show_debug: bool, current_user: str):
    if not user_message.strip():
        return history_display, agent_history, gr.update()

    if show_debug:
        answer, new_agent_history, debug_text = run_agent(
            user_message, agent_history, debug=True, current_user_email=current_user
        )
    else:
        answer, new_agent_history = run_agent(
            user_message, agent_history, debug=False, current_user_email=current_user
        )
        debug_text = ""

    history_display = history_display + [
        {"role": "user",      "content": user_message},
        {"role": "assistant", "content": answer},
    ]

    debug_update = gr.update(value=debug_text, visible=show_debug)
    return history_display, new_agent_history, debug_update


def clear_conversation():
    return [], [], gr.update(value="", visible=False)


# ── Provider / model selection ────────────────────────────────────────────────

_KNOWN_OLLAMA_MODELS = ["llama3.2:1b", "llama3.1:8b"]


def get_ollama_status():
    if llm.ollama_available():
        return "🟢 Ollama is running locally"
    return "🔴 Ollama not detected — install from ollama.com and run `ollama pull " + OLLAMA_CHAT_MODEL + "`"


def get_chat_model_choices() -> list:
    available = set(llm.get_available_ollama_models())
    choices = []
    for m in _KNOWN_OLLAMA_MODELS:
        label = m if m in available else f"{m}  (not pulled)"
        choices.append((label, m))
    choices.append((f"☁️ Gemini ({GEMINI_CHAT_MODEL})", "gemini"))
    return choices


def get_initial_chat_model_value() -> str:
    return "llama3.2:1b"


def on_chat_model_change(value: str):
    if value == "gemini":
        status = llm.set_chat_provider("gemini")
        return f"**Active:** ☁️ Gemini ({GEMINI_CHAT_MODEL})\n\n{status}", get_ollama_status()
    available = set(llm.get_available_ollama_models())
    if value not in available:
        return f"⚠️ `{value}` is not pulled. Run: `ollama pull {value}`", get_ollama_status()
    llm.set_chat_provider("ollama")
    status = llm.set_chat_model(value)
    return f"**Active:** {value}\n\n{status}", get_ollama_status()


# ── Auth handlers ──────────────────────────────────────────────────────────────

def do_login(email: str, password: str):
    """Handle login form submission."""
    if not email.strip() or not password.strip():
        return (
            gr.update(value="Please enter your email and password.", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    user = login_user(email.strip(), password)
    if user is None:
        return (
            gr.update(value="Incorrect email or password.", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    display_name = f"{user['first_name']} {user['surname']}"
    return (
        gr.update(value="", visible=False),
        user["email"],
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(value=f"**Welcome, {display_name}**", visible=True),
        gr.update(visible=True),
        gr.update(visible=False),
    )


def do_logout():
    return (
        None,
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(value="", visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
    )


def do_register(email, first_name, surname, year_of_birth, password, secret_question, secret_answer):
    """Handle registration form submission."""
    if not all([
        str(email).strip(), str(first_name).strip(), str(surname).strip(),
        str(password).strip(), secret_question, str(secret_answer).strip(),
    ]):
        return (
            gr.update(value="All fields are required.", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    try:
        year = int(year_of_birth)
        if year < 1900 or year > 2015:
            raise ValueError
    except (ValueError, TypeError):
        return (
            gr.update(value="Please enter a valid year of birth (e.g. 1990).", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    ok, err = register_user(
        email.strip(), first_name.strip(), surname.strip(),
        year, password, secret_question, secret_answer.strip(),
    )
    if not ok:
        return (
            gr.update(value=err, visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    display_name = f"{first_name.strip()} {surname.strip()}"
    return (
        gr.update(value="", visible=False),
        email.strip().lower(),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(value=f"**Welcome, {display_name}**", visible=True),
        gr.update(visible=True),
        gr.update(visible=False),
    )


def forgot_find_question(email: str):
    """Step 1 — look up the secret question for the given email."""
    if not email.strip():
        return (
            gr.update(value="Please enter your email address.", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
        )

    question = get_user_secret_question(email.strip())
    if question is None:
        return (
            gr.update(value="No account found with that email address.", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
        )

    return (
        gr.update(value="", visible=False),
        gr.update(value=f"**Your security question:** {question}", visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
    )


def forgot_reset_password(email: str, answer: str, new_password: str):
    """Step 2 — verify the secret answer and update the password."""
    if not str(answer).strip() or not str(new_password).strip():
        return gr.update(value="Please fill in all fields.", visible=True)

    if not verify_secret_answer(email.strip(), answer.strip()):
        return gr.update(value="Incorrect answer. Please try again.", visible=True)

    if not update_password(email.strip(), new_password):
        return gr.update(value="Failed to update password. Please try again.", visible=True)

    return gr.update(value="**Password reset successfully. You can now log in.**", visible=True)


def render_booking_summary(summary: dict) -> str:
    """Convert booking summary metrics into a markdown-friendly string."""
    if not summary:
        return "No analytics data available."

    return "\n".join([
        f"**Booking Analytics Dashboard**",
        f"- Total bookings: **{summary.get('total_bookings', 0)}**",
        f"- Active bookings: **{summary.get('active_bookings', 0)}**",
        f"- Cancelled bookings: **{summary.get('cancelled_bookings', 0)}**",
        f"- Total revenue (USD): **${summary.get('total_revenue_usd', 0):,.2f}**",
        f"- Total refunds (USD): **${summary.get('total_refunds_usd', 0):,.2f}**",
        f"- Date range: **{summary.get('start_date') or 'all'}** to **{summary.get('end_date') or 'all'}**",
    ])


def get_booking_analytics(start_date: str, end_date: str):
    """Fetch booking analytics summary for the given date range."""
    summary = query_booking_revenue_summary(
        start_date=start_date.strip() or None,
        end_date=end_date.strip() or None,
    )
    return render_booking_summary(summary)


def render_trip_history(user_email: str) -> str:
    """Format trip history into a markdown-friendly display."""
    if not user_email:
        return "Please log in to view your trip history."

    history = query_trip_history(user_email, limit=10)

    if "error" in history:
        return f"Error: {history.get('error')}"

    trips = history.get("trips", [])
    if not trips:
        return "No trips found in your history."

    # Build a markdown table of trips
    lines = [
        "## Your Trip History",
        "",
        "| Booking ID | From | To | Date | Fare Class | Amount | Status |",
        "|---|---|---|---|---|---|---|",
    ]

    for trip in trips:
        booking_id = trip.get("booking_id", "—")
        origin = trip.get("origin_station_id", "—")
        destination = trip.get("destination_station_id", "—")
        travel_date = trip.get("travel_date", "—")
        fare_class = trip.get("fare_class", "standard")
        price = f"${trip.get('price_paid_usd', 0):.2f}"
        status = trip.get("status", "unknown").capitalize()

        lines.append(
            f"| {booking_id} | {origin} | {destination} | {travel_date} | {fare_class} | {price} | {status} |"
        )

    return "\n".join(lines)


def render_route_visualization(origin: str, destination: str, route_type: str) -> str:
    """
    # TASK 6 EXTENSION:
    Format route information into a visual markdown display.
    """
    if not origin or not destination:
        return "Please enter both origin and destination station IDs."

    route_data = query_route_visualization(origin.strip(), destination.strip(), route_type)

    if "error" in route_data:
        return f"Error: {route_data.get('error')}"

    routes = route_data.get("routes", [])
    if not routes:
        return f"No routes found from {origin} to {destination}."

    lines = [
        f"## Route Visualization: {origin} → {destination}",
        "",
        f"**Type:** {route_type.replace('_', ' ').title()}  ",
        f"**Total Routes Found:** {route_data.get('count', 0)}",
        "",
    ]

    for idx, route in enumerate(routes, 1):
        lines.append(f"### Route {idx}: Line {route.get('line', '—')}")
        lines.append(f"- **Service Type:** {route.get('service_type', '—')}")
        lines.append(f"- **Direction:** {route.get('direction', '—')}")
        lines.append(f"- **Schedule ID:** {route.get('schedule_id', '—')}")
        lines.append(f"- **First Train:** {route.get('first_train_time', '—')}")
        lines.append(f"- **Last Train:** {route.get('last_train_time', '—')}")
        lines.append(f"- **Frequency:** Every {route.get('frequency_min', '—')} minutes")
        lines.append("")

        # Display stops timeline
        stops_detail = route.get("stops_detail", [])
        if stops_detail:
            lines.append("**Route Stops:**")
            lines.append("")
            for stop in stops_detail:
                station_id = stop.get("station_id", "—")
                travel_time = stop.get("travel_time_min", 0)
                lines.append(f"- **{station_id}** (arrival: {travel_time} min from origin)")

            lines.append("")

        # Display fare classes
        fare_summary = route.get("fare_summary", {})
        if fare_summary:
            lines.append("**Fare Classes:**")
            lines.append("")
            for fare_class, pricing in fare_summary.items():
                if isinstance(pricing, dict):
                    base = pricing.get("base_fare_usd", 0)
                    per_stop = pricing.get("per_stop_rate_usd", 0)
                    lines.append(f"- **{fare_class}:** ${base} base + ${per_stop} per stop")
            lines.append("")

    return "\n".join(lines)


# ── Panel visibility toggles ──────────────────────────────────────────────────

def show_login_panel():
    return gr.update(visible=True), gr.update(visible=False), gr.update(visible=False)

def show_register_panel():
    return gr.update(visible=False), gr.update(visible=True), gr.update(visible=False)

def show_forgot_panel():
    return gr.update(visible=False), gr.update(visible=False), gr.update(visible=True)

def hide_all_panels():
    return gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)


# ── Example queries ────────────────────────────────────────────────────────────

EXAMPLES = [
    "What national rail trains run from Central (NR01) to Stonehaven (NR05)?",
    "What is the fastest metro route from MS01 to MS14?",
    "How do I get from Central Square (MS01) to Stonehaven (NR05)?",
    "If Old Town station (NR03) is closed, what alternative routes exist from NR01 to NR05?",
    "My train was delayed 45 minutes — what compensation am I entitled to?",
    "What is the company policy on travelling with a bicycle on national rail?",
]


# ── Build UI ───────────────────────────────────────────────────────────────────

with gr.Blocks(title="TransitFlow") as demo:

    # ── Hidden state ──────────────────────────────────────────────────
    agent_history_state = gr.State([])
    current_user_state  = gr.State(None)   # None = guest, email str = logged in

    # ── Header: title + auth buttons ─────────────────────────────────
    with gr.Row(equal_height=True):
        gr.Markdown("""
# 🚂 TransitFlow Intelligent Rail Assistant
*Powered by PostgreSQL · pgvector · Neo4j · LLM*
        """)
        with gr.Column(scale=0, min_width=240):
            with gr.Row():
                login_btn    = gr.Button("👤 Login",    size="sm", variant="secondary")
                register_btn = gr.Button("📝 Register", size="sm", variant="secondary")
            user_info_display = gr.Markdown("", visible=False)
            logout_btn = gr.Button("Logout", size="sm", variant="stop", visible=False)

    # ── Login panel (hidden by default) ──────────────────────────────
    with gr.Column(visible=False) as login_panel:
        gr.Markdown("### Login")
        login_email_in    = gr.Textbox(label="Email", placeholder="you@example.com")
        login_password_in = gr.Textbox(label="Password", type="password")
        login_error_msg   = gr.Markdown("", visible=False)
        with gr.Row():
            login_submit_btn = gr.Button("Login", variant="primary")
            forgot_link_btn  = gr.Button("Forgot password?", size="sm")
            login_cancel_btn = gr.Button("Cancel", size="sm")

    # ── Register panel (hidden by default) ───────────────────────────
    with gr.Column(visible=False) as register_panel:
        gr.Markdown("### Create an Account")
        with gr.Row():
            reg_first_name_in = gr.Textbox(label="First name")
            reg_surname_in    = gr.Textbox(label="Surname")
        reg_email_in    = gr.Textbox(label="Email", placeholder="you@example.com")
        reg_year_in     = gr.Textbox(label="Year of birth", placeholder="e.g. 1990")
        reg_password_in = gr.Textbox(label="Password", type="password")
        reg_question_in = gr.Dropdown(choices=SECRET_QUESTIONS, label="Security question")
        reg_answer_in   = gr.Textbox(label="Secret answer")
        reg_error_msg   = gr.Markdown("", visible=False)
        with gr.Row():
            reg_submit_btn = gr.Button("Register", variant="primary")
            reg_cancel_btn = gr.Button("Cancel", size="sm")

    # ── Forgot password panel (hidden by default) ─────────────────────
    with gr.Column(visible=False) as forgot_panel:
        gr.Markdown("### Reset Your Password")
        forgot_email_in          = gr.Textbox(label="Email address", placeholder="you@example.com")
        forgot_check_btn         = gr.Button("Find my question", variant="secondary")
        forgot_question_display  = gr.Markdown("", visible=False)
        forgot_answer_in         = gr.Textbox(label="Your answer", visible=False)
        forgot_new_password_in   = gr.Textbox(label="New password", type="password", visible=False)
        forgot_reset_btn         = gr.Button("Reset password", variant="primary", visible=False)
        forgot_msg               = gr.Markdown("")
        forgot_back_btn          = gr.Button("Back to login", size="sm")

    # ── Main chat area ────────────────────────────────────────────────
    with gr.Row():

        # ── Left: chat ────────────────────────────────────────────────
        with gr.Column(scale=3):
            chatbot = gr.Chatbot(label="TransitFlow Assistant", height=420)

            with gr.Row():
                msg = gr.Textbox(
                    placeholder="Ask e.g. 'Are there seats from London to Bristol?'",
                    show_label=False,
                    scale=4,
                )
                send_btn = gr.Button("Send", variant="primary", scale=1)

            with gr.Row():
                clear_btn    = gr.Button("🗑️ Clear conversation", size="sm")
                debug_toggle = gr.Checkbox(label="🔍 Show database debug panel", value=True)

            # Debug panel — hidden until checkbox is ticked and a message is sent
            debug_panel = gr.Markdown(
                value="",
                visible=False,
            )

        # ── Right: sidebar ────────────────────────────────────────────
        with gr.Column(scale=1):

            gr.Markdown("### 🤖 LLM Provider")
            chat_model_dropdown = gr.Dropdown(
                choices=get_chat_model_choices(),
                value=get_initial_chat_model_value(),
                label="Chat model",
                info="Local Ollama models run fully locally. Gemini uses your API key.",
            )
            provider_status = gr.Markdown(value="**Active:** llama3.2:1b")
            ollama_status   = gr.Markdown(value=get_ollama_status())

            gr.Markdown("---")

            gr.Markdown("### � Analytics Dashboard")
            analytics_start_date = gr.Textbox(label="Start date", placeholder="YYYY-MM-DD")
            analytics_end_date = gr.Textbox(label="End date", placeholder="YYYY-MM-DD")
            analytics_button = gr.Button("Refresh booking analytics", variant="primary", size="sm")
            analytics_output = gr.Markdown(value="No analytics data loaded yet.")

            gr.Markdown("---")
            gr.Markdown("### 🛫 Trip History")
            trip_history_button = gr.Button("Load my trip history", variant="primary", size="sm")
            trip_history_output = gr.Markdown(value="Log in to view your trips.")

            gr.Markdown("---")
            gr.Markdown("### 🗺️ Route Visualizer")
            route_origin = gr.Textbox(label="Origin Station ID", placeholder="e.g., NR01 or MS01")
            route_destination = gr.Textbox(label="Destination Station ID", placeholder="e.g., NR05 or MS10")
            route_type_dropdown = gr.Dropdown(
                choices=["national_rail", "metro"],
                value="national_rail",
                label="Route Type",
            )
            route_visualize_button = gr.Button("Visualize Route", variant="primary", size="sm")
            route_output = gr.Markdown(value="Enter stations and select route type to visualize.")

            gr.Markdown("---")
            gr.Markdown("### �💡 Try these examples")
            for example in EXAMPLES:
                gr.Button(example, size="sm").click(
                    fn=lambda e=example: e,
                    outputs=msg,
                )

    # ── Event wiring ──────────────────────────────────────────────────

    chat_model_dropdown.change(
        fn=on_chat_model_change,
        inputs=chat_model_dropdown,
        outputs=[provider_status, ollama_status],
    )

    send_btn.click(
        fn=chat,
        inputs=[msg, chatbot, agent_history_state, debug_toggle, current_user_state],
        outputs=[chatbot, agent_history_state, debug_panel],
    ).then(fn=lambda: "", outputs=msg)

    msg.submit(
        fn=chat,
        inputs=[msg, chatbot, agent_history_state, debug_toggle, current_user_state],
        outputs=[chatbot, agent_history_state, debug_panel],
    ).then(fn=lambda: "", outputs=msg)

    analytics_button.click(
        fn=get_booking_analytics,
        inputs=[analytics_start_date, analytics_end_date],
        outputs=[analytics_output],
    )

    trip_history_button.click(
        fn=render_trip_history,
        inputs=[current_user_state],
        outputs=[trip_history_output],
    )

    route_visualize_button.click(
        fn=render_route_visualization,
        inputs=[route_origin, route_destination, route_type_dropdown],
        outputs=[route_output],
    )

    clear_btn.click(
        fn=clear_conversation,
        outputs=[chatbot, agent_history_state, debug_panel],
    )

    # Panel toggle buttons
    login_btn.click(
        fn=show_login_panel,
        outputs=[login_panel, register_panel, forgot_panel],
    )
    register_btn.click(
        fn=show_register_panel,
        outputs=[login_panel, register_panel, forgot_panel],
    )
    login_cancel_btn.click(
        fn=hide_all_panels,
        outputs=[login_panel, register_panel, forgot_panel],
    )
    reg_cancel_btn.click(
        fn=hide_all_panels,
        outputs=[login_panel, register_panel, forgot_panel],
    )
    forgot_link_btn.click(
        fn=show_forgot_panel,
        outputs=[login_panel, register_panel, forgot_panel],
    )
    forgot_back_btn.click(
        fn=show_login_panel,
        outputs=[login_panel, register_panel, forgot_panel],
    )

    # Login
    login_submit_btn.click(
        fn=do_login,
        inputs=[login_email_in, login_password_in],
        outputs=[
            login_error_msg,
            current_user_state,
            login_btn,
            register_btn,
            user_info_display,
            logout_btn,
            login_panel,
        ],
    )

    # Logout
    logout_btn.click(
        fn=do_logout,
        outputs=[
            current_user_state,
            login_btn,
            register_btn,
            user_info_display,
            logout_btn,
            login_panel,
            register_panel,
            forgot_panel,
        ],
    )

    # Register
    reg_submit_btn.click(
        fn=do_register,
        inputs=[
            reg_email_in, reg_first_name_in, reg_surname_in,
            reg_year_in, reg_password_in, reg_question_in, reg_answer_in,
        ],
        outputs=[
            reg_error_msg,
            current_user_state,
            login_btn,
            register_btn,
            user_info_display,
            logout_btn,
            register_panel,
        ],
    )

    # Forgot password — step 1: find question
    forgot_check_btn.click(
        fn=forgot_find_question,
        inputs=[forgot_email_in],
        outputs=[
            forgot_msg,
            forgot_question_display,
            forgot_answer_in,
            forgot_new_password_in,
            forgot_reset_btn,
        ],
    )

    # Forgot password — step 2: reset
    forgot_reset_btn.click(
        fn=forgot_reset_password,
        inputs=[forgot_email_in, forgot_answer_in, forgot_new_password_in],
        outputs=[forgot_msg],
    )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
        theme=gr.themes.Soft(),
    )
