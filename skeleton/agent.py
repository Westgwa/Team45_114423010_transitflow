from __future__ import annotations

# TASK 6 EXTENSION: Emits real-time booking/cancellation notifications via the NotificationManager.

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
    # TASK 6 EXTENSION: booking-analytics query exposed as an agent tool so the
    # bonus feature is reachable end-to-end (UI chat -> agent -> tool -> DB ->
    # LLM -> UI), not only through the sidebar dashboard panel.
    query_booking_revenue_summary,
    query_trip_history,
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


# ─────────────────────────────────────────────────────────────────────────────
# Tool-call normalisation / repair layer
# Runs on whatever the LLM (native or prompt router) selected, BEFORE the
# deterministic fallback, so a single good native call is enough end-to-end and
# does not depend on the fallback. It fixes the recurring native-selection bugs:
#   * station names instead of station_ids   -> map to MSxx / NRxx
#   * missing / wrong `network`              -> infer from the resolved ids
#   * params filled with a JSON *schema*     -> rebuild real arguments
#   * search_policy missing its query        -> use the user's message
#   * general policy routed to get_user_bookings -> reroute to search_policy
# ─────────────────────────────────────────────────────────────────────────────

_STATION_ID_RE = re.compile(r"^(MS|NR)\d{2}$", re.IGNORECASE)

# Bare names that exist on BOTH networks; resolved using the tool's network hint.
_AMBIGUOUS_STATION_ALIASES: dict[str, dict[str, str]] = {
    "central": {"metro": "MS01", "rail": "NR01"},
    "old town": {"metro": "MS07", "rail": "NR03"},
    "ferndale": {"metro": "MS15", "rail": "NR07"},
}

_POLICY_KEYWORDS = (
    "policy", "policies", "refund", "compensation", "entitled", "entitlement",
    "delay", "delayed", "luggage", "bicycle", "bike", "pet", "pets", "rules",
    "ticket type", "fare evasion", "child fare", "conduct",
)

_PERSONAL_KEYWORDS = (
    "my booking", "my ticket", "my trip", "my journey", "my reservation",
    "my purchase", "booking history", "my history", "show my", "view my",
)

# Seat / availability / can-I-book intent — English + Chinese. Used by both the
# native router prompt and the deterministic fallback so a seat-availability
# question maps to check_*_availability instead of returning no tool.
_AVAILABILITY_KEYWORDS = (
    # English: trains / schedules / seats / booking availability
    "train", "trains", "service", "services", "run from", "runs from",
    "schedule", "timetable", "available", "availability",
    "seat", "seats", "available seat", "available seats", "seat availability",
    "any seats", "spare seat", "free seat", "booking availability",
    "can i book", "is it available", "is there a ticket", "any tickets",
    "tickets available", "ticket available",
    # Chinese: 座位 / 車位 / 有票 / 可訂位 / 預訂
    "座位", "車位", "有位", "有沒有位", "有沒有座位", "還有座位",
    "有票", "有沒有票", "還有票", "可訂位", "是否可訂位", "能訂位",
    "訂位", "可預訂", "是否可預訂", "預訂",
)

# Alternative-route / disruption intent (English + Chinese).
_ALTERNATIVE_KEYWORDS = (
    "alternative route", "alternative routes", "avoid", "closed", "closure",
    "disrupted", "disruption", "blocked", "unavailable", "detour",
    "繞道", "繞路", "避開", "改道",
)

# Route / path / how-to-get intent (English + Chinese).
_ROUTE_KEYWORDS = (
    "route", "path", "how do i get", "how to get", "directions", "fastest",
    "quickest", "shortest", "cheapest", "transfer", "interchange", "via",
    "怎麼去", "怎麼到", "如何到", "路線", "最快", "最便宜", "轉乘", "怎麼走",
)


