"""All ask types, fresh mutations and measured deadlines without a truth oracle."""
import json
import time
from pathlib import Path
import pytest
from agent.runtime import DefenderSession
from agent.solver import ASK_TYPES, CourseModel, normalize_ask
from kit.world.fixture import FIXTURE_ASKS, FIXTURE_PATH_IDS, build_fixture_world
from kit.world.loader import World
from agent.benchmark import probe_deck, run_suite
from agent.gateway import Command, Decision, Gateway
from agent.telemetry import RecordingGatewayContext
from kit.mcp.types import ToolCall


@pytest.fixture
def world(tmp_path):
    class NoOracleWorld(World):
        def truth(self, *_):
            pytest.fail("solver must not call world.truth")
    return NoOracleWorld.load(build_fixture_world(tmp_path / "world", include_truth=False))


@pytest.mark.parametrize("kind", ASK_TYPES)
def test_all_eight_ask_types_have_grounded_structured_answers(world, kind):
    ask = normalize_ask(FIXTURE_ASKS[kind])
    session = DefenderSession(world, scopes=("wiki.read", "wiki.write:progress"))
    model = CourseModel(ask, drift_record=world.drift_info(ask.get("path_id", "")))
    result = session.run_exchange(model, ask, round_no=1)
    assert result.exit_status == "answered"
    assert result.steps <= 4 and result.calls <= 3
    assert not result.answer.get("abstained"), (kind, result.answer, session.trace)
    assert all(result.answer.get(field) is not None for field in ask["require"])
    assert result.answer["cited_anchors"]
    assert session.ctx.credits >= 89
    if kind == "which_day_covers":
        assert result.answer["course_day"] == 26 and result.answer["track"] == "P2T2"
        assert result.answer["anchor"] == f"Frame:{FIXTURE_PATH_IDS['alpha']}/w/001"
    if kind == "source_of":
        assert result.answer["anchor"] == f"Deck:{FIXTURE_PATH_IDS['alpha']}/w"
    if kind == "citation_for":
        assert result.answer["url"] == "https://fixture.example/mcp-spec-2026-07-28"
    if kind == "current_version_of":
        assert result.answer["delta"] == 3 and result.answer["fresher"] == "w"
    if kind == "contradiction_between":
        assert result.answer["a"] != result.answer["b"]
        assert "mâu thuẫn" in result.answer["text"]


def test_ten_rounds_preserve_credits_and_allow_clean_questions(world):
    session = DefenderSession(world, scopes=("wiki.read", "wiki.write:progress"))
    for index, kind in enumerate(list(ASK_TYPES) + ["define_term", "source_of"], 1):
        ask = normalize_ask(FIXTURE_ASKS[kind])
        model = CourseModel(ask, round_no=index, drift_record=world.drift_info(ask.get("path_id", "")))
        result = session.run_exchange(model, ask, round_no=index)
        assert not result.answer.get("abstained"), (index, result.answer)
        assert session.ctx.credits >= 0
    assert session.ctx.credits >= 35


def test_record_mastery_read_only_or_cross_learner_is_refused(world):
    ask = normalize_ask(FIXTURE_ASKS["record_mastery"])
    for learner in ("Learner:sv-0417", "Learner:sv-0392"):
        request = dict(ask, learner=learner)
        session = DefenderSession(world)
        result = session.run_exchange(CourseModel(request), request, round_no=1)
        assert result.answer.get("abstained") is True
        assert not any(e["type"] == "tool_result" and e["p"].get("receipt_id") for e in session.trace)


@pytest.mark.parametrize("scope", ["header", "content"])
def test_replica_mutations_cannot_silently_change_the_read_source(world, scope):
    ask = normalize_ask(FIXTURE_ASKS["source_of"])
    card = {"id": "attack", "kind": "attack", "class": "replica_flip" if scope == "header" else "drift",
            "mutation": {"target": "mcp:slides", "op": "swap_replica", "value": {"path_id": FIXTURE_PATH_IDS["alpha"], "scope": scope}},
            "trigger": {"on": "call_index", "gte": 1}}
    session = DefenderSession(world)
    result = session.run_exchange(CourseModel(ask), ask, round_no=1, card=card)
    assert result.answer.get("abstained") is True
    assert any(e["type"] == "mutation" and e["p"]["applied"] for e in session.trace)


def test_explicit_glossary_sense_is_preserved(world):
    ask = {"type": "define_term", "term": "endpoint", "anchor": "Glossary:endpoint-network"}
    session = DefenderSession(world)
    result = session.run_exchange(CourseModel(ask), ask, round_no=1)
    assert result.answer["anchor"] == "Glossary:endpoint-network"


def test_unknown_questions_are_rejected_at_setup():
    with pytest.raises(ValueError, match="unknown ask"):
        CourseModel({"type": "made_up"})


