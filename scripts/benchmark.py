"""检测基准评估：recall/precision/F1（含 holdout 防过拟合）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay_gateway.config import PrivacyConfig
from relay_gateway.privacy import PrivacyEngine


def load_cases(path: Path) -> list[dict]:
    cases = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def evaluate(engine: PrivacyEngine, cases: list[dict]) -> dict:
    per_category: dict[str, dict] = {}
    total = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for case in cases:
        cat = str(case.get("expected_category", "?"))
        expected = bool(case.get("expected_sensitive", True))
        detected = engine.scan_text(str(case.get("text", ""))).sensitive
        row = per_category.setdefault(cat, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
        if expected and detected:
            row["tp"] += 1
            total["tp"] += 1
        elif expected and not detected:
            row["fn"] += 1
            total["fn"] += 1
        elif not expected and detected:
            row["fp"] += 1
            total["fp"] += 1
        else:
            row["tn"] += 1
            total["tn"] += 1
    return {"per_category": per_category, "total": total}


def _rate(n: int, d: int) -> float:
    return n / d if d else 1.0


def print_report(name: str, stats: dict) -> None:
    total = stats["total"]
    recall = _rate(total["tp"], total["tp"] + total["fn"])
    precision = _rate(total["tp"], total["tp"] + total["fp"])
    f1 = 2 * recall * precision / (recall + precision) if (recall + precision) else 0.0
    print(f"\n== {name} ==")
    print(f"{'category':<16}{'cases':>6}{'TP':>5}{'FN':>5}{'FP':>5}{'recall':>8}{'precision':>10}")
    for cat in sorted(stats["per_category"]):
        r = stats["per_category"][cat]
        n = r["tp"] + r["fn"] + r["fp"] + r["tn"]
        rec = _rate(r["tp"], r["tp"] + r["fn"])
        prec = _rate(r["tp"], r["tp"] + r["fp"])
        print(f"{cat:<16}{n:>6}{r['tp']:>5}{r['fn']:>5}{r['fp']:>5}{rec:>8.3f}{prec:>10.3f}")
    print(f"OVERALL recall={recall:.3f} precision={precision:.3f} f1={f1:.3f} (tp={total['tp']} fn={total['fn']} fp={total['fp']})")
    stats["_overall"] = {"recall": recall, "precision": precision, "f1": f1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--holdout", type=Path, default=None)
    parser.add_argument("--vaults", default="C:\\Users\\alice\\my-vault,/home/alice/my-vault")
    parser.add_argument("--recall", type=float, default=0.85)
    parser.add_argument("--precision", type=float, default=0.90)
    args = parser.parse_args()

    engine = PrivacyEngine(
        PrivacyConfig(
            enabled=True,
            mode="safe_route",
            vault_paths=[v for v in args.vaults.split(",") if v],
            keywords=[],
        )
    )
    cases = load_cases(args.cases)
    stats = evaluate(engine, cases)
    print_report("cases", stats)

    hold_stats = None
    if args.holdout and args.holdout.exists():
        hold_stats = evaluate(engine, load_cases(args.holdout))
        print_report("holdout", hold_stats)

    recall = stats["_overall"]["recall"]
    precision = stats["_overall"]["precision"]
    ok = recall >= args.recall and precision >= args.precision
    print(f"\nTHRESHOLD recall>={args.recall} precision>={args.precision} -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
