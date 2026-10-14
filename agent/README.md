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
| `solver.py` | Plans and structured answers for all eight closed ask types |
| `practice.py` | Glossary mode, full suite, or a single ask JSON file |
| `benchmark.py` | Real-tool exercises across gateway profiles and ten attack probes |
| `check.ps1` | Complete Linux checks from Windows through WSL |
| `prompt.md` | Model strategy layered over the harness grammar |
| `telemetry.py` | Private diagnostic events, never scoring evidence |

## Windows / PowerShell

For the full suite on Windows, use Ubuntu 24.04 through WSL. One-time setup:

```powershell
wsl -d Ubuntu-24.04 --cd /mnt/e/D/learn_AI/LAB/Track2/Day26-Colosseum-Agent-Arena-Kit -- python3 -m venv --copies .venv/wsl
wsl -d Ubuntu-24.04 --cd /mnt/e/D/learn_AI/LAB/Track2/Day26-Colosseum-Agent-Arena-Kit -- .venv/wsl/bin/python -m pip install pytest
wsl -d Ubuntu-24.04 --cd /mnt/e/D/learn_AI/LAB/Track2/Day26-Colosseum-Agent-Arena-Kit -- unlink .venv/wsl/lib64
./agent/check.ps1
```

The last setup step removes only the optional Linux `lib64 -> lib` alias created by
venv: Python on Windows cannot stat that WSL symlink when the kit's source gate
walks the workspace. Adjust the WSL workspace path to your checkout.

For individual application checks, use the Windows Python 3.12 virtual environment:

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_student_solution.py tests/test_prosecute.py -q
./.venv/Scripts/python.exe -m agent.practice --suite
./.venv/Scripts/python.exe -m agent.practice --ask-file ask.json --allow-writes
./.venv/Scripts/python.exe -m agent.benchmark --bot all --output eval/benchmark-report.json
./.venv/Scripts/python.exe validate_deck.py deck/deck.json deck/lineup.json --world kit/world/df8c55dabb35
./.venv/Scripts/python.exe -m kit.submit --team NguyenNgocBao-2A202602951
```

The world release is downloaded locally and ignored by Git. Its SHA256 was checked;
the student artifact has 12,375 pages and no `truth.json`.

## Observations and integration

The gateway can read successful provenance from the arena-provided ctx.history.
The trusted environment can also call note_provenance(anchor, etag) after a
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

`CourseModel` implements all eight ask types with bounded tool plans and one optional retry for opaque read errors. It reads only the observations
returned by the tool runtime. The setup supplies the publicly distributed drift
record for current_version_of; both Deck anchors are still verified through registry.provenance this exchange.
The response explicitly names drift.json as its index source. Working is the
active snapshot convention when measured drift exists; an explicit published
fresher field overrides that convention. This is not an independently measured
edit timestamp.

Source-of handles Frame-to-Deck and Claim-to-source-frame relationships.
Contradiction-between reads the Talk discussion and, when necessary, its next
post. Definitions respect explicit senses. Progress writes require a granted scope,
matching learner, fresh provenance and a successful receipt. Ambiguous or unsafe
requests receive an explicit abstention rather than a fabricated fact.

The kit's MutableStack A2A helpers expect truth.json. The local PublicToolStack
uses the kit's public authenticated A2A executors instead, while retaining the
original mutation engine. Peer results are marked as leads requiring MCP
corroboration; they cannot establish independently verified citations by themselves.

`agent.practice --suite` runs ten exchanges covering all eight types on the real
student snapshot, with explicit progress-write authorization. The default mode
still supports ten glossary exchanges. `--ask-file` runs a supplied JSON ask;
writes are refused unless `--allow-writes` is selected. `DefenderSession.run_exchange`
also accepts any model implementing the kit query interface.

`agent.benchmark` compares the same CourseModel across the four gateway profiles,
then forces each of the ten attack targets through actual tools. Results are
conformance and mutation probes, not tournament win rates or hidden referee grades.
The original spar.py remains unchanged and simplified.

## Limits

Pattern matching does not prove semantic truth. Arithmetic checks verify explicit
basic equations, not whether a bare statistic is supported by a source. Revision
choice uses an explicit freshness signal when supplied; drift alone does not tell
which replica is fresher. These checks supplement grounded reasoning.

The native Windows test invocation has a pre-existing platform incompatibility in
`test_run_probe_vectors_unsandboxed_baseline`: the immutable kit calls `msvcrt.open`,
which Windows does not export. The complete check driver runs an identical temporary copy on native Ubuntu storage
so WSL mounted-file latency does not consume the 20-second exchange deadline;
this avoids changing the sealed kit, bots or fixtures.