def _resolve_station_id(value, network_hint: Optional[str] = None) -> Optional[str]:
    """Map a station reference (id OR name OR alias) to a canonical MSxx/NRxx id."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    if not text:
        return None
    if _STATION_ID_RE.match(text):
        return text.upper()

    # An id embedded anywhere in the text, e.g. "Stonehaven (NR05)" (which is what
    # _inject_station_ids produces) or "the NR05 platform" -> NR05.
    embedded = re.search(r"\b(MS|NR)\s?(\d{2})\b", text, re.IGNORECASE)
    if embedded:
        return f"{embedded.group(1).upper()}{embedded.group(2)}"

    key = text.lower()

    def _from_alias(name: str) -> Optional[str]:
        opts = _AMBIGUOUS_STATION_ALIASES[name]
        return opts.get(network_hint) or opts.get("rail") or next(iter(opts.values()))

    if key in _AMBIGUOUS_STATION_ALIASES:
        return _from_alias(key)
    if key in _STATION_INDEX:
        return _STATION_INDEX[key]

    # Strip generic descriptors ("Central Rail Station", "Ferndale Halt",
    # "National Rail Stonehaven", ...) and retry against the alias + name index.
    trimmed = re.sub(
        r"\b(national rail|national|rail|metro|underground|tube|city|line|"
        r"train|station|halt|junction|stop|the)\b",
        "",
        key,
    )
    trimmed = re.sub(r"\s+", " ", trimmed).strip()
    if trimmed and trimmed in _AMBIGUOUS_STATION_ALIASES:
        return _from_alias(trimmed)
    if trimmed and trimmed in _STATION_INDEX:
        return _STATION_INDEX[trimmed]

    # (8) Last resort: repair an id-shaped typo (e.g. "MR01" -> "NR01").
    return _correct_station_typo(text, network_hint)


def _infer_network(origin_id: Optional[str], destination_id: Optional[str]) -> str:
    """MS+MS -> 'metro', NR+NR -> 'rail', any MS/NR mix (cross-system) -> 'auto'."""
    o = (origin_id or "").upper()
    d = (destination_id or "").upper()
    if o.startswith("MS") and d.startswith("MS"):
        return "metro"
    if o.startswith("NR") and d.startswith("NR"):
        return "rail"
    return "auto"


def _infer_optimise_by(text: str) -> str:
    low = text.lower()
    cost_words = (
        "cheap", "cheapest", "lowest cost", "least expensive",
        "lowest fare", "fare", "price", "cost",
    )
    return "cost" if any(w in low for w in cost_words) else "time"


# (3) The tool only accepts optimise_by in {"time", "cost"}. The LLM frequently
# emits synonyms like "fastest" / "quickest" / "shortest_time"; map them so a
# native call no longer needs the fallback to repair the value.
_OPTIMISE_TIME_ALIASES = {
    "time", "fastest", "quickest", "shortest", "shortest_time",
    "shortest time", "soonest", "fast", "quick",
}
_OPTIMISE_COST_ALIASES = {
    "cost", "cheapest", "cheap", "lowest_cost", "lowest cost",
    "lowest_fare", "lowest fare", "least_expensive", "least expensive",
    "price", "fare",
}


def _normalise_optimise_by(value, text: str) -> str:
    """Map any optimise_by value/synonym to the tool's vocabulary ('time'|'cost').

    Falls back to inferring from the user's message when the value is missing or
    unrecognised, so optimise_by is always exactly 'time' or 'cost'.
    """
    if isinstance(value, str):
        v = re.sub(r"\s+", " ", value.strip().lower())
        if v in _OPTIMISE_TIME_ALIASES:
            return "time"
        if v in _OPTIMISE_COST_ALIASES:
            return "cost"
    return _infer_optimise_by(text)


# (8) Valid station ids: metro MS01-MS20, national rail NR01-NR10.
_VALID_STATION_PREFIXES = ("MS", "NR")
_VALID_STATION_IDS = (
    {f"MS{n:02d}" for n in range(1, 21)} | {f"NR{n:02d}" for n in range(1, 11)}
)


def _correct_station_typo(value, network_hint: Optional[str] = None) -> Optional[str]:
    """Fix an id-shaped reference one edit away from a valid prefix (e.g. 'MR01'
    -> 'NR01'). Returns None when the text is not id-shaped or cannot be repaired
    to an existing station, so we never call a tool with a non-existent id.
    """
    if value is None:
        return None
    m = re.match(r"^([A-Za-z]{2})\s?(\d{1,2})$", str(value).strip())
    if not m:
        return None
    prefix = m.group(1).upper()
    num = f"{int(m.group(2)):02d}"

    candidate = f"{prefix}{num}"
    if candidate in _VALID_STATION_IDS:
        return candidate

    # Prefixes exactly one character different from what the LLM produced.
    near = [
        p for p in _VALID_STATION_PREFIXES
        if sum(a != b for a, b in zip(prefix, p)) == 1
    ]
    if len(near) > 1:
        # Tie-break: honour the network hint, else keep the matching 2nd letter
        # ('MR' shares 'R' with 'NR' -> NR), else first valid candidate.
        if network_hint == "metro" and "MS" in near:
            near = ["MS"]
        elif network_hint == "rail" and "NR" in near:
            near = ["NR"]
        else:
            keep_second = [p for p in near if p[1] == prefix[1]]
            near = keep_second or near

    for p in near:
        fixed = f"{p}{num}"
        if fixed in _VALID_STATION_IDS:
            return fixed
    return None


def _params_look_like_schema(params) -> bool:
    """True when the LLM put a JSON *schema* into params instead of arguments."""
    if not isinstance(params, dict):
        return False
    if params.get("type") == "object":
        return True
    return bool({"properties", "required"} & set(params.keys()))


def _station_ids_from_text(text: str) -> list[str]:
    """Best-effort ordered station ids in a message: explicit ids first, else names."""
    ids: list[str] = []
    for m in re.findall(r"\b(MS\d{2}|NR\d{2})\b", text, re.IGNORECASE):
        u = m.upper()
        if u not in ids:
            ids.append(u)
    if ids:
        return ids

    padded = " " + re.sub(r"\s+", " ", text.lower()) + " "
    for name in sorted(_STATION_INDEX, key=len, reverse=True):
        if f" {name} " in padded or f" {name}(" in padded or f"{name}," in padded:
            sid = _STATION_INDEX[name]
            if sid not in ids:
                ids.append(sid)
    return ids


def _pick_primary_tool_call(calls: list[dict], user_message: str) -> list[dict]:
    """Collapse a multi-tool native selection down to the single best tool.

    Native selection sometimes returns several calls at once (e.g. an
    availability question yielding BOTH check_national_rail_availability and a
    stray find_route, or the same tool twice). We:
      1. drop exact duplicates (same name + same params), then
      2. keep only the one call matching the question's dominant intent, so a
         seat-availability question runs check_*_availability alone.
    """
    if len(calls) <= 1:
        return calls

    # 1) de-duplicate identical calls.
    deduped: list[dict] = []
    seen: set = set()
    for c in calls:
        key = (c.get("name"), json.dumps(c.get("params", {}), sort_keys=True, default=str))
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    if len(deduped) == 1:
        return deduped

    # 2) pick the call that matches the dominant intent (most specific first).
    low = user_message.lower()
    if any(k in low for k in _POLICY_KEYWORDS):
        preferred = ("search_policy",)
    elif any(k in low for k in _ALTERNATIVE_KEYWORDS):
        preferred = ("find_alternative_routes",)
    elif any(k in low for k in _AVAILABILITY_KEYWORDS):
        preferred = ("check_national_rail_availability", "check_metro_availability")
    elif any(k in low for k in _ROUTE_KEYWORDS):
        preferred = ("find_route",)
    else:
        preferred = ()

    candidates = [c for c in deduped if c.get("name") in preferred]
    if candidates:
        # Tie-break availability metro vs rail by the resolved origin id.
        if len(candidates) > 1:
            for c in candidates:
                oid = str(c.get("params", {}).get("origin_id", "")).upper()
                if c["name"] == "check_metro_availability" and oid.startswith("MS"):
                    return [c]
                if c["name"] == "check_national_rail_availability" and oid.startswith("NR"):
                    return [c]
        return [candidates[0]]

    # 3) no clear intent match -> keep just the first call (never run several).
    return [deduped[0]]


def _normalize_tool_calls(tool_calls: list[dict] | None, user_message: str) -> list[dict]:
    """Repair LLM-selected tool calls so they carry valid, complete arguments."""
    if not tool_calls:
        return tool_calls or []

    msg_ids = _station_ids_from_text(user_message)
    low = user_message.lower()
    is_policy = any(k in low for k in _POLICY_KEYWORDS)
    is_personal = any(k in low for k in _PERSONAL_KEYWORDS)

    repaired: list[dict] = []
    for call in tool_calls:
        name = call.get("name", "")
        params = call.get("params")
        if params is None:
            params = call.get("parameters")
        if not isinstance(params, dict):
            params = {}

        # (6) params accidentally filled with a JSON schema -> drop, rebuild below.
        if _params_look_like_schema(params):
            params = {}

        # (5) A clear policy / refund / compensation question wrongly routed to a
        # data tool (e.g. check_national_rail_availability for "I was delayed —
        # what am I entitled to?") -> reroute to search_policy at the native
        # stage, so the fallback no longer has to rescue it.
        if (
            name in (
                "check_national_rail_availability", "check_metro_availability",
                "find_route", "find_alternative_routes",
                "get_available_seats", "get_national_rail_fare",
            )
            and is_policy
            and not is_personal
            and len(msg_ids) < 2
        ):
            name = "search_policy"
            params = {"query": user_message}

        # (4) Never carry an empty / blank travel_date; drop it so the tool's
        # own default (or None) applies instead of an empty string -> null.
        if "travel_date" in params:
            td = params.get("travel_date")
            if not isinstance(td, str) or not td.strip():
                params.pop("travel_date", None)

        # Network hint from the tool itself (used to disambiguate names like "Central").
        if name in ("check_national_rail_availability", "get_national_rail_fare"):
            hint = "rail"
        elif name in ("check_metro_availability", "get_metro_fare", "calculate_metro_fare"):
            hint = "metro"
        else:
            hint = None

        # (1) Resolve every station-bearing field from name/alias to MSxx/NRxx.
        for field in (
            "origin_id", "destination_id", "avoid_station_id", "station_id",
            "origin_station_id", "destination_station_id",
        ):
            if params.get(field) not in (None, ""):
                resolved = _resolve_station_id(params[field], hint)
                if resolved:
                    params[field] = resolved

        # (2) Recover missing origin/destination from the message for path tools.
        if name in (
            "find_route", "find_alternative_routes",
            "check_national_rail_availability", "check_metro_availability",
        ):
            if not params.get("origin_id") and len(msg_ids) >= 1:
                params["origin_id"] = msg_ids[0]
            if not params.get("destination_id") and len(msg_ids) >= 2:
                params["destination_id"] = msg_ids[1]

        # (7) A general policy question must not call get_user_bookings.
        if name == "get_user_bookings" and is_policy and not is_personal:
            name = "search_policy"
            params = {"query": user_message}

        # (6) search_policy always carries the real question as `query`.
        if name == "search_policy":
            q = params.get("query")
            if not isinstance(q, str) or not q.strip():
                params = {"query": user_message}

        # (3)(4) network is derived from the resolved ids, so a cross-system pair
        # becomes "auto" even if the LLM said "metro".
        if name == "find_route":
            o, d = params.get("origin_id"), params.get("destination_id")
            if o and d:
                params["network"] = _infer_network(o, d)
            else:
                params.setdefault("network", "auto")
            # (3) normalise fastest/quickest/shortest_time -> time, etc.
            params["optimise_by"] = _normalise_optimise_by(
                params.get("optimise_by"), user_message
            )

        if name == "find_alternative_routes":
            o, d = params.get("origin_id"), params.get("destination_id")
            if o and d:
                params["network"] = _infer_network(o, d)

        repaired.append({"name": name, "params": params})

    # Collapse a multi-tool native selection to the single most appropriate tool
    # (e.g. an availability question must not also run a stray find_route).
    return _pick_primary_tool_call(repaired, user_message)


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
    {
        # TASK 6 EXTENSION: booking-revenue analytics, reachable from the chat.
        "name": "get_booking_analytics",
        "description": (
            "Get booking revenue analytics: total / active / cancelled booking counts, "
            "total revenue and total refunds, optionally limited to a travel-date range. "
            "Use for revenue, sales, income, earnings, analytics, how many bookings, "
            "or booking-statistics questions. Dates are optional (YYYY-MM-DD)."
        ),
        "parameters": {
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
        },
        "required": [],
    },
    {
        # TASK 6 EXTENSION: detailed personal trip history, reachable from the chat.
        "name": "get_trip_history",
        "description": (
            "Retrieve the logged-in user's detailed national-rail trip history "
            "(booking id, route, date, fare class, price, status). Requires login. "
            "Use for 'my trips', 'my trip history', 'my past journeys' questions."
        ),
        "parameters": {},
        "required": [],
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
get_delay_ripple(station_id, hops?)
get_booking_analytics(start_date?, end_date?)
get_trip_history()"""