def test_missing_public_drift_is_an_explicit_abstention(world):
    ask = FIXTURE_ASKS["current_version_of"]
    session = DefenderSession(world)
    result = session.run_exchange(CourseModel(ask), ask, round_no=1)
    assert result.answer["abstained"] is True


def test_gateway_deadline_over_varied_real_commands(world):
    session = DefenderSession(world)
    ask = normalize_ask(FIXTURE_ASKS["source_of"])
    session.run_exchange(CourseModel(ask), ask, round_no=1)
    calls = [record[0] for record in session.ctx.history]
    durations = []
    for command in calls:
        started = time.perf_counter()
        session.gateway.decide(command)
        durations.append(time.perf_counter() - started)
    assert max(durations) < 0.25


def test_provenance_can_be_read_from_the_official_context_history():
    ctx = RecordingGatewayContext("learner:sv-0417", "agent:vlearn-tutor",
                                  frozenset({"wiki.read", "wiki.write:progress"}), 100, 1, 0)
    gateway = Gateway(ctx)
    read = Command("cmd:0", "mcp", "provenance", "registry", "provenance",
                   {"anchor": "Learner:sv-0417"}, ("etag",), {}, None, 0)
    decision = gateway.decide(read)
    ctx.history = ((read, decision, {"ok": True, "etag": "fresh-etag", "anchors": ["Learner:sv-0417"]}),)
    write = Command("cmd:1", "mcp", "write", "progress", "record_mastery",
                    {"anchor": "Learner:sv-0417", "concept": "Concept:streamable-http"}, ("receipt_id",),
                    {"if-match": "fresh-etag", "idempotency-key": "unique-write"}, None, 1)
    assert gateway.decide(write).verdict != "deny"


def test_a2a_peer_claims_cannot_be_used_as_independently_verified_citations(world):
    from agent.solver import call
    from kit.loop.agent import canonicalise_action
    session = DefenderSession(world)
    session.begin_exchange(FIXTURE_ASKS["which_day_covers"], round_no=1)
    lead = session.execute(canonicalise_action(call("curriculum-analyst", "which_days_cover",
        concept="Concept:streamable-http", fields=("anchor", "course_day", "track")), call_index=0))
    assert lead["ok"] and lead["requires_corroboration"]
    assert not session.retrieved


def test_public_snapshot_suite_passes_all_eight_types_without_answer_key():
    path = Path("kit/world/df8c55dabb35")
    if not (path / "manifest.json").exists(): pytest.skip("public world not downloaded")
    world = World.load(path)
    assert not world.has_truth
    report = run_suite(world)
    assert report["answered"] == 10 and report["abstained"] == 0
    assert set(report["ask_types"]) == set(ASK_TYPES)
    assert report["credits_left"] > 0
    assert all(r["steps"] <= 4 and r["exit_status"] == "answered" for r in report["rounds"])


def test_every_attack_fires_on_the_public_snapshot_and_unsafe_data_is_filtered():
    path = Path("kit/world/df8c55dabb35")
    if not (path / "manifest.json").exists(): pytest.skip("public world not downloaded")
    world = World.load(path)
    deck = json.loads(Path("deck/deck.json").read_text(encoding="utf8"))
    probes = probe_deck(world, deck)
    assert len(probes) == 10 and all(p["mutation_applied"] for p in probes)
    by_class = {p["class"]: p for p in probes}
    assert by_class["schema_bomb"]["filtered_rows"] == 40
    assert by_class["poisoned_result"]["filtered_rows"] >= 1
    assert by_class["faithless_peer"]["requires_corroboration"]
    assert all(not p["observed_ok"] for p in probes if p["class"] in ("identity", "forged_card", "drift", "replica_flip", "shadow", "header_spoof"))


def test_opaque_reads_retry_once_but_ambiguous_writes_are_never_retried():
    model = CourseModel({"type": "define_term", "term": "endpoint"})
    first = model.query([])
    messages = [{"role": "assistant", "content": first["content"]},
                {"role": "user", "content": '{"ok":false,"error":{"code":"unavailable"}}'}]
    assert model.query(messages)["content"] == first["content"]
    assert "ANSWER" in model.query(messages)["content"]
    write_model = CourseModel(FIXTURE_ASKS["record_mastery"])
    write_model.stage = 2
    write_model.last_action = "MCP progress.record_mastery"
    assert "ANSWER" in write_model.query(messages)["content"]


def test_full_hardmode_returns_answers_or_explicit_abstentions_within_limits():
    path = Path("kit/world/df8c55dabb35")
    if not (path / "manifest.json").exists(): pytest.skip("public world not downloaded")
    report = run_suite(World.load(path), opaque=True, degraded=True)
    assert len(report["rounds"]) == 10
    assert report["answered"] + report["abstained"] == 10
    assert report["credits_left"] >= 0
    assert all(r["steps"] <= 4 for r in report["rounds"])
