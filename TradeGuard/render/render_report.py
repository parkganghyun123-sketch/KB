#!/usr/bin/env python3
"""하자 리포트 실데이터 렌더러 — detect.py 출력 → 화면3 HTML

목업(screen3_discrepancy_report.html)은 값이 하드코딩돼 있다.
이 스크립트는 **실제 검출 결과**로 같은 화면을 생성한다. 즉 데모에서 보는 화면이
실제 파이프라인의 산출물임을 증명할 수 있다.

사용법:
  python3 render/render_report.py benchmark/cases/DEFECT-011.json \\
          --out mockups/screen3_live.html
  python3 render/render_report.py report.json --out out.html   # detect --out 결과도 입력 가능
"""
import html
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
from detect import build_report, d as parse_date  # noqa: E402

GRADE_LABEL = {"A": ("하자가 발견되지 않았습니다", "안전", "ok"),
               "B": ("경미한 하자가 있습니다", "주의", "warn"),
               "C": ("지급거절 가능성이 있습니다", "위험", "danger"),
               "D": ("지급거절 가능성이 높습니다", "고위험", "danger")}
DOC_LABEL = {"letter_of_credit": "Letter of Credit", "commercial_invoice": "Commercial Invoice",
             "bill_of_lading": "Bill of Lading", "_input": "Input"}

