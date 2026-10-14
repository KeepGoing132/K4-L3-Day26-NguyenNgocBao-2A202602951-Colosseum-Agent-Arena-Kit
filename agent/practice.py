"""Run real glossary exchanges through the gateway, loop and answer checks.

Usage: python -m agent.practice --rounds 10
This scripted model quotes retrieved definitions. It does not emulate an LLM or
claim to solve all tournament ask types.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from agent.runtime import DefenderSession
from kit.world.loader import World


TERMS = ("mcp registry", "agent card", "traceparent header", "delegation depth",
         "streamable http transport", "mcp registry", "agent card", "traceparent header",
         "delegation depth", "streamable http transport")


class DefinitionModel:
    """A reproducible two-turn policy using only the actual tool observation."""
    def __init__(self, term):
        self.term = term
        self.called = False

    def query(self, messages):
        if not self.called:
            self.called = True
            action = f"MCP glossary.define term={json.dumps(self.term)} lang=vi fields=definition,sense"
        else:
            tool_message = next((m for m in reversed(messages) if m["role"] == "user"), {})
            result = json.loads(tool_message.get("content", "{}"))
            rows, anchors = result.get("rows") or [], result.get("anchors") or []
            if result.get("ok") and rows and anchors and rows[0].get("definition"):
                answer = {"text": str(rows[0]["definition"]), "definition": rows[0]["definition"],
                          "sense": rows[0].get("sense"), "anchor": anchors[0], "cited_anchors": [anchors[0]]}
            else:
                answer = {"text": "Chưa đủ căn cứ đã xác minh để định nghĩa thuật ngữ này.",
                          "cited_anchors": [], "abstained": True}
            action = "ANSWER " + json.dumps(answer, ensure_ascii=False)
        return {"role": "assistant", "content": "```action\n" + action + "\n```"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default="kit/world/df8c55dabb35")
    parser.add_argument("--term", help="Use the same term in each glossary round")
    parser.add_argument("--suite", action="store_true", help="Run all eight ask types with real tools")
    parser.add_argument("--ask-file", help="Read one ask JSON object from a UTF-8 file")
    parser.add_argument("--allow-writes", action="store_true", help="Grant wiki.write:progress for explicit mastery requests")
    parser.add_argument("--rounds", type=int, choices=range(1, 11), default=10)
    args = parser.parse_args(argv)
    world = World.load(args.world)
    if world.has_truth:
        parser.error("Practice requires a student world without truth.json")
    scopes = ("wiki.read", "wiki.write:progress") if args.allow_writes else ("wiki.read",)
    if args.suite:
        from agent.benchmark import run_suite
        print(json.dumps(run_suite(world), ensure_ascii=True, indent=2))
        return 0
    session = DefenderSession(world, scopes=scopes)
    if args.ask_file:
        from agent.solver import CourseModel
        ask = json.loads(Path(args.ask_file).read_text(encoding="utf8"))
        model = CourseModel(ask, drift_record=world.drift_info(ask.get("path_id", "")))
        result = session.run_exchange(model, ask, round_no=1)
        print(json.dumps(result.answer, ensure_ascii=True, indent=2))
        return 0
    prompt = Path(__file__).with_name("prompt.md").read_text(encoding="utf8")
    outcomes = []
    for index in range(args.rounds):
        term = args.term or TERMS[index]
        ask = {"type": "define_term", "term": term, "require": ["definition", "anchor"]}
        result = session.run_exchange(DefinitionModel(term), ask, round_no=index + 1, strategy_prompt=prompt)
        outcomes.append({"round": index + 1, "term": term, "answer": result.answer,
                         "credits_left": session.ctx.credits, "calls": result.calls,
                         "steps": result.steps, "exit_status": result.exit_status})
    print(json.dumps({"world_id": world.manifest["world_id"], "rounds": outcomes,
                      "credits_left": session.ctx.credits}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
