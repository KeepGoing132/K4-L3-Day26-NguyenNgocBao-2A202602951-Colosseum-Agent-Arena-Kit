"""Pure gateway policy. All observations must come from the trusted caller."""
from __future__ import annotations

import json
from collections.abc import Mapping

from kit.mcp.a2a import AGENT_CARDS, admit_skill, parse_traceparent, verify_card, verify_delegation
from kit.mcp.servers import _lookup_spec
from kit.mcp.specs import A2A_PEERS, cost_of
from kit.mcp.types import ToolCall
from kit.world.anchor import Anchor, AnchorSyntaxError
from agent.guardrails import scan_for_injected_instructions
from agent.strategy import round_allowance, successor_of


def learner_id(value):
    """Compare learner identities without confusing actor and agent identity."""
    if not isinstance(value, str):
        return None
    namespace, separator, identifier = value.partition(":")
    if separator and namespace.lower() == "learner" and identifier:
        return identifier
    return None


class GatewayPolicy:
    def __init__(self, ctx):
        self.ctx = ctx
        self.cards = dict(AGENT_CARDS)
        self.provenance = {}
        self.tokens = set()
        self.write_keys = set()
        self.write_targets = set()
        self.calls = {}
        self.lease_rounds = {}
        self.round = None
        self.spent = 0

    def note_card(self, server, card):
        """Never trust a caller-supplied 'verified' boolean as a signature."""
        result = verify_card(card)
        if result.admitted and result.peer == server:
            self.cards[server] = card
            return True
        self.cards.pop(server, None)
        return False

    def note_provenance(self, anchor, etag, *, call_index=None):
        if isinstance(anchor, str) and isinstance(etag, str) and etag:
            self.provenance[anchor] = (etag, self.ctx.round, call_index)

    def decide(self, cmd, deny):
        from agent.gateway import Decision

        if self.round != self.ctx.round:
            self.round, self.spent = self.ctx.round, 0
        headers = {str(k).lower(): v for k, v in cmd.headers.items()}
        args = dict(cmd.args)
        server, tool = successor_of(cmd.server, cmd.tool) or (cmd.server, cmd.tool)
        spec = _lookup_spec(server, tool)
        if spec is None:
            return deny(cmd, "unknown server/tool")
        if (cmd.kind == "a2a") != (server in A2A_PEERS):
            return deny(cmd, "command kind does not match MCP/A2A server")
        if any(args.get(k) is not None for k in ("route", "_route", "replica")):
            return deny(cmd, "routing belongs in the header, not the body")
        if headers.get("mcp-replica", "w") not in ("w", "c"):
            return deny(cmd, "invalid replica header")
        if headers.get("x-server-fingerprint") == "unvouched":
            return deny(cmd, "server identity is not vouched for")
        if headers.get("x-card-signature") == "invalid":
            return deny(cmd, "invalid peer card signature")

        actor = learner_id(self.ctx.act)
        if actor is None:
            return deny(cmd, "context has no authenticated learner act")
        for key in ("learner", "learner_id", "act"):
            if key in args and learner_id(args[key]) != actor:
                return deny(cmd, "target learner differs from ctx.act")
        for key in ("anchor", "target", "subject"):
            target = args.get(key)
            if isinstance(target, str) and target.lower().startswith("learner:"):
                if learner_id(target) != actor:
                    return deny(cmd, "learner anchor differs from ctx.act")
        if "wiki.read" not in self.ctx.scopes and not spec.is_write:
            return deny(cmd, "missing wiki.read scope")
        # Inspect instruction-bearing content, not search terms or lesson titles.
        for key in ("note", "body", "instruction", "instructions"):
            if isinstance(args.get(key), str) and scan_for_injected_instructions(args[key]).suspicious:
                return deny(cmd, "instruction found in retrieved content")

        token_id = None
        if server in A2A_PEERS:
            card = self.cards.get(server)
            skill = args.get("skill") or ("who_enrolled" if (server, tool) == ("roster", "lookup_learner") else tool)
            if card is None or not admit_skill(card, skill).admitted:
                return deny(cmd, "peer card or declared skill not admitted")
            audience = headers.get("aud")
            if audience is not None and audience not in (server, f"a2a:{server}"):
                return deny(cmd, "delegation audience differs from called peer")
            token = headers.get("delegation") or args.get("delegation")
            if isinstance(token, str):
                try:
                    token = json.loads(token)
                except (TypeError, ValueError):
                    return deny(cmd, "malformed delegation token")
            admission = verify_delegation(token or {}, aud=f"a2a:{server}",
                                          expected_act=self.ctx.act, call_index=cmd.call_index,
                                          seen_token_ids=self.tokens)
            if not admission.admitted:
                return deny(cmd, f"delegation denied: {admission.reason.value}")
            token_id = token.token_id if hasattr(token, "token_id") else token["token_id"]
        if "traceparent" in headers:
            try:
                parse_traceparent(headers["traceparent"])
            except (TypeError, ValueError):
                return deny(cmd, "invalid traceparent")

        if spec.needs_lease:
            if not cmd.lease_id or cmd.lease_id not in self.ctx.leases:
                return deny(cmd, "frame read requires a live lease")
            previous_round = self.lease_rounds.setdefault(cmd.lease_id, self.ctx.round)
            if previous_round != self.ctx.round:
                return deny(cmd, "lease cannot be reused across rounds")
        anchor = args.get("anchor")
        if isinstance(anchor, str):
            try:
                parsed_anchor = Anchor.parse(anchor)
            except AnchorSyntaxError:
                return deny(cmd, "invalid target anchor")
            if parsed_anchor.rev and tool in ("get_frame", "provenance"):
                if headers.get("mcp-replica", parsed_anchor.rev) != parsed_anchor.rev:
                    return deny(cmd, "replica header disagrees with target anchor")
                headers["mcp-replica"] = parsed_anchor.rev
        if spec.is_write:
            if f"wiki.write:{server}" not in self.ctx.scopes:
                return deny(cmd, "missing write scope")
            if not isinstance(anchor, str) or not anchor:
                return deny(cmd, "write requires an explicit target anchor")
            if (server, tool) == ("progress", "record_mastery") and learner_id(anchor) != actor:
                return deny(cmd, "write target is not the learner in ctx.act")
            if not all(isinstance(headers.get(k), str) and headers[k].strip()
                       for k in spec.required_headers):
                return deny(cmd, "write requires If-Match and Idempotency-Key")
            etag = self.provenance.get(anchor)
            if not etag or etag[:2] != (headers["if-match"], self.ctx.round):
                return deny(cmd, "write requires provenance verified this round")
            key = headers["idempotency-key"]
            concept = args.get("concept")
            if concept is not None and not isinstance(concept, str):
                return deny(cmd, "invalid write concept")
            target = (server, tool, anchor, concept)
            if key in self.write_keys or target in self.write_targets:
                return deny(cmd, "write already authorized; reconcile before another attempt")

        if spec.rate_limit:
            limit, window = spec.rate_limit
            recent = [r for r in self.calls.get((server, tool), [])
                      if r >= self.ctx.round - window + 1]
            if len(recent) >= limit:
                return deny(cmd, "tool rate window exhausted")
        fields = cmd.fields or spec.default_fields
        if "*" in fields:
            if fields != ("*",):
                return deny(cmd, "wildcard cannot be mixed with named fields")
            fields = spec.default_fields
        # Catalog defaults are the expensive mask; use just identifiers.
        if (server, tool) == ("registry", "list_servers") and (not cmd.fields or cmd.fields == ("*",)):
            fields = ("name",)
        if (server, tool) == ("glossary", "list_terms") and (not cmd.fields or cmd.fields == ("*",)):
            fields = ("term",)
        if (cmd.server, cmd.tool) == ("slides", "search"):
            fields = tuple("body" if f == "snippet" else f for f in fields if f != "anchor") or spec.default_fields
        if not set(fields) <= set(spec.all_fields):
            return deny(cmd, "mask includes an unsupported field")
        if tool == "get_frame" and isinstance(anchor, str) and anchor.startswith("Note:"):
            fields = tuple(sorted(set(fields) | {"meta"}))
        try:
            rows = int(args.get("limit", 1))
        except (TypeError, ValueError, OverflowError):
            return deny(cmd, "invalid row limit")
        if rows < 1 or rows > 50:
            return deny(cmd, "row limit must be between 1 and 50")
        if (server, tool) in (("slides", "query"), ("research", "search"),
                              ("registry", "list_servers"), ("glossary", "list_terms")):
            args["limit"] = min(rows, 3)
            rows = args["limit"]
        else:
            rows = 1
        call = ToolCall(server=server, tool=tool, args=args, fields=tuple(sorted(set(fields))),
                        headers=headers, lease_id=cmd.lease_id, call_index=cmd.call_index)
        price = cost_of(call, n_rows=rows)
        # Reserve sustainable credits for the remaining rounds; actual credits win.
        allowance = round_allowance(int(self.ctx.credits), self.ctx.round)
        if price > self.ctx.credits or self.spent + price > allowance:
            return deny(cmd, "call exceeds remaining credits or round allowance")
        self.spent += price
        self.calls.setdefault((server, tool), []).append(self.ctx.round)
        if token_id is not None:
            self.tokens.add(token_id)
        if spec.is_write:
            self.write_keys.add(key)
            self.write_targets.add(target)
        changed = (server, tool, args, call.fields, headers) != (
            cmd.server, cmd.tool, cmd.args, cmd.fields, cmd.headers)
        return Decision(verdict="rewrite" if changed else "forward", call=call)