CSS = """
:root{--kb-yellow:#FFBC00;--ink:#26282c;--sub:#696e76;--danger:#d92d20;--danger-bg:#fef0ef;
--warn:#b54708;--warn-bg:#fffaeb;--ok:#067647;--ok-bg:#ecfdf3;--blue:#175cd3;--blue-bg:#eff6ff;
--line:#e4e6ea;--card:#fff;--bg:#f6f7f8}
*{box-sizing:border-box;margin:0}
body{width:1280px;margin:0 auto;background:var(--bg);color:var(--ink);
font-family:"Pretendard","Apple SD Gothic Neo","Malgun Gothic",sans-serif}
.topbar{background:#fff;border-bottom:1px solid var(--line);padding:14px 40px;display:flex;align-items:center;gap:12px}
/* 자체 워드마크 — KB 브랜드 자산(심볼·로고타입)은 사용 권한 미확인으로 사용하지 않는다 */
.wordmark{font-size:19px;font-weight:800;letter-spacing:-.5px}
.wordmark span{border-bottom:3px solid var(--kb-yellow);padding-bottom:1px}
.entry{font-size:11px;font-weight:700;color:var(--sub);background:var(--bg);
border:1px solid var(--line);border-radius:999px;padding:4px 10px;white-space:nowrap}
.topbar b{font-size:17px}.topbar .step{margin-left:auto;color:var(--sub);font-size:13px}
/* 면책 고지 — 인쇄본에도 반드시 남는다 */
.site-foot{border-top:2px solid var(--ink);margin-top:26px;padding-top:14px;break-inside:avoid}
.site-foot .notice{font-size:12.5px;line-height:1.7;background:var(--warn-bg);
border:1px solid #fde68a;border-radius:10px;padding:11px 15px;color:var(--ink)}
.site-foot ul{margin:12px 0 0;padding-left:18px;font-size:12px;color:var(--sub);line-height:1.85}
.live{background:var(--ok-bg);color:var(--ok);border:1px solid #a6f4c5;border-radius:999px;
padding:4px 12px;font-size:12px;font-weight:700}
main{padding:28px 40px 60px}
.risk-hero{display:flex;gap:24px;background:var(--card);border:1px solid var(--line);border-radius:16px;
padding:28px 32px;align-items:center;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.grade-badge{width:110px;height:110px;border-radius:20px;display:grid;place-items:center;flex-shrink:0;border:2px solid}
.grade-badge.danger{background:var(--danger-bg);border-color:var(--danger);color:var(--danger)}
.grade-badge.warn{background:var(--warn-bg);border-color:var(--warn);color:var(--warn)}
.grade-badge.ok{background:var(--ok-bg);border-color:var(--ok);color:var(--ok)}
.grade-badge .g{font-size:52px;font-weight:800;line-height:1}.grade-badge .s{font-size:12px;font-weight:600}
.risk-info h1{font-size:22px;margin-bottom:6px}.risk-info .score{font-weight:700}
.risk-info p{color:var(--sub);font-size:14.5px;line-height:1.65;max-width:760px;margin-top:8px}
.chips{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
.chip{font-size:12.5px;padding:5px 12px;border-radius:999px;background:var(--bg);border:1px solid var(--line);color:var(--sub)}
.chip.red{background:var(--danger-bg);border-color:#f5c6c2;color:var(--danger);font-weight:600}
h2.sec{font-size:16px;margin:30px 0 14px;display:flex;align-items:center;gap:8px}
h2.sec .count{background:var(--danger);color:#fff;font-size:12px;border-radius:999px;padding:2px 9px}
.disc-card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--danger);
border-radius:12px;padding:20px 24px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
.disc-card.medium{border-left-color:var(--warn)}.disc-card.low{border-left-color:var(--sub)}
.disc-head{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.sev{font-size:11.5px;font-weight:700;padding:3px 10px;border-radius:6px}
.sev.high{background:var(--danger-bg);color:var(--danger)}
.sev.medium{background:var(--warn-bg);color:var(--warn)}
.sev.low{background:var(--bg);color:var(--sub)}
.disc-head h3{font-size:15.5px}.disc-id{margin-left:auto;color:#aab0b8;font-size:12px;font-family:monospace}
.evi{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}
.evi-box{background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px 14px}
.evi-box.single{grid-column:1/-1}
.evi-box .doc{font-size:11px;color:var(--sub);letter-spacing:.3px;margin-bottom:4px;text-transform:uppercase;font-weight:600}
.evi-box .field{font-size:11.5px;color:#8a9098;font-family:monospace;margin-bottom:5px}
.evi-box .val{font-size:13.5px;font-family:"SF Mono",Consolas,monospace;line-height:1.5;word-break:break-word}
.ucp{display:flex;gap:10px;align-items:flex-start;background:var(--bg);border-radius:8px;padding:11px 14px;margin-bottom:12px}
.ucp-badge{flex-shrink:0;background:var(--ink);color:var(--kb-yellow);font-size:11.5px;font-weight:700;padding:4px 10px;border-radius:6px}
.ucp q{color:var(--sub);font-size:13px;line-height:1.6}
.fix{background:var(--blue-bg);border:1px solid #d3e2fb;border-radius:8px;padding:11px 14px;font-size:13.5px;color:var(--blue);line-height:1.6}
.fix b{font-size:12px;letter-spacing:.5px;display:block;margin-bottom:3px}
.empty{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:40px;text-align:center;color:var(--sub)}
footer{margin-top:30px;font-size:12px;color:#98a2b3;line-height:1.7;border-top:1px solid var(--line);padding-top:14px}
.cta{margin-top:26px;display:flex;gap:12px}
.btn{border:0;border-radius:10px;padding:14px 26px;font-size:15px;font-weight:700;cursor:pointer;
text-decoration:none;display:inline-block;font-family:inherit}
.btn.primary{background:var(--kb-yellow);color:var(--ink)}
.btn.ghost{background:#fff;border:1px solid var(--line);color:var(--sub)}

/* ── 인쇄(PDF 저장) 최적화 ────────────────────────────
   버튼·상단바 등 화면 전용 요소를 숨기고 A4 폭에 맞춘다.
   브라우저 인쇄 대화상자에서 "PDF로 저장"을 고르면 그대로 리포트가 된다. */
@media print{
  @page{size:A4;margin:12mm}
  body{width:auto;background:#fff}
  .topbar,.cta{display:none!important}
  main{padding:0}
  .risk-hero,.disc-card,.evi-box,.ucp,.fix{box-shadow:none;break-inside:avoid;page-break-inside:avoid}
  .disc-card{border:1px solid #ccc;border-left-width:4px}
  .print-head{display:block!important}
  a{text-decoration:none;color:inherit}
}
.print-head{display:none;border-bottom:2px solid var(--ink);padding-bottom:10px;margin-bottom:18px}
.print-head b{font-size:18px}.print-head span{float:right;font-size:12px;color:var(--sub)}
"""


def e(s):
    return html.escape(str(s if s is not None else ""))


def card(disc):
    sev = disc["severity"]
    evi = "".join(
        f'<div class="evi-box{" single" if len(disc["evidence"]) == 1 else ""}">'
        f'<div class="doc">{e(DOC_LABEL.get(x["doc"], x["doc"]))}</div>'
        f'<div class="field">{e(x["field"])}</div>'
        f'<div class="val">{e(x["value"])}</div></div>'
        for x in disc["evidence"])
    fix = (f'<div class="fix"><b>수정 제안</b>{e(disc["suggested_fix_ko"])}</div>'
           if disc.get("suggested_fix_ko") else "")
    quote = disc["ucp_basis"].get("quote_ko") or ""
    return f"""<article class="disc-card {sev}">
  <div class="disc-head"><span class="sev {sev}">{sev.upper()}</span>
    <h3>{e(disc["description_ko"])}</h3><span class="disc-id">{e(disc["id"])}</span></div>
  <div class="evi">{evi}</div>
  <div class="ucp"><span class="ucp-badge">{e(disc["ucp_basis"]["article"])}</span><q>{e(quote)}</q></div>
  {fix}
</article>"""


