"""Local integration with the real kit loop and tool stack.

This is a practice harness, not a replacement for the tournament's trusted arena.
Gateway and tool state persist across exchanges; answers are checked before delivery.
"""
from __future__ import annotations
import json
from collections.abc import Mapping
from dataclasses import replace

from agent.gateway import Command, Gateway
from agent.guardrails import scan_for_injected_instructions, validate_answer
from agent.safety import anchor_base
from agent.telemetry import RecordingGatewayContext
from kit.loop.agent import Agent, RunResult
from kit.loop.limits import LoopTerminated
from kit.loop.prompt import render_system_prompt
from kit.mcp.a2a import AGENT_CARDS, execute as execute_a2a, mint_delegation, verify_card, verify_delegation
from kit.mcp.hardmode import HardMode
from kit.mcp.mutations import MutableStack
from kit.mcp.specs import MCP_SERVERS, A2A_PEERS


class PublicToolStack(MutableStack):
    """Use public A2A executors when the student world intentionally has no truth.

    The kit's mutation stack uses oracle-backed A2A helpers. The public executors
    resolve course links instead. Mutation application remains the original kit code.
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._native_tokens = set()

    def _execute_a2a(self, call):
        token = call.headers.get("delegation") or {}
        result = execute_a2a(self.world, call, expected_act=self.act, delegation=token,
                             seen_token_ids=self._native_tokens, hardmode=self.hardmode)
        if result.get("ok") and isinstance(token, Mapping) and token.get("token_id"):
            self._native_tokens.add(token["token_id"])
        return result


class DefenderSession:
    def __init__(self, world, *, act="learner:sv-0417", scopes=("wiki.read",),
                 credits=100, duel_id="local-practice", hardmode=True, gateway_cls=Gateway,
                 opaque=False, degraded=False):
        self.ctx = RecordingGatewayContext(act, "agent:vlearn-tutor", frozenset(scopes), credits, 0, 0)
        self.gateway = gateway_cls(self.ctx)
        if hasattr(self.gateway, "note_card"):
            for peer, card in AGENT_CARDS.items():
                verified = dict(card.to_dict(), verified=verify_card(card).admitted)
                self.gateway.note_card(peer, verified)
        mode = HardMode(world=world, opaque_enabled=opaque, degraded_replica_enabled=degraded) if hardmode else None
        if mode:
            mode.reset(duel_id, world_id=world.manifest.get("world_id", ""))
        self.stack = PublicToolStack(world=world, act=act, sub=self.ctx.sub, hardmode=mode)
        self.trace = []
        self._seq = 0
        self._leases = {}
        self._result_tokens = set()
        self._seen_cards = {}
        self.retrieved = set()
        self.private_snippets = []
        self.ask = {}
        self.delivered_answer = None

    def _emit(self, event_type, **payload):
        event = {"v": 1, "layer": 1, "producer": "arena", "type": event_type,
                 "seq": self._seq, "round": self.ctx.round, "p": payload}
        self._seq += 1
        self.trace.append(event)

    def begin_exchange(self, ask, *, round_no, card=None):
        self.ctx.round = round_no
        self.ctx.call_index = 0
        self.ctx.leases = ()
        self._leases.clear()
        self.retrieved.clear()
        self.private_snippets.clear()
        self.ask = dict(ask)
        self.delivered_answer = None
        self.trace = []
        self._seq = 0
        if self.stack.hardmode:
            self.stack.hardmode.begin_round(round_no)
        self.stack.arm(card if card and card.get("kind") == "attack" else None)
        self._emit("exchange_start", ask=self.ask, act=self.ctx.act,
                   scopes=sorted(self.ctx.scopes), card_id=(card or {}).get("id"))

    def execute(self, action):
        if action.get("kind") == "answer":
            checked = validate_answer(action.get("args") or {}, self.retrieved,
                                      ask=self.ask, private_snippets=self.private_snippets)
            self.delivered_answer = checked.answer
            self._emit("answer", **checked.answer)
            return {"ok": checked.accepted, "answer": checked.answer,
                    "issues": list(checked.issues), "cost": 0, "credits_left": self.ctx.credits}
        outgoing = dict(action)
        outgoing["headers"] = dict(action.get("headers") or {})
        # Credential issuance belongs to the trusted environment, never the model.
        if action.get("kind") == "a2a" and "delegation" not in outgoing["headers"]:
            outgoing["headers"]["delegation"] = mint_delegation(
                act=self.ctx.act, sub=self.ctx.sub, aud=f"a2a:{action['server']}",
                ttl=3, call_index=self.ctx.call_index, nonce=self.ctx.round).to_dict()
        outgoing["call_index"] = self.ctx.call_index
        self.ctx.leases = tuple(key for key, index in self._leases.items()
                                if self.ctx.call_index - index <= 3)
        cmd = Command.from_action_dict(outgoing, cmd_id=f"cmd:{self.ctx.call_index:04d}")
        self.ctx.call_index += 1
        self._emit("command", **cmd.to_dict())
        decision = self.gateway.decide(cmd)
        self._emit("decision", **decision.to_dict())
        if decision.verdict == "deny":
            result = {"ok": False, "error": {"code": "unauthorized", "reason": decision.reason}, "cost": 0}
            self._emit("enforced", verdict_applied="deny", charged=0, reason=decision.reason)
        else:
            call = decision.call
            result, mutation = self.stack.execute(call)
            if mutation:
                self._emit("mutation", **mutation)
            self.ctx.credits -= result.get("cost", 0)
            self._emit("enforced", verdict_applied=decision.verdict, charged=result.get("cost", 0))
            self._emit("tool_call", **call.to_dict(), mask=list(call.fields), cost=result.get("cost", 0),
                       credits_left=self.ctx.credits, lease_used=call.lease_id)
            recorded = dict(result)
            recorded["error_code"] = (result.get("error") or {}).get("code")
            self._emit("tool_result", **recorded)
            if result.get("ok"):
                lease = result.get("lease_id")
                if lease:
                    self._leases[lease] = cmd.call_index
                result = self._filter_result(call, result)
                if result.get("ok") and (call.server, call.tool) == ("registry", "provenance"):
                    etag = result.get("etag")
                    if etag and hasattr(self.gateway, "note_provenance"):
                        self.gateway.note_provenance(call.args.get("anchor"), etag)
        self.ctx.history += ((cmd, decision, dict(result)),)
        result = dict(result)
        result["credits_left"] = self.ctx.credits
        return result

    def _filter_result(self, call, result):
        requested = call.args.get("anchor")
        if call.tool in ("get_frame", "provenance") and isinstance(requested, str):
            anchors = result.get("anchors") or []
            if requested not in anchors:
                return {"ok": False, "error": {"code": "conflict", "reason": "result anchor differs from requested anchor"}, "cost": result.get("cost", 0)}
            parts = anchor_base(requested).split("/")
            expected_rev = parts[1] if len(parts) > 1 and parts[1] in ("w", "c") else None
            if expected_rev and result.get("replica") not in (None, expected_rev):
                return {"ok": False, "error": {"code": "conflict", "reason": "result replica differs from anchor revision"}, "cost": result.get("cost", 0)}
        safe_rows = []
        for row in result.get("rows") or []:
            if not isinstance(row, Mapping): continue
            if (call.server, call.tool) == ("registry", "list_servers") and row.get("name") not in MCP_SERVERS | A2A_PEERS:
                continue
            if call.server == "research" and isinstance(row.get("anchor"), str) and not row["anchor"].startswith("Source:"):
                continue
            token = row.get("delegation_token")
            if token is not None:
                admission = verify_delegation(token, aud=f"a2a:{call.server}",
                    call_index=call.call_index, expected_act=self.ctx.act,
                    seen_token_ids=self._result_tokens)
                if not admission.admitted:
                    continue
                self._result_tokens.add(token["token_id"])
            if row.get("agent_card") is not None:
                admission = verify_card(row["agent_card"])
                if not admission.admitted or admission.peer != call.server:
                    continue
            meta = row.get("meta") or {}
            private = row.get("private") is True or (isinstance(meta, Mapping) and meta.get("private") is True)
            if private:
                self.private_snippets.append(str(row.get("body", "")))
                continue
            if scan_for_injected_instructions(json.dumps(row, ensure_ascii=False)).suspicious:
                continue
            safe_rows.append(dict(row))
        if result.get("rows") and not safe_rows:
            return {"ok": False, "error": {"code": "unavailable", "reason": "no safe source rows"}, "cost": result.get("cost", 0)}
        filtered = dict(result)
        filtered["rows"] = safe_rows
        filtered["filtered_rows"] = len(result.get("rows") or []) - len(safe_rows)
        # Filtering any row makes the envelope's anchor-to-row mapping ambiguous.
        if len(safe_rows) != len(result.get("rows") or []):
            filtered["anchors"] = [r["anchor"] for r in safe_rows if isinstance(r.get("anchor"), str)]
        if call.server in ("curriculum-analyst", "citation-checker", "roster"):
            filtered["requires_corroboration"] = True
        else:
            self.retrieved.update(filtered.get("anchors") or [])
            self.retrieved.update(r["anchor"] for r in safe_rows if isinstance(r.get("anchor"), str))
        return filtered

    def run_exchange(self, model, ask, *, round_no, card=None, strategy_prompt=""):
        from agent.solver import normalize_ask
        ask = normalize_ask(ask)
        self.begin_exchange(ask, round_no=round_no, card=card)
        loop = Agent(model, self, system_prompt=render_system_prompt() + "\n" + strategy_prompt,
                     task=json.dumps(ask, ensure_ascii=False))
        try:
            result = loop.run()
        except LoopTerminated as exc:
            # Preserve the actual termination status and a safe final record.
            # This is not reported as a normally answered exchange.
            self.delivered_answer = {"text": "Chưa đủ căn cứ đã xác minh; đã hết giới hạn của lượt đấu.",
                                     "cited_anchors": [], "abstained": True}
            self._emit("answer", **self.delivered_answer)
            self._emit("exchange_end", reason=exc.exit_status)
            result = RunResult(exc.exit_status, loop._steps, loop._calls, loop.elapsed(),
                               self.delivered_answer, tuple(loop.messages), loop.observations)
        # kit.loop returns the original action; return the checked, delivered answer.
        messages = [dict(message) for message in result.messages]
        for message in reversed(messages):
            if message.get("role") == "assistant":
                message["content"] = "```action\nANSWER " + json.dumps(self.delivered_answer, ensure_ascii=False) + "\n```"
                break
        return replace(result, answer=self.delivered_answer, messages=tuple(messages))
