import argparse
import json
from pathlib import Path
import time
import unittest


class JSONResult(unittest.TextTestResult):
    def __init__(self, *arguments, **keywords):
        super().__init__(*arguments, **keywords)
        self.outcomes = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes.append({"test": test.id(), "status": "passed"})

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.outcomes.append({"test": test.id(), "status": "failed"})

    def addError(self, test, error):
        super().addError(test, error)
        self.outcomes.append({"test": test.id(), "status": "error"})

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcomes.append({"test": test.id(), "status": "skipped", "reason": reason})


def main():
    parser = argparse.ArgumentParser(description="Run the stdlib controller tests with a bounded JSON summary.")
    parser.add_argument("--output", type=Path, default=Path("test-results/unit-summary.json"))
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py", top_level_dir=str(root))
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2, resultclass=JSONResult).run(suite)
    summary = {
        "status": "PASS" if result.wasSuccessful() else "FAIL",
        "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "seconds": round(time.monotonic() - started, 3),
        "outcomes": result.outcomes,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