# ─────────────────────────────────────────────────────────────────────────────
# Validation: decides whether the normalised native tool call can run as-is.
# This is the single gate that keeps a correct native selection from ever being
# overwritten by the deterministic fallback. (ok=True -> execute native, skip
# fallback; ok=False -> the reason names the first problem and the fallback then
# rebuilds the call dynamically from the user's question.)
# ─────────────────────────────────────────────────────────────────────────────
_TOOL_NAMES = {t["name"] for t in TOOLS}
_REQUIRED_BY_TOOL = {t["name"]: list(t.get("required", [])) for t in TOOLS}
_STATION_FIELDS = (
    "origin_id", "destination_id", "avoid_station_id", "station_id",
    "origin_station_id", "destination_station_id",
)


def _validate_tool_call(calls: list[dict]) -> tuple[bool, str]:
    """Return (ok, reason) for the normalised native selection.

    Fails (-> fallback) on exactly the cases the fallback exists for:
    empty selection, >1 tool, unknown tool, schema-shaped params, empty-string
    params, missing required params, a station field that is still a name or an
    invalid id, or a find_route with a wrong network / unsupported optimise_by.
    """
    if not calls:
        return False, "native selection returned no tool ([])"
    if len(calls) > 1:
        return False, f"native selected {len(calls)} tools (expected exactly 1)"

    call = calls[0]
    name = call.get("name")
    if name not in _TOOL_NAMES:
        return False, f"unknown / wrong tool {name!r}"

    params = call.get("params")
    if not isinstance(params, dict):
        return False, "params is not an object"
    if _params_look_like_schema(params):
        return False, "params look like a JSON schema, not arguments"

    for k, v in params.items():
        if isinstance(v, str) and v.strip() == "":
            return False, f"empty-string param {k!r}"

    missing = [
        r for r in _REQUIRED_BY_TOOL.get(name, [])
        if not str(params.get(r, "")).strip()
    ]
    if missing:
        return False, f"missing required params: {missing}"

    # Every station-bearing field must be a canonical MSxx/NRxx id — not a name
    # like "Central Station", not an invalid id like "MR01".
    for f in _STATION_FIELDS:
        if f in params and str(params[f]).strip():
            if not _STATION_ID_RE.match(str(params[f])):
                return False, f"{f}={params[f]!r} is not a valid station id"

    # Route network / optimise_by must be self-consistent and supported.
    if name == "find_route":
        o, d = params.get("origin_id"), params.get("destination_id")
        if o and d and params.get("network") != _infer_network(o, d):
            return False, f"network={params.get('network')!r} does not match the station ids"
        if params.get("optimise_by") not in ("time", "cost"):
            return False, f"optimise_by={params.get('optimise_by')!r} is not supported"
    if name == "find_alternative_routes":
        o, d = params.get("origin_id"), params.get("destination_id")
        if o and d and params.get("network") not in (None, _infer_network(o, d)):
            return False, f"network={params.get('network')!r} does not match the station ids"

    return True, "native call is valid"


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

            # Pass the full route dicts through (path, legs, total_time_min, and the
            # separated avoid_station_ids / related_avoid_station_ids) instead of
            # nesting the whole dict under a mislabelled "legs" key.
            result = [{**r, "route_number": i + 1} for i, r in enumerate(routes)]

        elif tool_name == "get_delay_ripple":
            result = query_delay_ripple(
                delayed_station_id=params["station_id"],
                hops=params.get("hops", 2),
            )

        elif tool_name == "get_booking_analytics":
            # TASK 6 EXTENSION: end-to-end path for the analytics bonus —
            # the LLM-selected tool drives the same rollup-view query that backs
            # the dashboard, so the chat can answer revenue/cancellation questions.
            result = query_booking_revenue_summary(
                start_date=params.get("start_date") or None,
                end_date=params.get("end_date") or None,
            )

        elif tool_name == "get_trip_history":
            # TASK 6 EXTENSION: detailed trip history through the agent (login required).
            if not current_user_email:
                return json.dumps({"error": "You must be logged in to view your trip history."})
            result = query_trip_history(current_user_email, limit=20)

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
- Select EXACTLY ONE tool — the single best match for the question. Never return
  more than one tool call, and never repeat the same tool. A seat-availability
  question selects ONLY check_*_availability, not also find_route.
