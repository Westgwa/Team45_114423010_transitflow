from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

from skeleton.llm_provider import llm
from skeleton.notifications import notifications
from databases.relational.queries import (
    query_national_rail_availability,
    query_national_rail_fare,
    query_metro_schedules,
    query_metro_fare,
    query_available_seats,
    query_user_profile,
    query_user_bookings,
    execute_booking,
    execute_cancellation,
    query_policy_vector_search,
)
from databases.graph.queries import (
    query_shortest_route,
    query_cheapest_route,
    query_alternative_routes,
    query_interchange_path,
    query_delay_ripple,
)


_STATION_INDEX: dict[str, str] = {
    "central square": "MS01", "riverside": "MS02", "northgate": "MS03",
    "elm park": "MS04", "westfield": "MS05", "harbour view": "MS06",
    "old town": "MS07", "university": "MS08", "queensbridge": "MS09",
    "parkside": "MS10", "greenhill": "MS11", "lakeshore": "MS12",
    "clifton": "MS13", "eastwick": "MS14", "ferndale": "MS15",
    "hilltop": "MS16", "broadmoor": "MS17", "sunnyvale": "MS18",
    "redwood": "MS19", "thornton": "MS20",

    "central station": "NR01", "maplewood": "NR02",
    "old town junction": "NR03", "ashford": "NR04",
    "stonehaven": "NR05", "bridgeport": "NR06",
    "ferndale halt": "NR07", "coalport": "NR08",
    "dunmore": "NR09", "langford end": "NR10",
}


def _inject_station_ids(text: str) -> str:
    result = text
    seen_ids: set[str] = set()

    for name in sorted(_STATION_INDEX, key=len, reverse=True):
        sid = _STATION_INDEX[name]

        if sid in seen_ids:
            continue

        pattern = re.compile(re.escape(name), re.IGNORECASE)

        if pattern.search(result):
            result = pattern.sub(f"{name} ({sid})", result)
            seen_ids.add(sid)

    return result


SYSTEM_PROMPT = """You are TransitFlow, a transit assistant for a dual-network system.

Networks: City Metro MS01-MS20 (lines M1-M4) | National Rail NR01-NR10 (lines NR1-NR2)
Interchanges: Central=MS01/NR01 | Old Town=MS07/NR03 | Ferndale=MS15/NR07
Today: {today}

LOGIN RULE: Routes, fares, schedules, and policies work WITHOUT login for all users. Only make_booking and cancel_booking need login — if the user tries to book or cancel and is not logged in, tell them to log in first.

When DATA FROM TRANSITFLOW DATABASE is provided, use it as the only source of truth. Do not contradict it or say a route was not found if the data shows one.
For route results: list every station name in order, note any line changes, and give the total travel time or cost.
Always reply in the same language as the user.
""".format(today=date.today().isoformat())


