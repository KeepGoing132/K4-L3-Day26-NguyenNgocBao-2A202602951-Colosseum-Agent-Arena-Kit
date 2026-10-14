"""Check an identical temporary copy on native Linux storage.

WSL's mounted Windows filesystem is too slow for the kit's repeated indexed
reads under a 20-second exchange deadline. No kit code or test is patched.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    if sys.platform != "linux":
        raise SystemExit("Run this check driver through Ubuntu WSL: ./agent/check.ps1")
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="colosseum-check-") as directory:
        scratch = Path(directory)
        workspace = scratch / "repo"
        shutil.copytree(source, workspace, ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache", "runs", "*.bundle", "*.local.json", ".env"))
        environment = scratch / "venv"
        shutil.copytree(source / ".venv" / "wsl", environment, ignore=shutil.ignore_patterns("__pycache__"))
        interpreter = str(environment / "bin" / "python")
        commands = [
            ["-m", "kit.gate_no_key"],
            ["-m", "pytest", "tests/", "-q"],
            ["validate_deck.py", "deck/deck.json", "deck/lineup.json", "--world", "kit/world/df8c55dabb35"],
            ["-m", "eval.prosecute"],
            ["-m", "agent.benchmark", "--bot", "all", "--stress", "--output", "eval/benchmark-report.json"],
        ]
        results = []
        for command in commands:
            print("CHECK " + " ".join(command), flush=True)
            completed = subprocess.run([interpreter, *command], cwd=workspace,
                                       capture_output=True, text=True)
            print(completed.stdout, end="", flush=True)
            if completed.stderr: print(completed.stderr, file=sys.stderr, end="", flush=True)
            results.append({"command": command, "exit_code": completed.returncode})
            if completed.returncode: return completed.returncode
            if command[1] == "pytest":
                summary = re.search(r"\d+ passed[^\n]*", completed.stdout)
                results[-1]["summary"] = summary.group() if summary else completed.stdout.splitlines()[-1]
        shutil.copyfile(workspace / "eval" / "benchmark-report.json", source / "eval" / "benchmark-report.json")
        report = {"platform": "Ubuntu WSL / native Linux temporary workspace", "python": sys.version.split()[0],
                  "protected_sources_modified": False, "checks": results}
        (source / "eval" / "validation-run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
