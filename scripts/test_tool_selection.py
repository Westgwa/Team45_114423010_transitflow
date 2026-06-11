"""
TransitFlow — Tool-Selection / Normalisation Tests
==================================================
Pure-logic tests for the agent's native tool selection + parameter
normalisation layer (skeleton/agent.py). These verify that a *single* native
tool call is repaired into a correct, complete call WITHOUT relying on the
deterministic fallback — covering the recurring native-selection bugs:

  1. station name  -> station_id        (Central -> NR01, Stonehaven -> NR05)
  2. network metro/rail/auto inference  (NRxx -> rail, MSxx -> metro, mix -> auto)
  3. optimise_by synonyms               (fastest/quickest/shortest_time -> time)
  4. empty travel_date                  ("" dropped, never sent as null)
  5. wrong tool selection               (delay compensation -> search_policy)
  6. schema-shaped params               ({properties,...} -> {"query": ...})
  7. related avoid station              (avoid MS07 kept; backend maps -> NR03)
  8. station_id typo                    (MR01 -> NR01)

No database / LLM / Ollama is required: the heavy modules imported by
skeleton.agent are stubbed in sys.modules before import, so only the pure
helper functions run.

Usage:
    python scripts/test_tool_selection.py

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─────────────────────────────────────────────────────────────────────────────
# Stub the heavy modules so importing skeleton.agent does NOT connect to
# Ollama / Postgres / Neo4j. We only exercise the pure normalisation helpers.
# ─────────────────────────────────────────────────────────────────────────────
def _install_stubs() -> None:
    llm_mod = types.ModuleType("skeleton.llm_provider")

    class _StubLLM:
        """Programmable stub: tests set .provider and .next_tool_calls."""

        def __init__(self):
            self.provider = "stub"
            self.next_tool_calls = []

        def get_chat_provider(self):
            return self.provider

        def chat(self, *a, **k):
            return "STUB ANSWER"

        def embed(self, *a, **k):
            return []

        def ollama_tool_call(self, *a, **k):
            return list(self.next_tool_calls)

    llm_mod.llm = _StubLLM()
    sys.modules["skeleton.llm_provider"] = llm_mod

    notif_mod = types.ModuleType("skeleton.notifications")
    notif_mod.notifications = types.SimpleNamespace(notify=lambda *a, **k: None)
    sys.modules["skeleton.notifications"] = notif_mod

    rel_mod = types.ModuleType("databases.relational.queries")
    for fn in (
        "query_national_rail_availability", "query_national_rail_fare",
        "query_metro_schedules", "query_metro_fare", "query_available_seats",
        "query_user_profile", "query_user_bookings", "execute_booking",
        "execute_cancellation", "query_policy_vector_search",
        "query_booking_revenue_summary", "query_trip_history",
    ):
        setattr(rel_mod, fn, lambda *a, **k: None)
    sys.modules["databases.relational.queries"] = rel_mod

    graph_mod = types.ModuleType("databases.graph.queries")
    for fn in (
        "query_shortest_route", "query_cheapest_route",
        "query_alternative_routes", "query_interchange_path",
        "query_delay_ripple",
    ):
        setattr(graph_mod, fn, lambda *a, **k: None)
    sys.modules["databases.graph.queries"] = graph_mod


_install_stubs()

from skeleton import agent  # noqa: E402  (import after stubs are installed)


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def _one(native_call: dict, message: str) -> dict:
    """Run a single native tool call through the normaliser, return the result."""
    out = agent._normalize_tool_calls([native_call], message)
    assert len(out) == 1, out
    return out[0]


# ── Helper-level unit checks ──────────────────────────────────────────────────

def section_helpers():
    print("\n=== Helpers: station / network / optimise_by / typo ===")

    check("resolve 'Central' (rail hint) -> NR01",
          agent._resolve_station_id("Central", "rail") == "NR01")
    check("resolve 'Central Square' -> MS01",
          agent._resolve_station_id("Central Square") == "MS01")
    check("resolve 'Stonehaven' -> NR05",
          agent._resolve_station_id("Stonehaven") == "NR05")
    check("resolve already-id 'NR05' -> NR05",
          agent._resolve_station_id("NR05") == "NR05")

    check("network NR01+NR05 -> rail",
          agent._infer_network("NR01", "NR05") == "rail")
    check("network MS01+MS14 -> metro",
          agent._infer_network("MS01", "MS14") == "metro")
    check("network MS01+NR05 -> auto (cross-system)",
          agent._infer_network("MS01", "NR05") == "auto")

    check("optimise 'fastest' -> time",
          agent._normalise_optimise_by("fastest", "fastest route") == "time")
    check("optimise 'quickest' -> time",
          agent._normalise_optimise_by("quickest", "x") == "time")
    check("optimise 'shortest_time' -> time",
          agent._normalise_optimise_by("shortest_time", "x") == "time")
    check("optimise 'cheapest' -> cost",
          agent._normalise_optimise_by("cheapest", "x") == "cost")
    check("optimise missing -> inferred 'time'",
          agent._normalise_optimise_by(None, "best route") == "time")

    check("typo 'MR01' -> NR01",
          agent._correct_station_typo("MR01") == "NR01")
    check("typo 'NS01' (metro hint) -> MS01",
          agent._correct_station_typo("NS01", "metro") == "MS01")
    check("typo 'ZZ99' (unfixable) -> None",
          agent._correct_station_typo("ZZ99") is None)

    check("schema-shaped params detected",
          agent._params_look_like_schema(
              {"properties": {"query": {"type": "string"}},
               "type": "object", "required": ["query"]}) is True)

    # Station-name variants that previously leaked to the tool as names.
    check("resolve 'Central Rail Station' -> NR01",
          agent._resolve_station_id("Central Rail Station", "rail") == "NR01")
    check("resolve 'Stonehaven (NR05)' (embedded id) -> NR05",
          agent._resolve_station_id("Stonehaven (NR05)") == "NR05")
    check("resolve 'National Rail Stonehaven' -> NR05",
          agent._resolve_station_id("National Rail Stonehaven", "rail") == "NR05")
    check("resolve 'Ferndale Halt' -> NR07",
          agent._resolve_station_id("Ferndale Halt", "rail") == "NR07")
    check("resolve unknown 'Random Place' -> None",
          agent._resolve_station_id("Random Place") is None)


# ── Scenario checks (the 7 cases from the request) ────────────────────────────

def section_scenarios():
    print("\n=== Scenarios: native call -> normalised call ===")

    # 1. national rail availability: Central -> Stonehaven (names -> NR01/NR05)
    r = _one(
        {"name": "check_national_rail_availability",
         "params": {"origin_id": "Central", "destination_id": "Stonehaven"}},
        "Are there available seats from Central to Stonehaven?",
    )
    check("1) availability Central->Stonehaven becomes NR01->NR05",
          r["name"] == "check_national_rail_availability"
          and r["params"]["origin_id"] == "NR01"
          and r["params"]["destination_id"] == "NR05",
          str(r))

    # 2. metro route MS01 -> MS14, optimise_by fastest -> time, network metro
    r = _one(
        {"name": "find_route",
         "params": {"origin_id": "MS01", "destination_id": "MS14",
                    "network": "metro", "optimise_by": "fastest"}},
        "What is the fastest route from MS01 to MS14?",
    )
    check("2) metro route -> network=metro, optimise_by=time",
          r["name"] == "find_route"
          and r["params"]["network"] == "metro"
          and r["params"]["optimise_by"] == "time",
          str(r))

    # 3. cross-system route MS01 -> NR05: native said metro, must become auto
    r = _one(
        {"name": "find_route",
         "params": {"origin_id": "MS01", "destination_id": "NR05",
                    "network": "metro", "optimise_by": "fastest"}},
        "How do I get from MS01 to NR05?",
    )
    check("3) cross-system route -> network=auto, optimise_by=time",
          r["name"] == "find_route"
          and r["params"]["network"] == "auto"
          and r["params"]["optimise_by"] == "time",
          str(r))

    # 4. national rail alternative route NR01->NR05 avoid NR03: native said metro
    r = _one(
        {"name": "find_alternative_routes",
         "params": {"origin_id": "NR01", "destination_id": "NR05",
                    "avoid_station_id": "NR03", "network": "metro"}},
        "If NR03 is closed, what alternative routes exist from NR01 to NR05?",
    )
    check("4) alternative route -> network=rail, avoid=NR03",
          r["name"] == "find_alternative_routes"
          and r["params"]["network"] == "rail"
          and r["params"]["avoid_station_id"] == "NR03",
          str(r))

    # 5. related avoid station: NR01->NR05 avoid MS07. Agent keeps MS07 and rail;
    #    the backend maps MS07 -> NR03 into related_avoid_station_ids.
    r = _one(
        {"name": "find_alternative_routes",
         "params": {"origin_id": "NR01", "destination_id": "NR05",
                    "avoid_station_id": "MS07", "network": "rail"}},
        "Alternative routes from NR01 to NR05 avoiding Old Town metro (MS07)?",
    )
    check("5) avoid MS07 preserved, network=rail (backend maps -> NR03)",
          r["params"]["avoid_station_id"] == "MS07"
          and r["params"]["network"] == "rail",
          str(r))

    # 6. delay compensation must go to search_policy, NOT availability
    msg = "My train was delayed 45 minutes — what compensation am I entitled to?"
    r = _one(
        {"name": "check_national_rail_availability",
         "params": {"origin_id": "", "destination_id": ""}},
        msg,
    )
    check("6) delay-compensation reroutes availability -> search_policy",
          r["name"] == "search_policy" and r["params"].get("query") == msg,
          str(r))

    # 7. bicycle policy: schema-shaped params -> real {"query": ...}
    msg = "What is the company policy on travelling with a bicycle on national rail?"
    r = _one(
        {"name": "search_policy",
         "params": {"properties": {"query": {"type": "string"}},
                    "type": "object", "required": ["query"]}},
        msg,
    )
    check("7) search_policy schema params -> {'query': <message>}",
          r["name"] == "search_policy"
          and r["params"] == {"query": msg},
          str(r))


# ── Extra robustness checks ───────────────────────────────────────────────────

def section_extra():
    print("\n=== Extra: typo id + empty travel_date ===")

    # station_id typo correction inside a real tool call: MR01 -> NR01
    r = _one(
        {"name": "check_national_rail_availability",
         "params": {"origin_id": "MR01", "destination_id": "NR05"}},
        "Trains from MR01 to NR05?",
    )
    check("typo MR01 in call corrected -> NR01",
          r["params"]["origin_id"] == "NR01"
          and r["params"]["destination_id"] == "NR05",
          str(r))

    # empty travel_date must be dropped (never sent as "")
    r = _one(
        {"name": "check_national_rail_availability",
         "params": {"origin_id": "NR01", "destination_id": "NR05",
                    "travel_date": ""}},
        "Trains from NR01 to NR05?",
    )
    check("empty travel_date dropped (not '')",
          "travel_date" not in r["params"],
          str(r))

    # placeholder travel_date 'null' must be dropped (else Postgres date error)
    r = _one(
        {"name": "check_national_rail_availability",
         "params": {"origin_id": "Central", "destination_id": "Stonehaven",
                    "travel_date": "null"}},
        "Seats from Central to Stonehaven?",
    )
    check("travel_date 'null' dropped + names -> NR01/NR05",
          "travel_date" not in r["params"]
          and r["params"]["origin_id"] == "NR01"
          and r["params"]["destination_id"] == "NR05",
          str(r))

    # a non-ISO travel_date is dropped rather than passed to the DB
    r = _one(
        {"name": "check_national_rail_availability",
         "params": {"origin_id": "NR01", "destination_id": "NR05",
                    "travel_date": "tomorrow"}},
        "Trains from NR01 to NR05?",
    )
    check("non-ISO travel_date 'tomorrow' dropped",
          "travel_date" not in r["params"], str(r))

    # a real ISO travel_date is kept
    r = _one(
        {"name": "check_national_rail_availability",
         "params": {"origin_id": "NR01", "destination_id": "NR05",
                    "travel_date": "2026-08-01"}},
        "Trains from NR01 to NR05 on 2026-08-01?",
    )
    check("ISO travel_date 2026-08-01 kept",
          r["params"].get("travel_date") == "2026-08-01", str(r))

    check("_is_nullish('null') True / '2026-08-01' False",
          agent._is_nullish("null") and agent._is_nullish("None")
          and not agent._is_nullish("2026-08-01"))


def section_validation():
    """_validate_tool_call gates the fallback: passes only on a complete,
    id-correct single call; fails on exactly the cases the fallback exists for."""
    print("\n=== Validation gate ===")

    ok, _ = agent._validate_tool_call(
        [{"name": "check_national_rail_availability",
          "params": {"origin_id": "NR01", "destination_id": "NR05"}}])
    check("valid availability call -> passed", ok is True)

    ok, reason = agent._validate_tool_call([])
    check("empty selection -> failed", ok is False and "no tool" in reason)

    ok, reason = agent._validate_tool_call([
        {"name": "check_national_rail_availability", "params": {"origin_id": "NR01", "destination_id": "NR05"}},
        {"name": "find_route", "params": {"origin_id": "NR01", "destination_id": "NR05"}},
    ])
    check("two tools -> failed", ok is False and "2 tools" in reason)

    ok, reason = agent._validate_tool_call(
        [{"name": "check_national_rail_availability",
          "params": {"origin_id": "Central Station", "destination_id": "NR05"}}])
    check("station name (not id) -> failed",
          ok is False and "not a valid station id" in reason, reason)

    ok, reason = agent._validate_tool_call(
        [{"name": "check_national_rail_availability", "params": {"origin_id": "NR01"}}])
    check("missing required param -> failed",
          ok is False and "missing required" in reason, reason)

    ok, reason = agent._validate_tool_call(
        [{"name": "search_policy",
          "params": {"properties": {"query": {"type": "string"}}, "type": "object"}}])
    check("schema-shaped params -> failed",
          ok is False and "schema" in reason, reason)

    ok, reason = agent._validate_tool_call(
        [{"name": "find_route",
          "params": {"origin_id": "NR01", "destination_id": "NR05",
                     "network": "metro", "optimise_by": "time"}}])
    check("find_route wrong network -> failed",
          ok is False and "network" in reason, reason)

    ok, reason = agent._validate_tool_call(
        [{"name": "find_route",
          "params": {"origin_id": "MS01", "destination_id": "MS14",
                     "network": "metro", "optimise_by": "fastest"}}])
    check("find_route unsupported optimise_by -> failed",
          ok is False and "optimise_by" in reason, reason)


def section_runagent():
    """End-to-end: a correct native call must drive the result WITHOUT the
    deterministic fallback overwriting it."""
    print("\n=== run_agent: native selection drives result (no fallback override) ===")

    # Make the agent run the Ollama (native) path and return whatever data the
    # tool produces, then capture the debug trace.
    agent.llm.provider = "ollama"
    captured = {}

    def _fake_avail(**params):
        captured["called_with"] = params
        return [{"schedule_id": "NRX1", "origin_id": params.get("origin_id"),
                 "destination_id": params.get("destination_id")}]

    orig = agent.query_national_rail_availability
    agent.query_national_rail_availability = _fake_avail
    try:
        # Native already chose the right tool with valid (normalised) ids.
        agent.llm.next_tool_calls = [
            {"name": "check_national_rail_availability",
             "params": {"origin_id": "NR01", "destination_id": "NR05"}}
        ]
        _, _, debug = agent.run_agent(
            "Are there trains from NR01 to NR05?", history=[], debug=True,
        )
        check("native availability call executed with NR01->NR05",
              captured.get("called_with", {}).get("origin_id") == "NR01"
              and captured.get("called_with", {}).get("destination_id") == "NR05",
              str(captured.get("called_with")))
        check("debug shows Validation result: passed",
              "Validation result:** passed" in debug)
        check("debug shows 'Fallback skipped: native call is valid'",
              "Fallback skipped:** native call is valid" in debug)
        check("no forced fallback fired (**Fallback:** absent)",
              "**Fallback:**" not in debug,
              "fallback fired" if "**Fallback:**" in debug else "ok")
        check("debug shows Final call = native call",
              "Final call:" in debug and "check_national_rail_availability" in debug)
    finally:
        agent.query_national_rail_availability = orig
        agent.llm.provider = "stub"
        agent.llm.next_tool_calls = []


def _run_native_empty(message: str):
    """Run run_agent with the native path returning [] (no tool), capturing the
    availability call params + debug trace. Returns (called_with, debug)."""
    agent.llm.provider = "ollama"
    agent.llm.next_tool_calls = []          # native picked nothing
    captured = {}

    def _fake_avail(**params):
        captured["called_with"] = params
        return [{"schedule_id": "NRX1", **params}]

    orig = agent.query_national_rail_availability
    agent.query_national_rail_availability = _fake_avail
    try:
        _, _, debug = agent.run_agent(message, history=[], debug=True)
    finally:
        agent.query_national_rail_availability = orig
        agent.llm.provider = "stub"
        agent.llm.next_tool_calls = []
    return captured.get("called_with"), debug


def section_seat_availability():
    """Native returns [] for a seat-availability question; the fallback must
    select check_national_rail_availability with DYNAMIC origin/destination."""
    print("\n=== Seat availability: native [] -> fallback picks availability (dynamic) ===")

    # English "available seats", dynamic ids NR06 -> NR08 (NOT a hard-coded NR01->NR05).
    called, _ = _run_native_empty("Are there available seats from NR06 to NR08?")
    check("EN available-seats -> check_national_rail_availability NR06->NR08 (dynamic)",
          called is not None
          and called.get("origin_id") == "NR06"
          and called.get("destination_id") == "NR08",
          str(called))
    check("EN available-seats -> no empty travel_date sent",
          called is not None and called.get("travel_date") in (None, ),
          str(called))

    # Chinese seat / 可訂位 question, different ids again to prove it is not fixed.
    called, _ = _run_native_empty("NR02 到 NR04 還有座位嗎？是否可訂位？")
    check("ZH 座位/可訂位 -> check_national_rail_availability NR02->NR04 (dynamic)",
          called is not None
          and called.get("origin_id") == "NR02"
          and called.get("destination_id") == "NR04",
          str(called))

    # Name-based seat query: Central Station -> Stonehaven must resolve to NR01 -> NR05.
    called, _ = _run_native_empty("Any seats from Central Station to Stonehaven?")
    check("name-based seats Central Station->Stonehaven -> NR01->NR05",
          called is not None
          and called.get("origin_id") == "NR01"
          and called.get("destination_id") == "NR05",
          str(called))


def section_station_guarantee():
    """The tool must only ever receive MSxx/NRxx ids — names are normalised
    before the call, and an unresolvable station is skipped, not queried."""
    print("\n=== Station id guarantee: names -> ids before tool call ===")

    # Normalised single call: name variants -> ids.
    out = agent._normalize_tool_calls(
        [{"name": "check_national_rail_availability",
          "params": {"origin_id": "Central Rail Station",
                     "destination_id": "Stonehaven (NR05)"}}],
        "Seats from Central Rail Station to Stonehaven (NR05)?",
    )
    check("normalise 'Central Rail Station'/'Stonehaven (NR05)' -> NR01/NR05",
          out[0]["params"]["origin_id"] == "NR01"
          and out[0]["params"]["destination_id"] == "NR05",
          str(out))

    # Execution guard: a resolvable name reaches the tool as an id.
    called, _ = _run_native_empty("Are there seats from Central Station to Stonehaven?")
    check("run_agent: Central Station/Stonehaven reach tool as NR01/NR05",
          called is not None
          and called.get("origin_id") == "NR01"
          and called.get("destination_id") == "NR05",
          str(called))

    # Execution guard: an UNRESOLVABLE station must NOT be queried with a name.
    agent.llm.provider = "ollama"
    agent.llm.next_tool_calls = [
        {"name": "check_national_rail_availability",
         "params": {"origin_id": "Nowhereville", "destination_id": "Stonehaven"}}
    ]
    seen = {}

    def _capture(**p):
        seen["params"] = p
        return []

    orig = agent.query_national_rail_availability
    agent.query_national_rail_availability = _capture
    try:
        _, _, debug = agent.run_agent(
            "Seats from Nowhereville to Stonehaven?", history=[], debug=True)
    finally:
        agent.query_national_rail_availability = orig
        agent.llm.provider = "stub"
        agent.llm.next_tool_calls = []
    # Either it was skipped (no call), or any call made used a valid id — never a name.
    ok = "params" not in seen or (
        agent._STATION_ID_RE.match(str(seen["params"].get("origin_id", "")))
        and agent._STATION_ID_RE.match(str(seen["params"].get("destination_id", "")))
    )
    check("unresolvable station never reaches DB as a name",
          ok, f"called_with={seen.get('params')}")


def _run_native_empty_debug(message: str):
    """Run run_agent with native [] and return (called_with, debug)."""
    agent.llm.provider = "ollama"
    agent.llm.next_tool_calls = []
    captured = {}

    def _fake_avail(**params):
        captured["p"] = params
        return [{"schedule_id": "NRX1", **params}]

    orig = agent.query_national_rail_availability
    agent.query_national_rail_availability = _fake_avail
    try:
        _, _, debug = agent.run_agent(message, history=[], debug=True)
    finally:
        agent.query_national_rail_availability = orig
        agent.llm.provider = "stub"
        agent.llm.next_tool_calls = []
    return captured.get("p"), debug


def section_intent_seeding():
    """Native [] for an availability question must be SEEDED into a
    check_national_rail_availability call (not left to the late fallback)."""
    print("\n=== Intent seeding: availability never returns [] ===")

    # _classify_intent
    check("classify 'available seats ...' -> availability",
          agent._classify_intent("Are there available seats from NR01 to NR05?") == "availability")
    check("classify '有沒有座位 ... 可不可以訂' -> availability",
          agent._classify_intent("NR01 到 NR05 有沒有座位？可不可以訂？") == "availability")
    check("classify 'tickets available ...' -> availability",
          agent._classify_intent("Are tickets available from NR01 to NR05?") == "availability")

    # _seed_availability_call resolves names -> ids in order
    seed = agent._seed_availability_call("Any seats from Central Station to Stonehaven Station?")
    check("seed Central Station/Stonehaven Station -> NR01/NR05",
          seed is not None and seed["name"] == "check_national_rail_availability"
          and seed["params"]["origin_id"] == "NR01"
          and seed["params"]["destination_id"] == "NR05",
          str(seed))

    # End-to-end: native [] + availability -> seeded, validated, fallback skipped
    called, debug = _run_native_empty_debug(
        "Are there available seats from Central Station to Stonehaven Station?")
    check("EN names, native [] -> tool called with NR01/NR05",
          called is not None
          and called.get("origin_id") == "NR01"
          and called.get("destination_id") == "NR05",
          str(called))
    check("debug shows 'Intent detected:** availability'",
          "Intent detected:** availability" in debug)
    check("debug shows intent-seeded availability call",
          "Intent-seeded availability call" in debug)
    check("debug shows Validation passed + fallback skipped (no forced **Fallback:**)",
          "Validation result:** passed" in debug
          and "Fallback skipped:** native call is valid" in debug
          and "**Fallback:**" not in debug,
          "forced fallback fired" if "**Fallback:**" in debug else "ok")

    # Chinese availability also seeds.
    called, debug = _run_native_empty_debug("NR01 到 NR05 有沒有座位？可不可以訂？")
    check("ZH availability, native [] -> tool called with NR01/NR05",
          called is not None
          and called.get("origin_id") == "NR01"
          and called.get("destination_id") == "NR05",
          str(called))


def section_single_tool():
    """A multi-tool native selection must collapse to the single best tool."""
    print("\n=== Single-tool selection: collapse multi-tool native results ===")

    # availability + stray find_route -> keep ONLY availability
    out = agent._normalize_tool_calls(
        [
            {"name": "check_national_rail_availability",
             "params": {"origin_id": "NR01", "destination_id": "NR05"}},
            {"name": "find_route",
             "params": {"origin_id": "NR01", "destination_id": "NR05"}},
        ],
        "Are there available seats from NR01 to NR05?",
    )
    check("availability + find_route -> only check_national_rail_availability",
          len(out) == 1 and out[0]["name"] == "check_national_rail_availability",
          str(out))

    # duplicate availability calls -> de-duplicated to one
    out = agent._normalize_tool_calls(
        [
            {"name": "check_national_rail_availability",
             "params": {"origin_id": "NR01", "destination_id": "NR05"}},
            {"name": "check_national_rail_availability",
             "params": {"origin_id": "Central", "destination_id": "Stonehaven"}},
        ],
        "Any seats NR01 to NR05?",
    )
    check("duplicate availability calls collapse to one (names normalised)",
          len(out) == 1 and out[0]["name"] == "check_national_rail_availability"
          and out[0]["params"]["origin_id"] == "NR01"
          and out[0]["params"]["destination_id"] == "NR05",
          str(out))

    # route question with a stray availability -> keep find_route
    out = agent._normalize_tool_calls(
        [
            {"name": "check_national_rail_availability",
             "params": {"origin_id": "MS01", "destination_id": "MS14"}},
            {"name": "find_route",
             "params": {"origin_id": "MS01", "destination_id": "MS14",
                        "optimise_by": "fastest"}},
        ],
        "What is the fastest route from MS01 to MS14?",
    )
    check("route question -> only find_route (network=metro, optimise_by=time)",
          len(out) == 1 and out[0]["name"] == "find_route"
          and out[0]["params"]["network"] == "metro"
          and out[0]["params"]["optimise_by"] == "time",
          str(out))

    # station names still normalised inside the surviving call
    out = agent._normalize_tool_calls(
        [
            {"name": "check_national_rail_availability",
             "params": {"origin_id": "Central", "destination_id": "Stonehaven"}},
            {"name": "find_route",
             "params": {"origin_id": "Central", "destination_id": "Stonehaven"}},
        ],
        "Seats from Central to Stonehaven?",
    )
    check("surviving availability call has names -> NR01/NR05",
          len(out) == 1 and out[0]["name"] == "check_national_rail_availability"
          and out[0]["params"]["origin_id"] == "NR01"
          and out[0]["params"]["destination_id"] == "NR05",
          str(out))


def main() -> int:
    print("TransitFlow — Tool-Selection / Normalisation Tests")
    section_helpers()
    section_scenarios()
    section_extra()
    section_validation()
    section_runagent()
    section_seat_availability()
    section_station_guarantee()
    section_intent_seeding()
    section_single_tool()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\n{'=' * 60}")
    print(f"  {passed} passed, {failed} failed")
    print(f"{'=' * 60}")
    if failed:
        print("\nFailed checks:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name} :: {detail}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
