#!/usr/bin/env python3
"""TradeGuard 합성 서류 렌더러 — benchmark_case JSON → 서류 HTML (→ PNG)

사용법:
    python3 render.py <case.json> [<case2.json> ...] [--out DIR] [--png]

    --out   출력 디렉터리 (기본: ./out)
    --png   Playwright로 A4 크기 PNG까지 생성 (미설치 시 안내 후 HTML만 생성)
            설치: pip install playwright && playwright install chromium

출력 파일명: {case_id}_lc.html / _invoice.html / _bl.html (+ .png)
PNG 생성 시 case JSON의 rendered_files 필드를 갱신한 사본을 out에 저장한다.
"""
import json
import sys
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATES = {
    "lc": ("lc_mt700.html.j2", "letter_of_credit", "lc"),
    "invoice": ("commercial_invoice.html.j2", "commercial_invoice", "inv"),
    "bl": ("bill_of_lading.html.j2", "bill_of_lading", "bl"),
}
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _parse(iso: str) -> date:
    return date.fromisoformat(iso)


def swiftdate(iso):  # 2026-07-15 -> 260715 (SWIFT YYMMDD)
    if not iso:
        return ""
    d = _parse(iso)
    return f"{d.year % 100:02d}{d.month:02d}{d.day:02d}"


def docdate(iso):  # 2026-07-15 -> 15 JUL 2026
    if not iso:
        return ""
    d = _parse(iso)
    return f"{d.day:02d} {MONTHS[d.month - 1]} {d.year}"


def money(v):  # 84000 -> 84,000.00
    return f"{float(v):,.2f}"


def money0(v):  # 4000 -> 4,000
    return f"{float(v):,.0f}"


def build_env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), undefined=StrictUndefined,
                      trim_blocks=False, lstrip_blocks=False)
    env.filters.update(swiftdate=swiftdate, docdate=docdate, money=money, money0=money0)
    return env


def render_case(case_path: Path, out_dir: Path, png: bool) -> dict:
    case = json.loads(case_path.read_text(encoding="utf-8"))
    case_id = case["case_id"]
    env = build_env()
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = {}
    for key, (tpl_name, doc_key, var_name) in TEMPLATES.items():
        doc = case["documents"][doc_key]
        html = env.get_template(tpl_name).render(**{var_name: doc})
        html_path = out_dir / f"{case_id}_{key}.html"
        html_path.write_text(html, encoding="utf-8")
        rendered[key] = html_path
        print(f"  [html] {html_path}")

    if png:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("  [warn] playwright 미설치 — PNG 생략. pip install playwright && playwright install chromium")
            return case
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 794, "height": 1123})
            files = {}
            for key, html_path in rendered.items():
                page.goto(html_path.resolve().as_uri())
                png_path = html_path.with_suffix(".png")
                page.screenshot(path=str(png_path), full_page=True)
                files[f"{key}_image" if key != "invoice" else "invoice_image"] = png_path.name
                print(f"  [png ] {png_path}")
            browser.close()
        case["rendered_files"] = {
            "lc_image": f"{case_id}_lc.png",
            "invoice_image": f"{case_id}_invoice.png",
            "bl_image": f"{case_id}_bl.png",
        }
        (out_dir / f"{case_id}.json").write_text(
            json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    return case


def main():
    args = sys.argv[1:]
    png = "--png" in args
    out = Path("out")
    if "--out" in args:
        i = args.index("--out")
        out = Path(args[i + 1])
        args = args[:i] + args[i + 2:]
    cases = [Path(a) for a in args if not a.startswith("--")]
    if not cases:
        sys.exit("사용법: python3 render.py <case.json> [--out DIR] [--png]")
    for c in cases:
        print(f"[case] {c}")
        render_case(c, out, png)


if __name__ == "__main__":
    main()