- params are the ACTUAL arguments, never a JSON schema (no type/properties/required keys).
- Stations must be ids (MSxx / NRxx). Convert names to ids: Central Station->NR01,
  Central Square->MS01, Stonehaven->NR05, Old Town->MS07, Sunnyvale->MS18.
- network: both MSxx -> "metro"; both NRxx -> "rail"; MSxx+NRxx mix (cross-system) -> "auto".
- find_route must include origin_id, destination_id, network, optimise_by.
  optimise_by MUST be exactly "time" or "cost" (map fastest/quickest/shortest -> "time",
  cheapest/lowest-fare/lowest-cost -> "cost").
- Route/path/direction/fastest/cheapest/how-to-get questions: use find_route.
- Alternative route / closed station / avoid station / detour questions: use find_alternative_routes.
- Seat availability / available seats / capacity / booking-availability / "is there a
  ticket" / "can I book" / trains-between questions — including Chinese 座位 / 車位 / 有票 /
  是否可訂位 / 可預訂 — MUST select check_national_rail_availability (rail) or
  check_metro_availability (metro). NEVER return an empty tool_calls list for these.
- Policy questions — delay compensation, refund, cancellation, ticket rules, entitled,
  claim, bike/bicycle policy, luggage, travel policy: use search_policy with only
  {{"query": <user question>}}. A "delayed ... what am I entitled to" question is POLICY,
  NOT availability.
