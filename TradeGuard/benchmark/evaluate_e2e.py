#!/usr/bin/env python3
"""PNG → extract → detect end-to-end evaluation.

Reports field-level extraction accuracy separately from discrepancy precision,
recall, F1, and grade accuracy. This is the metric path intended for the final
technical presentation; benchmark/evaluate.py remains the detector-only ceiling.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from detect import build_report, d as parse_date  # noqa: E402
from extract import run as extract_images  # noqa: E402

DOCS = ("letter_of_credit", "commercial_invoice", "bill_of_lading")
SUFFIX = {
    "letter_of_credit": "lc",
    "commercial_invoice": "invoice",
    "bill_of_lading": "bl",
}
IGNORED = {"field_confidence", "unreadable_fields"}


def leaves(value: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key not in IGNORED:
                yield from leaves(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaves(child, f"{path}[{index}]")
    else:
        yield path, value


def lookup(value: Any, path: str) -> Any:
    import re

    current = value
    for name, index in re.findall(r"([^.[]+)(?:\[(\d+)])?", path):
        current = current[name]
        if index:
            current = current[int(index)]
    return current


def equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, str) and isinstance(actual, str):
        return " ".join(expected.split()).casefold() == " ".join(actual.split()).casefold()
    return expected == actual


def field_counts(expected: dict[str, Any], actual: dict[str, Any]) -> tuple[int, int]:
    correct = total = 0
    for path, expected_value in leaves(expected):
        total += 1
        try:
            actual_value = lookup(actual, path)
        except (KeyError, IndexError, TypeError):
            continue
        correct += int(equal(expected_value, actual_value))
    return correct, total


def ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def image_path(case: dict[str, Any], rendered_dir: Path, doc_type: str) -> Path:
    rendered = case.get("rendered_files") or {}
    schema_key = {
        "letter_of_credit": "lc_image",
        "commercial_invoice": "invoice_image",
        "bill_of_lading": "bl_image",
    }[doc_type]
    name = rendered.get(schema_key) or f"{case['case_id']}_{SUFFIX[doc_type]}.png"
    return rendered_dir / name


def evaluate(case_paths: list[Path], rendered_dir: Path) -> dict[str, Any]:
    tp = fp = fn = grade_match = correct_fields = total_fields = 0
    failures: list[dict[str, str]] = []
    case_results: list[dict[str, Any]] = []

    for case_path in case_paths:
        case = json.loads(case_path.read_text(encoding="utf-8"))
        extracted: dict[str, dict[str, Any]] = {}
        case_correct = case_total = 0
        try:
            for doc_type in DOCS:
                path = image_path(case, rendered_dir, doc_type)
                if not path.is_file():
                    raise FileNotFoundError(path)
                value = extract_images([path])
                extracted[doc_type] = value
                correct, total = field_counts(case["documents"][doc_type], value)
                case_correct += correct
                case_total += total
        except (Exception, SystemExit) as exc:
            failures.append({"case_id": case["case_id"], "error": str(exc)[:300]})
            continue

        correct_fields += case_correct
        total_fields += case_total
        report = build_report(
            case["case_id"], extracted, parse_date(case.get("presentation_date"))
        )
        predicted = {item["type"] for item in report["discrepancies"]}
        expected = set(case["defect_types"])
        tp += len(predicted & expected)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
        expected_grade = case["ground_truth"]["overall_risk"]["grade"]
        grade_match += int(report["overall_risk"]["grade"] == expected_grade)
        case_results.append({
            "case_id": case["case_id"],
            "field_accuracy": round(ratio(case_correct, case_total), 4),
            "expected_defects": sorted(expected),
            "predicted_defects": sorted(predicted),
            "expected_grade": expected_grade,
            "predicted_grade": report["overall_risk"]["grade"],
        })

    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    completed = len(case_results)
    return {
        "mode": "png_end_to_end",
        "requested_cases": len(case_paths),
        "completed_cases": completed,
        "failed_cases": len(failures),
        "field_accuracy": round(ratio(correct_fields, total_fields), 4),
        "defect_precision": round(precision, 4),
        "defect_recall": round(recall, 4),
        "defect_f1": round(ratio(2 * precision * recall, precision + recall), 4),
        "grade_accuracy": round(ratio(grade_match, completed), 4),
        "counts": {
            "tp": tp, "fp": fp, "fn": fn,
            "fields_correct": correct_fields, "fields_total": total_fields,
        },
        "failures": failures,
        "case_results": case_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("cases"))
    parser.add_argument("--rendered-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="스모크 테스트용 최대 케이스 수")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    paths = sorted(args.cases.glob("*.json"))
    if args.limit:
        paths = paths[:args.limit]
    if not paths:
        parser.error(f"평가할 케이스가 없습니다: {args.cases}")
    result = evaluate(paths, args.rendered_dir)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"[evaluate_e2e] 저장: {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
