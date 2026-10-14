# Defender implementation

The tournament entry point remains `Gateway(ctx).decide(cmd) -> Decision`.
It is synchronous, performs no I/O, and preserves state across a duel.

| File | Responsibility |
|---|---|
| `gateway.py` | Command/Decision types, gateway entry point, telemetry |
| `policy.py` | Actor/scope authorization, signed cards and delegation, live leases, write preconditions, rate windows and budget |
| `strategy.py` | Cost helpers, replica selection, round pacing and optional cache/delegation helpers |
| `guardrails.py` / `safety.py` | Citation validation, injection patterns, private excerpt redaction, exact arithmetic and checked answers |
| `runtime.py` | Local practice integration with the real kit loop and MutableStack |
| `practice.py` | Reproducible glossary exchanges using retrieved definitions |
| `prompt.md` | Model strategy layered over the harness grammar |
| `telemetry.py` | Private diagnostic events, never scoring evidence |

## Windows / PowerShell

Use Python 3.12 in the workspace virtual environment:

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_student_solution.py tests/test_prosecute.py -q
./.venv/Scripts/python.exe -m agent.practice --rounds 10
./.venv/Scripts/python.exe validate_deck.py deck/deck.json deck/lineup.json --world kit/world/df8c55dabb35
./.venv/Scripts/python.exe -m kit.submit --team YOUR-TEAM
```

The world release is downloaded locally and ignored by Git. Its SHA256 was checked;
the student artifact has 12,375 pages and no `truth.json`.

## Observations and integration

Only the trusted environment should call `note_provenance(anchor, etag)` after a
successful registry.provenance call. `note_card(peer, card)` verifies the actual
registry signature; a dict claiming `verified: true` does not admit a peer.
A2A commands require a signed delegation for the actual peer and current actor.
The local runtime issues credentials before passing commands through the gateway,
then also verifies credentials or cards returned in tool results.

The runtime keeps one gateway, tool stack and credit pool across exchanges.
Retrieved anchors and leases reset each round. Unsafe/private result rows are
filtered before observation; ANSWER validation checks retrieved citations, required
fields and explicit arithmetic, then redacts private excerpts. Invalid answers
become explicit abstentions. The delivered trace, returned answer and final message
all contain the checked answer.

`agent.practice` exercises real glossary retrieval and final-answer handling, with
no model API. It is a two-turn scripted model, not a complete tournament agent.
`DefenderSession.run_exchange` accepts any model implementing the kit's query
interface. The original `spar.py` remains a simplified simulation and does not
exercise this runtime or these final-answer checks.

## Limits

Pattern matching does not prove semantic truth. Arithmetic checks verify explicit
basic equations, not whether a bare statistic is supported by a source. Revision
choice uses an explicit freshness signal when supplied; drift alone does not tell
which replica is fresher. These checks supplement grounded reasoning.

The full test suite has a pre-existing Windows failure in
`test_run_probe_vectors_unsandboxed_baseline`: the immutable kit calls `msvcrt.open`,
which Windows does not export. The kit, bots and fixtures remain unchanged.
