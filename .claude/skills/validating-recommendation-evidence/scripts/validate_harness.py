from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[4]

REQUIRED = [
    "AGENTS.md",
    ".claude/context/project-context.md",
    ".claude/context/data-catalog-contract.md",
    ".claude/context/metric-contract.md",
    ".claude/context/rag-contract.md",
    ".claude/agents/location-data-auditor.md",
    ".claude/agents/location-input-contract-builder.md",
    ".claude/agents/candidate-method-designer.md",
    ".claude/agents/rag-evidence-builder.md",
    ".claude/agents/recommendation-qa-reviewer.md",
    ".claude/skills/seoul-site-recommendation-orchestrator/SKILL.md",
    "artifacts/README.md",
    "artifacts/00-input.md",
    "artifacts/10-analysis/data-usage-classification.md",
    "artifacts/20-method/candidate-selection-spec.md",
    "artifacts/20-method/rag-evidence-schema.json",
    "artifacts/20-method/generated-evidence-schema.json",
    "artifacts/30-review/recommendation-quality-review.md",
    "artifacts/final/recommendation-system-spec.md",
]

AGENTS = {
    "location-data-auditor": "inherit",
    "location-input-contract-builder": "inherit",
    "candidate-method-designer": "inherit",
    "rag-evidence-builder": "inherit",
    "recommendation-qa-reviewer": "inherit",
}

ORCHESTRATOR_TOKENS = [
    "TeamCreate",
    "TaskCreate",
    "TaskUpdate",
    "TaskGet",
    "SendMessage",
    "TeamDelete",
    "stale",
    "사람 승인 필요",
]


def fail(message: str) -> None:
    print(f"FAIL: {message}")


def main() -> int:
    failures = 0
    for relative in REQUIRED:
        if not (ROOT / relative).is_file():
            fail(f"required file missing: {relative}")
            failures += 1

    if (ROOT / ".claude/commands").exists():
        fail("forbidden directory exists: .claude/commands")
        failures += 1

    for name, model in AGENTS.items():
        path = ROOT / ".claude/agents" / f"{name}.md"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for field in (f"name: {name}", "description:", "tools:", f"model: {model}"):
            if field not in text:
                fail(f"{path.relative_to(ROOT)} missing or mismatched field: {field}")
                failures += 1

    orchestrator = ROOT / ".claude/skills/seoul-site-recommendation-orchestrator/SKILL.md"
    if orchestrator.is_file():
        text = orchestrator.read_text(encoding="utf-8")
        for token in ORCHESTRATOR_TOKENS:
            if token not in text:
                fail(f"orchestrator missing contract token: {token}")
                failures += 1

    readme = ROOT / "artifacts/README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8")
        if not re.search(r"current|stale|needs-review|archived", text):
            fail("artifacts README has no file status vocabulary")
            failures += 1
        if "사람 승인 필요" not in text:
            fail("artifacts README has no approval status")
            failures += 1

    if failures:
        print(f"SUMMARY: {failures} failure(s)")
        return 1
    print("PASS: harness structure and core contracts are present")
    return 0


if __name__ == "__main__":
    sys.exit(main())