- Do NOT use get_user_bookings for a general policy question — only for the user's OWN bookings.
- travel_date is optional: omit it entirely if the user gave no date; never send "".
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
"route MS01 to NR05" -> {{"tool_calls": [{{"name": "find_route", "params": {{"origin_id": "MS01", "destination_id": "NR05", "network": "auto", "optimise_by": "time"}}}}]}}
"Central to Stonehaven seats" -> {{"tool_calls": [{{"name": "check_national_rail_availability", "params": {{"origin_id": "NR01", "destination_id": "NR05"}}}}]}}
"Are there available seats from NR01 to NR05?" -> {{"tool_calls": [{{"name": "check_national_rail_availability", "params": {{"origin_id": "NR01", "destination_id": "NR05"}}}}]}}
"NR01 到 NR05 還有座位嗎？是否可訂位？" -> {{"tool_calls": [{{"name": "check_national_rail_availability", "params": {{"origin_id": "NR01", "destination_id": "NR05"}}}}]}}
"My train was delayed 45 minutes — what compensation am I entitled to?" -> {{"tool_calls": [{{"name": "search_policy", "params": {{"query": "My train was delayed 45 minutes — what compensation am I entitled to?"}}}}]}}
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
                "Select EXACTLY ONE tool — the single best match for the question. "
                "Never select more than one tool and never repeat a tool; a seat-availability "
                "question selects ONLY check_*_availability, not also find_route. "
                "params MUST be the actual function arguments — NEVER a JSON schema "
                "(never output keys like type/properties/required). "
                "Stations MUST be ids: metro=MSxx (MS01-MS20), national rail=NRxx (NR01-NR10). "
                "Convert any station NAME to its id (e.g. Central Station->NR01, "
                "Central Square->MS01, Stonehaven->NR05, Old Town->MS07). "
                "network: both MSxx -> 'metro'; both NRxx -> 'rail'; a mix of MSxx and "
                "NRxx (cross-system) -> 'auto'. "
                "Alternative route / closed station / avoid station questions must use find_alternative_routes. "
                "Route/directions/fastest/quickest/cheapest/path/how-to-get questions must use find_route "
                "with all of origin_id, destination_id, network, optimise_by. "
                "optimise_by MUST be exactly 'time' or 'cost' — map fastest/quickest/shortest to 'time', "
                "cheapest/lowest fare/lowest cost to 'cost'. "
                "Metro fare/price/how much questions use get_metro_fare only when the user asks price, not route. "
                "Seat availability / available seats / capacity / 'is there a ticket' / 'can I book' / "
                "train / service / schedule questions — including Chinese 座位/車位/有票/是否可訂位/可預訂 — "
                "MUST use check_national_rail_availability (rail) or check_metro_availability (metro); "
                "never return an empty tool list for a seat-availability question. "
                "travel_date is optional — omit it if no date is given, never send an empty string. "
                "Policy questions — delay compensation, refund, cancellation, ticket rules, entitled, claim, "
                "bicycle/bike policy, luggage, travel policy — use search_policy with only {query: <user question>}; "
                "a 'delayed ... what am I entitled to' question is POLICY (search_policy), NOT availability. "
                "do NOT call get_user_bookings for a general policy question — only when the user asks about THEIR own bookings. "
                "Output only tool calls."
            ),
        )

    else:
        selection_response = llm.chat(
            messages=[{"role": "user", "content": tool_selection_prompt}],
            system_prompt="JSON only. Output valid JSON. No empty string param values.",
        )
        tool_calls = _parse_tool_calls(selection_response) or []

    # Step 1.4: repair the LLM's tool call (station names -> ids, network
    # inference, schema-as-params, search_policy query, get_user_bookings
    # misroute) so a correct native selection no longer needs the fallback.
    _raw_tool_calls = tool_calls
    tool_calls = _normalize_tool_calls(tool_calls, user_message)

    # Step 1.5: validate the normalised native call. If it passes, it runs as-is
    # and the deterministic fallback is skipped entirely.
    native_valid, _validation_reason = _validate_tool_call(tool_calls)

    if debug:
        debug_info.append(f"**Native tool call:** {_raw_tool_calls if _raw_tool_calls else '[]'}")
        debug_info.append(f"**Normalised tool call:** {tool_calls if tool_calls else '[]'}")
        debug_info.append(
            f"**Validation result:** {'passed' if native_valid else 'failed'}"
        )
        if native_valid:
            debug_info.append("**Fallback skipped:** native call is valid")
        else:
            debug_info.append(f"**Fallback reason:** {_validation_reason}")

    # Step 1.6: deterministic fallback / correction — only reached when the
    # native call failed validation.
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

    def _fallback(name: str, params: dict, reason: str, keep_if: tuple = ()):
        nonlocal tool_calls
        # A native call that passed validation always wins — never enter the
        # fallback in that case. Otherwise rebuild the call from the question.
        if native_valid:
            return
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

    # 2. Train / schedule / service / seat-availability (English + Chinese)
    _is_availability = any(kw in _lower for kw in _AVAILABILITY_KEYWORDS)

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

    # 2. Force train / schedule / seat-availability questions to the correct tool
    if not tool_calls:
        _is_availability = any(kw in _lower for kw in _AVAILABILITY_KEYWORDS)

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

    # 4. Booking analytics / revenue — TASK 6 EXTENSION end-to-end through the agent
    if not tool_calls:
        _analytics_triggers = {
            "revenue",
            "analytics",
            "total bookings",
            "how many bookings",
            "total income",
            "income",
            "sales",
            "earnings",
            "refund total",
            "total refund",
            "booking statistics",
            "booking stats",
        }

        if any(kw in _lower for kw in _analytics_triggers):
            _dates = re.findall(r"\d{4}-\d{2}-\d{2}", user_message)
            _params: dict = {}
            if len(_dates) >= 1:
                _params["start_date"] = _dates[0]
            if len(_dates) >= 2:
                _params["end_date"] = _dates[1]
            _fallback("get_booking_analytics", _params, "forced analytics query")

    # 5. Detailed trip history — TASK 6 EXTENSION (login required)
    if current_user_email and not tool_calls:
        _trip_triggers = {
            "trip history",
            "my trips",
            "past journeys",
            "past trips",
            "journey history",
            "travel history",
        }

        if any(kw in _lower for kw in _trip_triggers):
            _fallback("get_trip_history", {}, "detailed trip-history query")

    if debug:
        debug_info.append(f"**Final call:** {tool_calls if tool_calls else '[]'}")

    # Step 2: Execute each tool call
    tool_results = []

    # Map tool -> required params so we can drop empty *optional* params (e.g. the
    # LLM often fills get_booking_analytics' optional start_date/end_date with "")
    # while still skipping a call that is missing a genuinely required value.
    _required_params = {t["name"]: set(t.get("required", [])) for t in TOOLS}

    for call in tool_calls:
        tool_name = call.get("name", "")
        params = call.get("params") or call.get("parameters", {})

        # Drop empty-string / None params (treat them as "not provided").
        params = {k: v for k, v in params.items() if v != "" and v is not None}

        # Final guarantee: any station-bearing field handed to the tool MUST be a
        # canonical MSxx/NRxx id. Resolve a lingering name (last-resort safety net
        # so success never depends on the fallback), and skip the call if a station
        # reference cannot be resolved rather than querying the DB with a name.
        _hint = (
            "rail" if tool_name in ("check_national_rail_availability", "get_national_rail_fare")
            else "metro" if tool_name in ("check_metro_availability", "get_metro_fare", "calculate_metro_fare")
            else None
        )
        _bad_station = None
        for _field in (
            "origin_id", "destination_id", "avoid_station_id", "station_id",
            "origin_station_id", "destination_station_id",
        ):
            if _field in params:
                _val = str(params[_field])
                if not _STATION_ID_RE.match(_val):
                    _fixed = _resolve_station_id(_val, _hint)
                    if _fixed:
                        params[_field] = _fixed
                    else:
                        _bad_station = (_field, _val)
                        break
        if _bad_station:
            if debug:
                debug_info.append(
                    f"**Skipped** `{tool_name}` — unresolved station id "
                    f"{_bad_station[0]}={_bad_station[1]!r} (not MSxx/NRxx)"
                )
            continue

        missing_required = [
            p for p in _required_params.get(tool_name, set()) if p not in params
        ]
        if missing_required:
            if debug:
                debug_info.append(
                    f"**Skipped** `{tool_name}` — missing required params: {missing_required}"
                )
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