TOOLS = [
    {
        "name": "check_national_rail_availability",
        "description": (
            "Check available national rail trains and services between two stations. "
            "Use for trains, schedules, timetables, services, or availability."
        ),
        "parameters": {
            "origin_id": {"type": "string"},
            "destination_id": {"type": "string"},
            "travel_date": {"type": "string"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "get_national_rail_fare",
        "description": "Calculate national rail fare.",
        "parameters": {
            "schedule_id": {"type": "string"},
            "fare_class": {"type": "string"},
            "stops_travelled": {"type": "integer"},
        },
        "required": ["schedule_id", "fare_class", "stops_travelled"],
    },
    {
        "name": "check_metro_availability",
        "description": "Check metro services between two metro stations.",
        "parameters": {
            "origin_id": {"type": "string"},
            "destination_id": {"type": "string"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "calculate_metro_fare",
        "description": "Calculate metro fare.",
        "parameters": {
            "schedule_id": {"type": "string"},
            "stops_travelled": {"type": "integer"},
        },
        "required": ["schedule_id", "stops_travelled"],
    },
    {
        "name": "get_metro_fare",
        "description": (
            "Get metro ticket price. Use only for fare/price/cost/how much questions. "
            "Do not use this for route or direction questions."
        ),
        "parameters": {
            "origin_id": {"type": "string"},
            "destination_id": {"type": "string"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "get_user_bookings",
        "description": "Retrieve logged-in user's booking history.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "get_available_seats",
        "description": "Show available seats on a national rail service.",
        "parameters": {
            "schedule_id": {"type": "string"},
            "travel_date": {"type": "string"},
            "fare_class": {"type": "string"},
        },
        "required": ["schedule_id", "travel_date", "fare_class"],
    },
    {
        "name": "make_booking",
        "description": "Create a national rail booking. Requires login.",
        "parameters": {
            "schedule_id": {"type": "string"},
            "origin_station_id": {"type": "string"},
            "destination_station_id": {"type": "string"},
            "travel_date": {"type": "string"},
            "fare_class": {"type": "string"},
            "seat_id": {"type": "string"},
            "ticket_type": {"type": "string"},
        },
        "required": [
            "schedule_id",
            "origin_station_id",
            "destination_station_id",
            "travel_date",
            "fare_class",
            "seat_id",
        ],
    },
    {
        "name": "cancel_booking",
        "description": "Cancel a national rail booking. Requires login.",
        "parameters": {
            "booking_id": {"type": "string"},
        },
        "required": ["booking_id"],
    },
    {
        "name": "search_policy",
        "description": (
            "Search policy documents. Use for refunds, delay compensation, luggage, "
            "bicycles, pets, food, conduct, booking rules, ticket types, fare evasion, or child fares."
        ),
        "parameters": {
            "query": {"type": "string"},
        },
        "required": ["query"],
    },
    {
        "name": "find_route",
        "description": (
            "Find best route between stations. Use for directions, fastest route, quickest route, "
            "shortest path, cheapest route, lowest fare route, or cross-network journey."
        ),
        "parameters": {
            "origin_id": {"type": "string"},
            "destination_id": {"type": "string"},
            "network": {"type": "string"},
            "optimise_by": {"type": "string"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "find_alternative_routes",
        "description": "Find routes avoiding a delayed or closed station.",
        "parameters": {
            "origin_id": {"type": "string"},
            "destination_id": {"type": "string"},
            "avoid_station_id": {"type": "string"},
            "network": {"type": "string"},
        },
        "required": ["origin_id", "destination_id", "avoid_station_id"],
    },
    {
        "name": "get_delay_ripple",
        "description": "Show affected stations within N hops of a disruption.",
        "parameters": {
            "station_id": {"type": "string"},
            "hops": {"type": "integer"},
        },
        "required": ["station_id"],
    },
]


TOOLS_SCHEMA = """\
find_route(origin_id, destination_id, network?, optimise_by?)
check_national_rail_availability(origin_id, destination_id, travel_date?)
get_national_rail_fare(schedule_id, fare_class, stops_travelled)
check_metro_availability(origin_id, destination_id)
calculate_metro_fare(schedule_id, stops_travelled)
get_metro_fare(origin_id, destination_id)
get_available_seats(schedule_id, travel_date, fare_class)
make_booking(schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type?)
cancel_booking(booking_id)
get_user_bookings()
search_policy(query)
find_alternative_routes(origin_id, destination_id, avoid_station_id, network?)
get_delay_ripple(station_id, hops?)"""


def _execute_tool(
    tool_name: str,
    params: dict,
    current_user_email: Optional[str] = None,
) -> str:
    try:
        if tool_name == "check_national_rail_availability":
            result = query_national_rail_availability(**params)

        elif tool_name == "get_national_rail_fare":
            result = query_national_rail_fare(**params)

        elif tool_name == "check_metro_availability":
            result = query_metro_schedules(
                origin_id=params["origin_id"],
                destination_id=params["destination_id"],
            )

        elif tool_name == "calculate_metro_fare":
            result = query_metro_fare(**params)

        elif tool_name == "get_metro_fare":
            schedules = query_metro_schedules(
                origin_id=params["origin_id"],
                destination_id=params["destination_id"],
            )

            if not schedules:
                result = {"error": "No metro service found between these stations."}
            else:
                sched = schedules[0]
                # stops_travelled is computed by the junction-table query.
                n_stops = int(sched.get("stops_travelled") or 1)

                fare = query_metro_fare(sched["schedule_id"], n_stops)

                result = {
                    "origin": sched.get("origin_name", params["origin_id"]),
                    "destination": sched.get("destination_name", params["destination_id"]),
                    "line": sched.get("line"),
                    "schedule_id": sched["schedule_id"],
                    "stops": n_stops,
                    **(fare or {"error": "Fare lookup failed"}),
                }

        elif tool_name == "get_user_bookings":
            if not current_user_email:
                return json.dumps({"error": "No user is currently logged in."})
            result = query_user_bookings(current_user_email)

        elif tool_name == "get_available_seats":
            result = query_available_seats(**params)

        elif tool_name == "make_booking":
            if not current_user_email:
                return json.dumps({"error": "You must be logged in to make a booking."})

            profile = query_user_profile(current_user_email)

            if not profile:
                return json.dumps({"error": "User profile not found."})

            ok, data = execute_booking(
                user_id=profile["user_id"],
                schedule_id=params["schedule_id"],
                origin_station_id=params["origin_station_id"],
                destination_station_id=params["destination_station_id"],
                travel_date=params["travel_date"],
                fare_class=params["fare_class"],
                seat_id=params["seat_id"],
                ticket_type=params.get("ticket_type", "single"),
            )

            if ok:
                notifications.notify({
                    "type": "booking",
                    "message": (
                        f"🔔 Booking confirmed: {data.get('booking_id')} on {data.get('schedule_id')} "
                        f"for {data.get('travel_date')}."
                    ),
                    "booking_id": data.get("booking_id"),
                    "schedule_id": data.get("schedule_id"),
                    "travel_date": data.get("travel_date"),
                })

            result = data if ok else {"error": data}

        elif tool_name == "cancel_booking":
            if not current_user_email:
                return json.dumps({"error": "You must be logged in to cancel a booking."})

            profile = query_user_profile(current_user_email)

            if not profile:
                return json.dumps({"error": "User profile not found."})

            ok, data = execute_cancellation(
                booking_id=params["booking_id"],
                user_id=profile["user_id"],
            )

            if ok:
                booking = data.get("booking", {}) if isinstance(data, dict) else {}
                notifications.notify({
                    "type": "cancellation",
                    "message": (
                        f"⚠️ Booking cancelled: {booking.get('booking_id', params['booking_id'])}. "
                        f"Refund: ${data.get('refund_amount_usd', 0):.2f}."
                    ),
                    "booking_id": booking.get("booking_id", params["booking_id"]),
                    "refund_amount_usd": data.get("refund_amount_usd", 0),
                })

            result = data if ok else {"error": data}

        elif tool_name == "search_policy":
            embedding = llm.embed(params["query"])
            docs = query_policy_vector_search(embedding)

            result = [
                {
                    "title": d["title"],
                    "category": d["category"],
                    "content": d["content"][:800],
                    "similarity": round(d["similarity"], 3),
                }
                for d in docs
            ]

        elif tool_name == "find_route":
            origin_id = params["origin_id"]
            destination_id = params["destination_id"]
            network = params.get("network", "auto")
            optimise_by = params.get("optimise_by", "time")

            is_cross = (
                origin_id.upper().startswith("MS") and destination_id.upper().startswith("NR")
            ) or (
                origin_id.upper().startswith("NR") and destination_id.upper().startswith("MS")
            )

            if is_cross:
                result = query_interchange_path(origin_id, destination_id)
            elif optimise_by == "cost":
                result = query_cheapest_route(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    network=network,
                )
            else:
                result = query_shortest_route(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    network=network,
                )

        elif tool_name == "find_alternative_routes":
            routes = query_alternative_routes(
                origin_id=params["origin_id"],
                destination_id=params["destination_id"],
                avoid_station_id=params["avoid_station_id"],
                network=params.get("network", "auto"),
            )

            result = [{"route_number": i + 1, "legs": r} for i, r in enumerate(routes)]

        elif tool_name == "get_delay_ripple":
            result = query_delay_ripple(
                delayed_station_id=params["station_id"],
                hops=params.get("hops", 2),
            )

        else:
            result = {"error": f"Unknown tool: {tool_name}"}

        return json.dumps(result, default=str)

    except Exception as e:
        return json.dumps({"error": str(e)})


def _flatten_to_text(obj, depth: int = 0) -> str:
    pad = "  " * depth

    if isinstance(obj, dict):
        if not obj:
            return f"{pad}(empty)"

        lines = []

        for k, v in obj.items():
            if v is None:
                continue

            if isinstance(v, (dict, list)):
                inner = _flatten_to_text(v, depth + 1)
                if inner.strip():
                    lines.append(f"{pad}{k}:\n{inner}")
            else:
                lines.append(f"{pad}{k}: {v}")

        return "\n".join(lines) or f"{pad}(empty)"

    if isinstance(obj, list):
        if not obj:
            return f"{pad}(no records)"

        parts = []

        for i, item in enumerate(obj, 1):
            if isinstance(item, (dict, list)):
                parts.append(f"{pad}[{i}]")
                parts.append(_flatten_to_text(item, depth + 1))
            else:
                parts.append(f"{pad}- {item}")

        return "\n".join(parts)

    return f"{pad}{obj}"


def _normalise_result(tool_name: str, result_json: str) -> str:
    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        return result_json

    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"

    return _flatten_to_text(data)


def _summarise_result(tool_name: str, result_json: str) -> str:
    return result_json


def _parse_tool_calls(llm_response: str) -> list[dict] | None:
    text = llm_response.strip()

    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    decoder = json.JSONDecoder()

    for m in re.finditer(r"\{", text):
        try:
            data, _ = decoder.raw_decode(text, m.start())
            if "tool_calls" in data:
                return data["tool_calls"]
        except (json.JSONDecodeError, KeyError, ValueError):
            continue

    return None


def run_agent(
    user_message: str,
    history: list[dict],
    debug: bool = False,
    current_user_email: Optional[str] = None,
) -> tuple:
    """
    Main agent loop.

    Args:
        user_message:       The user's latest message
        history:            Conversation history
        debug:              If True, also return internal tool call info
        current_user_email: Email of the logged-in user, or None for guests

    Returns:
        (assistant_reply, updated_history) or (assistant_reply, updated_history, debug_info)
    """
    debug_info = []

    # Build a context-aware system prompt based on login state
    if current_user_email:
        profile = query_user_profile(current_user_email)
        if profile:
            user_display = f"{profile['full_name']} (email: {current_user_email}, user_id: {profile['user_id']})"
        else:
            user_display = current_user_email

        contextual_prompt = SYSTEM_PROMPT + (
            f"\n\nLogged-in user: {user_display}. "
            "Answer personal booking queries for this user without asking for their email or ID. "
            "Use get_user_bookings() for any booking history request. "
            "Use make_booking / cancel_booking for booking and cancellation requests."
        )
    else:
        contextual_prompt = SYSTEM_PROMPT + (
            "\n\nNo user is currently logged in. "
            "If the user asks about personal bookings, history, or wants to make/cancel a booking, "
            "tell them they must log in first."
        )

    recent_history = history[-4:] if len(history) > 4 else history
    _augmented_message = _inject_station_ids(user_message)

    tool_selection_prompt = f"""Output only this JSON:
{{"tool_calls": [{{"name": "TOOL", "params": {{"KEY": "VALUE"}}}}]}}
Or if no tool needed:
{{"tool_calls": []}}

STATIONS: Metro=MS01-MS20, Rail=NR01-NR10
USER: {current_user_email or "not logged in"}

Rules:
- Route/path/direction/fastest/cheapest/how-to-get questions: use find_route.
- Alternative route / closed station / avoid station questions: use find_alternative_routes.
- Policy/refund/compensation/luggage/bicycle questions: use search_policy.
- Booking/cancellation requires login.
- Never use empty string params.

TOOLS:
{TOOLS_SCHEMA}

HISTORY:
{json.dumps(recent_history, indent=None)}

USER: "{_augmented_message}"

Examples:
"fastest route MS01 to MS14" -> {{"tool_calls": [{{"name": "find_route", "params": {{"origin_id": "MS01", "destination_id": "MS14", "network": "metro", "optimise_by": "time"}}}}]}}
"cheapest route MS01 to MS14" -> {{"tool_calls": [{{"name": "find_route", "params": {{"origin_id": "MS01", "destination_id": "MS14", "network": "metro", "optimise_by": "cost"}}}}]}}
"trains NR01 to NR05" -> {{"tool_calls": [{{"name": "check_national_rail_availability", "params": {{"origin_id": "NR01", "destination_id": "NR05"}}}}]}}
"If NR03 is closed, what alternative routes exist from NR01 to NR05?" -> {{"tool_calls": [{{"name": "find_alternative_routes", "params": {{"origin_id": "NR01", "destination_id": "NR05", "avoid_station_id": "NR03", "network": "rail"}}}}]}}
"refund policy" -> {{"tool_calls": [{{"name": "search_policy", "params": {{"query": "refund policy"}}}}]}}

JSON:"""

    # Step 1: LLM tool selection
    if llm.get_chat_provider() == "ollama":
        tool_calls = llm.ollama_tool_call(
            recent_history,
            TOOLS,
            _augmented_message,
            system_prompt=(
                "You are a tool router. "
                "Alternative route / closed station / avoid station questions must use find_alternative_routes. "
                "Route/directions/fastest/quickest/cheapest/path/how-to-get questions must use find_route. "
                "Cheapest/lowest fare/lowest cost route questions must set optimise_by='cost'. "
                "Fastest/quickest/shortest route questions must set optimise_by='time'. "
                "Metro fare/price/how much questions use get_metro_fare only when the user asks price, not route. "
                "National rail train/service/schedule questions must use check_national_rail_availability. "
                "Policy/rules/compensation/luggage/bicycle questions use search_policy. "
                "Output only tool calls."
            ),
        )

        if debug:
            debug_info.append(f"**Tool selection (native):** {tool_calls}")

    else:
        selection_response = llm.chat(
            messages=[{"role": "user", "content": tool_selection_prompt}],
            system_prompt="JSON only. Output valid JSON. No empty string param values.",
        )
        tool_calls = _parse_tool_calls(selection_response) or []

        if debug:
            debug_info.append(f"**Tool selection:** {selection_response}")

    # Step 1.5: deterministic fallback / correction
    _lower = _augmented_message.lower()

    def _extract_unique_station_ids(text: str) -> list[str]:
        raw_ids = re.findall(
            r"\b(MS\d{2}|NR\d{2})\b",
            text,
            re.IGNORECASE,
        )

        result = []
        for sid in raw_ids:
            sid = sid.upper()
            if sid not in result:
                result.append(sid)

        return result

    # Prefer explicit IDs typed by the user.
    # Example: Central (NR01) -> NR01
    _explicit_station_ids = _extract_unique_station_ids(user_message)

    # If user did not type IDs, use injected station names.
    _station_ids = _explicit_station_ids or _extract_unique_station_ids(_augmented_message)
    _two_stations = len(_station_ids) >= 2

    def _fallback(name: str, params: dict, reason: str):
        nonlocal tool_calls
        tool_calls = [{"name": name, "params": params}]
        if debug:
            debug_info.append(f"**Fallback:** {reason} → {name}({params})")

    # -1. Policy / refund / compensation / rules
    _policy_triggers = {
        "policy",
        "policies",
        "refund",
        "compensation",
        "delay compensation",
        "delayed",
        "delay",
        "entitled",
        "entitlement",
        "bicycle",
        "bike",
        "luggage",
        "pet",
        "pets",
        "rules",
        "ticket type",
        "fare evasion",
        "child fare",
    }

    _is_policy = any(kw in _lower for kw in _policy_triggers)

    if _is_policy:
        _fallback(
            "search_policy",
            {
                "query": user_message,
            },
            "forced policy query",
        )

    # 0. Alternative route / station closure / avoid station
    _alternative_triggers = {
        "alternative route",
        "alternative routes",
        "avoid",
        "closed",
        "closure",
        "disrupted",
        "disruption",
        "blocked",
        "unavailable",
    }

    _is_alternative = any(kw in _lower for kw in _alternative_triggers)

    if not _is_policy and _is_alternative:
        # Example:
        # If Old Town station (NR03) is closed,
        # what alternative routes exist from NR01 to NR05?
        # -> ["NR03", "NR01", "NR05"]
        ids = _explicit_station_ids or _station_ids

        if len(ids) >= 3:
            avoid_station_id = ids[0]
            origin_id = ids[1]
            destination_id = ids[2]

            if origin_id.startswith("NR") and destination_id.startswith("NR"):
                network = "rail"
            elif origin_id.startswith("MS") and destination_id.startswith("MS"):
                network = "metro"
            else:
                network = "auto"

            _fallback(
                "find_alternative_routes",
                {
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "avoid_station_id": avoid_station_id,
                    "network": network,
                },
                "forced alternative route query",
            )

    # 1. Route / direction / fastest / cheapest / cross-network
    _route_triggers = {
        "fastest route",
        "quickest route",
        "shortest route",
        "cheapest route",
        "lowest cost route",
        "least expensive route",
        "best route",
        "how do i get",
        "how to get",
        "directions from",
        "route from",
        "route to",
        "get from",
        "travel from",
        "way from",
        "path from",
        "fastest",
        "quickest",
        "cheapest",
    }

    _is_route = (
        any(kw in _lower for kw in _route_triggers)
        or (_two_stations and "route" in _lower)
    )

    if not _is_policy and not _is_alternative and _is_route and _two_stations:
        opt = (
            "cost"
            if any(
                kw in _lower
                for kw in [
                    "cheap",
                    "cheapest",
                    "lowest cost",
                    "least expensive",
                    "lowest fare",
                    "cost",
                    "fare",
                    "price",
                ]
            )
            else "time"
        )

        origin_id = _station_ids[0].upper()
        destination_id = _station_ids[1].upper()

        if origin_id.startswith("MS") and destination_id.startswith("MS"):
            network = "metro"
        elif origin_id.startswith("NR") and destination_id.startswith("NR"):
            network = "rail"
        else:
            network = "auto"

        _fallback(
            "find_route",
            {
                "origin_id": origin_id,
                "destination_id": destination_id,
                "network": network,
                "optimise_by": opt,
            },
            "forced route query",
        )

    # 2. Train / schedule / service / availability
    _avail_triggers = {
        "train",
        "trains",
        "service",
        "services",
        "run from",
        "runs from",
        "schedule",
        "timetable",
        "available",
        "availability",
    }

    _is_availability = any(kw in _lower for kw in _avail_triggers)

    if not _is_policy and not _is_alternative and not _is_route and _is_availability and _two_stations:
        origin_id = _station_ids[0].upper()
        destination_id = _station_ids[1].upper()

        travel_date = next(
            (
                w
                for w in _lower.split()
                if re.match(r"\d{4}-\d{2}-\d{2}", w)
            ),
            None,
        )

        params = {
            "origin_id": origin_id,
            "destination_id": destination_id,
        }

        if travel_date:
            params["travel_date"] = travel_date

        if origin_id.startswith("NR") and destination_id.startswith("NR"):
            tool = "check_national_rail_availability"
        elif origin_id.startswith("MS") and destination_id.startswith("MS"):
            tool = "check_metro_availability"
        else:
            tool = "find_route"
            params["network"] = "auto"
            params["optimise_by"] = "time"

        _fallback(tool, params, "forced availability query")

    # 3. Personal booking history — requires login
    if current_user_email and not tool_calls:
        _personal_triggers = {
            "my booking",
            "my ticket",
            "my trip",
            "my journey",
            "my history",
            "my reservation",
            "show booking",
            "view booking",
            "check booking",
            "list booking",
            "show my",
            "view my",
        }

        if any(kw in _lower for kw in _personal_triggers):
            _fallback("get_user_bookings", {}, "personal booking query")

    # 2. Force train / schedule / availability questions to the correct tool
    if not tool_calls:
        _avail_triggers = {
            "train",
            "trains",
            "service",
            "services",
            "run from",
            "runs from",
            "schedule",
            "timetable",
            "available",
            "availability",
        }

        _is_availability = any(kw in _lower for kw in _avail_triggers)

        if _is_availability and _two_stations:
            o, d = _station_ids[0].upper(), _station_ids[1].upper()

            _travel_date = next(
                (
                    w
                    for w in _lower.split()
                    if re.match(r"\d{4}-\d{2}-\d{2}", w)
                ),
                None,
            )

            _params = {
                "origin_id": o,
                "destination_id": d,
            }

            if _travel_date:
                _params["travel_date"] = _travel_date

            if o.startswith("NR") and d.startswith("NR"):
                _tool = "check_national_rail_availability"
            elif o.startswith("MS") and d.startswith("MS"):
                _tool = "check_metro_availability"
            else:
                _tool = "find_route"
                _params["network"] = "auto"
                _params["optimise_by"] = "time"

            _fallback(_tool, _params, "forced availability query")

    # 3. Personal booking history — requires login
    if current_user_email and not tool_calls:
        _personal_triggers = {
            "my booking",
            "my ticket",
            "my trip",
            "my journey",
            "my history",
            "my reservation",
            "show booking",
            "view booking",
            "check booking",
            "list booking",
            "show my",
            "view my",
        }

        if any(kw in _lower for kw in _personal_triggers):
            _fallback("get_user_bookings", {}, "personal booking query")

    # Step 2: Execute each tool call
    tool_results = []

    for call in tool_calls:
        tool_name = call.get("name", "")
        params = call.get("params") or call.get("parameters", {})

        if any(v == "" for v in params.values()):
            if debug:
                debug_info.append(f"**Skipped** `{tool_name}` — empty params: {params}")
            continue

        if debug:
            debug_info.append(f"**Calling:** `{tool_name}({params})`")

        result_json = _execute_tool(tool_name, params, current_user_email)
        summary = _summarise_result(tool_name, result_json)

        if debug:
            debug_info.append(
                f"**Result (raw):** ```json\n{result_json[:500]}\n```\n"
                f"**Summary sent to LLM:** {summary}"
            )

        tool_results.append(
            {
                "tool": tool_name,
                "params": params,
                "result": result_json,
                "summary": summary,
            }
        )

    # Step 3: Compose final answer
    _DB_KEYWORDS = {
        "booking",
        "ticket",
        "schedule",
        "fare",
        "route",
        "seat",
        "train",
        "metro",
        "journey",
        "trip",
        "history",
        "reservation",
    }

    if tool_results:
        data_block = "\n\n".join(
            f"[{tr['tool']}]\n{_normalise_result(tr['tool'], tr['result'])}"
            for tr in tool_results
        )

        if debug:
            debug_info.append(f"**Data (normalised):**\n{data_block}")

        content = (
            f"DATA FROM TRANSITFLOW DATABASE:\n{data_block}"
            f"\n\nUser asks: {user_message}"
            f"\n\nAnswer using only the data above:"
        )

    elif any(kw in user_message.lower() for kw in _DB_KEYWORDS):
        content = (
            f"User asks: {user_message}\n\n"
            "IMPORTANT: No data was retrieved from the TransitFlow database for this query. "
            "Do NOT invent bookings, fares, schedules, seat numbers, routes, or travel times. "
            "Tell the user no data was found."
        )

    else:
        content = user_message

    final_messages = history + [{"role": "user", "content": content}]
    answer = llm.chat(messages=final_messages, system_prompt=contextual_prompt)

    updated_history = history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": answer},
    ]

    if debug:
        return answer, updated_history, "\n\n".join(debug_info)

    return answer, updated_history