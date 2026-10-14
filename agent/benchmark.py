"""Reproducible real-stack exercises and attack probes, not tournament judging."""
from __future__ import annotations
import argparse
import importlib
import json
from pathlib import Path
from agent.gateway import Gateway
from agent.runtime import DefenderSession
from agent.solver import ASK_TYPES, CourseModel, call, normalize_ask
from kit.loop.agent import canonicalise_action
from kit.world.loader import World


def suite_asks(world):
    concepts = [a for a in sorted(world._offsets) if a.startswith("Concept:")]
    concept = next(a for a in concepts if len({f.split("/")[0] for f in map(str, world.links(a))
                                           if f.startswith("Frame:")}) == 1)
    frame = next(str(a) for a in world.links(concept) if a.ns == "Frame" and a.rev == "w")
    sources = [a for a in sorted(world._offsets) if a.startswith("Source:")]
    source = next(a for a in sources if str(world.page(a).meta.get("url", "")).startswith("http"))
    paths = [p for p in sorted(world._drift) if world.page(f"Deck:{p}/w") and world.page(f"Deck:{p}/c")]
    path = next((p for p in paths if world.drifts(p)), paths[0])
    talks = [a for a in sorted(world._offsets) if a.startswith("Talk:")]
    talk = next((a for a in talks if len([l for l in world.page(a).links if l.startswith("Claim:")]) >= 2), talks[0])
    term = next(t for t in ("mcp registry", "streamable http", "lease", "endpoint") if world.terms(t))
    asks = [
        {"type": "which_day_covers", "concept": concept},
        {"type": "source_of", "anchor": frame},
        {"type": "citation_for", "anchor": source},
        {"type": "current_version_of", "path_id": path},
        {"type": "contradiction_between", "talk": talk},
        {"type": "define_term", "term": term},
        {"type": "whatlinkshere", "anchor": concept},
        {"type": "record_mastery", "learner": "Learner:sv-0417", "concept": concept},
        {"type": "define_term", "term": term},
        {"type": "source_of", "anchor": frame},
    ]
    return [normalize_ask(ask) for ask in asks]


def run_suite(world, *, gateway_cls=Gateway, asks=None, cards=None, opaque=False, degraded=False):
    session = DefenderSession(world, scopes=("wiki.read", "wiki.write:progress"),
                              gateway_cls=gateway_cls, opaque=opaque, degraded=degraded)
    outcomes = []
    for index, ask in enumerate(asks or suite_asks(world), 1):
        model = CourseModel(ask, round_no=index, drift_record=world.drift_info(ask.get("path_id", "")))
        result = session.run_exchange(model, ask, round_no=index, card=(cards or [None] * 10)[index - 1])
        outcomes.append({"round": index, "ask": ask, "answer": result.answer, "steps": result.steps,
                         "calls": result.calls, "credits_left": session.ctx.credits,
                         "mutations": [e["p"] for e in session.trace if e["type"] == "mutation"],
                         "exit_status": result.exit_status})
    return {"scope": "real MutableStack; local answer validation; not instructor referee",
            "answered": sum(not r["answer"].get("abstained", False) for r in outcomes),
            "abstained": sum(r["answer"].get("abstained", False) for r in outcomes),
            "ask_types": sorted({r["ask"]["type"] for r in outcomes}),
            "credits_left": session.ctx.credits, "rounds": outcomes}