def build_html(report, source_name, presentation_date=None):
    risk = report["overall_risk"]
    g = risk["grade"]
    headline, badge, tone = GRADE_LABEL[g]
    discs = sorted(report["discrepancies"],
                   key=lambda x: {"high": 0, "medium": 1, "low": 2}[x["severity"]])
    high = sum(1 for x in discs if x["severity"] == "high")
    med = sum(1 for x in discs if x["severity"] == "medium")
    chips = [f'<span class="chip">케이스 {e(report["case_id"])}</span>',
             f'<span class="chip">{len(report["documents_checked"])}종 서류 검사</span>']
    if high:
        chips.append(f'<span class="chip red">HIGH {high}건</span>')
    if med:
        chips.append(f'<span class="chip">MEDIUM {med}건</span>')
    if presentation_date:
        chips.append(f'<span class="chip">제시일 {e(presentation_date)}</span>')

    body = "".join(card(d) for d in discs) if discs else \
        '<div class="empty">교차 대조 결과 하자가 발견되지 않았습니다.</div>'
    section = (f'<h2 class="sec">발견된 하자 <span class="count">{len(discs)}</span></h2>'
               if discs else '<h2 class="sec">검사 결과</h2>')

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<title>TradeGuard — 하자 검사 리포트 ({e(report["case_id"])})</title>
<style>{CSS}</style></head><body>
<div class="topbar"><b class="wordmark">Trade<span>Guard</span></b>
  <span class="entry">2026 KB AI Challenge 출품작</span>
  <span class="live">● LIVE — detect.py 실제 출력</span>
  <span class="step">① 서류 등록 → ② 판독 → <b>③ 하자 검사</b> → ④ 결과</span></div>
<main>
  <div class="print-head"><b>TradeGuard 하자 검사 리포트</b>
    <span>케이스 {e(report["case_id"])} · {datetime.now():%Y-%m-%d}</span></div>
  <section class="risk-hero">
    <div class="grade-badge {tone}"><span class="g">{g}</span><span class="s">{badge}</span></div>
    <div class="risk-info">
      <h1>{headline} <span class="score">· {risk.get("score", "-")} / 100</span></h1>
      <p>{e(risk["summary_ko"])}</p>
      <div class="chips">{"".join(chips)}</div>
    </div>
  </section>
  {section}
  {body}
  <div class="cta">
    <a class="btn primary" href="screen4_fx_simulator.html">대금 수취 시점 손익 보기 →</a>
    <button class="btn ghost" onclick="window.print()">🖨 하자 리포트 PDF 저장</button>
    <a class="btn ghost" href="index.html">← 데모 홈</a>
  </div>
  <div class="site-foot">
    <p class="notice"><b>본 리포트는 2026 KB AI Challenge 출품을 위해 개발된 프로토타입의 산출물입니다.</b>
      KB국민은행이 운영하거나 보증하는 서비스가 아니며, 은행 제출 전 <b>참고용 사전 점검</b> 결과입니다.
      은행의 최종 심사 결과와 다를 수 있습니다.</p>
    <ul>
      <li>UCP600 조항 요지는 학습용 요약본입니다. 정확한 해석은 국제상업회의소(ICC) 공식 간행물을 따릅니다.</li>
      <li>본 리포트는 법률·무역 실무에 대한 자문이 아닙니다.</li>
    </ul>
  </div>
  <footer>
    입력: {e(source_name)} · 생성 {datetime.now():%Y-%m-%d %H:%M} ·
    판정 엔진 pipeline/detect.py (결정적 규칙, LLM 미사용) ·
    조항 인용 pipeline/ucp600_kb.json<br>
    이 화면의 모든 값은 하드코딩이 아니라 실제 검출 결과를 렌더링한 것입니다.
  </footer>
</main></body></html>"""


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = None
    if "--out" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--out") + 1])
    if not args:
        sys.exit("사용법: python3 render_report.py <case.json|report.json> [--out out.html]")

    src = Path(args[0])
    data = json.loads(src.read_text(encoding="utf-8"))
    pres = data.get("presentation_date")
    if "documents" in data:  # 벤치마크 케이스 → 검출 실행
        report = build_report(data.get("case_id", src.stem), data["documents"], parse_date(pres))
    elif "discrepancies" in data:  # 이미 detect.py가 만든 리포트
        report = data
    else:
        sys.exit("입력 형식을 알 수 없습니다 (documents 또는 discrepancies 키 필요)")

    page = build_html(report, src.name, pres)
    out = out or src.with_suffix(".report.html")
    out.write_text(page, encoding="utf-8")
    print(f"[report] {report['case_id']} 등급 {report['overall_risk']['grade']} · "
          f"하자 {len(report['discrepancies'])}건 → {out}")


if __name__ == "__main__":
    main()