def probe_deck(world, deck):
    """Force each declared target through the real mutation engine and inspect output.

    This tests that a card actually fires and records how result validation handled
    it. It does not substitute these probes for an official lethality-band verdict.
    """
    reports = []
    for card in deck["cards"]:
        if card["kind"] != "attack": continue
        session = DefenderSession(world, scopes=("wiki.read", "wiki.write:progress"))
        session.begin_exchange(card["ask"], round_no=1, card=card)
        mutation = card["mutation"]
        target = mutation["target"].split(":", 1)[1]
        gte = int((card.get("trigger") or {}).get("gte", 0))
        # Prepare a real frame lease for slide probes and write provenance otherwise.
        if target == "slides":
            session.execute(canonicalise_action(call("slides", "query", q="mcp", ns="Frame", limit=1, fields=("title",)), call_index=0))
        for _ in range(session.ctx.call_index, gte):
            session.execute(canonicalise_action(call("registry", "provenance", anchor="Learner:sv-0417", fields=("etag",)), call_index=0))
        if target == "slides":
            path = (mutation.get("value") or {}).get("path_id")
            if not path:
                linked = list(world.links(card["ask"].get("concept", "")))
                path = next((a.slug for a in linked if a.ns == "Frame" and a.rev == "w"), None)
            anchor = f"Frame:{path}/w/001"
            lease = next(iter(session._leases), None)
            action = call("slides", "get_frame", anchor=anchor, fields=("title", "body"), lease=lease)
        elif target == "research":
            action = call("research", "search", q="mcp", limit=1, fields=("anchor", "snippet"))
        elif target == "registry":
            action = call("registry", "list_servers", fields=("name",))
        elif target == "progress":
            prior = session.execute(canonicalise_action(call("registry", "provenance", anchor="Learner:sv-0417", fields=("etag",)), call_index=0))
            action = call("progress", "record_mastery", anchor="Learner:sv-0417", concept=card["ask"]["concept"],
                          fields=("receipt_id",), headers={"if-match": prior.get("etag", ""), "idempotency-key": card["id"]})
        elif target == "curriculum-analyst":
            action = call(target, "which_days_cover", concept=card["ask"].get("concept"), fields=("anchor", "course_day"))
        elif target == "citation-checker":
            source = next(a for a in sorted(world._offsets) if a.startswith("Source:"))
            action = call(target, "verify_source", anchor=source, fields=("anchor",))
        else:
            action = call("roster", "lookup_learner", learner="Learner:sv-0417", fields=("act",))
        observed = session.execute(canonicalise_action(action, call_index=0))
        events = [e["p"] for e in session.trace if e["type"] == "mutation"]
        reports.append({"id": card["id"], "class": card["class"], "mutation_applied": any(e.get("applied") for e in events),
                        "observed_ok": observed.get("ok"), "requires_corroboration": observed.get("requires_corroboration", False),
                        "filtered_rows": observed.get("filtered_rows", 0),
                        "error": observed.get("error"), "credits_left": session.ctx.credits})
    return reports


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default="kit/world/df8c55dabb35")
    parser.add_argument("--bot", choices=("you", "rookie", "operator", "adversary", "all"), default="you")
    parser.add_argument("--deck", default="deck/deck.json")
    parser.add_argument("--output", help="Write a reproducible JSON report")
    parser.add_argument("--stress", action="store_true", help="Also enable seeded opaque errors and degraded replicas")
    args = parser.parse_args(argv)
    world = World.load(args.world)
    if world.has_truth: parser.error("Only the public student snapshot is accepted")
    names = ("you", "rookie", "operator", "adversary") if args.bot == "all" else (args.bot,)
    report = {"world_id": world.manifest["world_id"], "suites": {}}
    for name in names:
        gateway = Gateway if name == "you" else importlib.import_module(f"bots.{name}.gateway").Gateway
        report["suites"][name] = run_suite(world, gateway_cls=gateway)
    report["attack_probes"] = probe_deck(world, json.loads(Path(args.deck).read_text(encoding="utf8")))
    if args.stress:
        report["stress_suite"] = run_suite(world, opaque=True, degraded=True)
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    summary = {"world_id": report["world_id"], "suites": {
        name: {k: data[k] for k in ("answered", "abstained", "credits_left", "ask_types")}
        for name, data in report["suites"].items()}, "attack_probes": report["attack_probes"]}
    if args.stress:
        summary["stress_suite"] = {k: report["stress_suite"][k] for k in ("answered", "abstained", "credits_left")}
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
