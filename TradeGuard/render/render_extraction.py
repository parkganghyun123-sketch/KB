#!/usr/bin/env python3
"""화면2 실데이터 렌더러 — 추출 결과 JSON → 판독 화면 HTML

목업(screen2_extraction.html)은 필드값이 하드코딩돼 있다. 이 스크립트는
extract.py가 실제로 추출한 JSON을 읽어 같은 화면을 만든다.

두 가지 모드로 동작한다:
  · 추출 결과만 있을 때 → 필드값 + 신뢰도 표시
  · 정답(케이스 JSON)도 주면 → **정답 대조 열이 추가**되어 어느 필드를 틀렸는지 보인다
    (심사 데모에서 "정확도 96.6%"의 근거를 화면으로 보여줄 수 있음)

사용법:
  # 1) 추출 결과 3종으로 생성
  python3 render/render_extraction.py --case DEFECT-001 \\
      --lc out/lc.json --invoice out/inv.json --bl out/bl.json \\
      --docs render/sample_output --out mockups/screen2_live.html

  # 2) 정답 대조 모드 (추출 없이 정답을 그대로 넣으면 100% 화면 = 목업 대체용)
  python3 render/render_extraction.py --from-case benchmark/cases/DEFECT-001.json \\
      --out mockups/screen2_live.html
"""
import html
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmark"))

DOCS = [("letter_of_credit", "lc", "📜 신용장"),
        ("commercial_invoice", "invoice", "🧾 상업송장"),
        ("bill_of_lading", "bl", "🚢 선하증권")]
SKIP = {"field_confidence", "unreadable_fields", "doc_type"}

CSS = """
:root{--kb-yellow:#FFBC00;--ink:#26282c;--sub:#696e76;--line:#e4e6ea;--card:#fff;--bg:#f6f7f8;
--warn:#b54708;--warn-bg:#fffaeb;--ok:#067647;--ok-bg:#ecfdf3;--danger:#d92d20;--danger-bg:#fef0ef}
*{box-sizing:border-box;margin:0}
body{width:1280px;margin:0 auto;background:var(--bg);color:var(--ink);
font-family:"Pretendard","Apple SD Gothic Neo","Malgun Gothic",sans-serif}
.topbar{background:#fff;border-bottom:1px solid var(--line);padding:14px 40px;display:flex;align-items:center;gap:12px}
.logo-dot{width:26px;height:26px;border-radius:7px;background:var(--kb-yellow);display:grid;place-items:center;font-weight:800;font-size:13px}
.topbar b{font-size:17px}.topbar .step{margin-left:auto;color:var(--sub);font-size:13px}
.live{background:var(--ok-bg);color:var(--ok);border:1px solid #a6f4c5;border-radius:999px;padding:4px 12px;font-size:12px;font-weight:700}
main{padding:22px 40px 60px}
.summary{display:flex;gap:14px;margin-bottom:18px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 20px;flex:1}
.stat .k{font-size:12px;color:var(--sub);margin-bottom:4px}
.stat .v{font-size:22px;font-weight:800}
.stat .v.ok{color:var(--ok)}.stat .v.warn{color:var(--warn)}
.tabs{display:flex;gap:8px;margin-bottom:14px}
.tab{border:1px solid var(--line);background:#fff;border-radius:999px;padding:9px 20px;font-size:14px;
font-weight:600;color:var(--sub);cursor:pointer}
.tab.active{background:var(--ink);color:var(--kb-yellow);border-color:var(--ink)}
.split{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.pane{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.pane-hd{padding:12px 18px;border-bottom:1px solid var(--line);font-size:13px;font-weight:700;
color:var(--sub);display:flex;justify-content:space-between}
iframe{width:100%;height:720px;border:0;background:#fff}
.fieldwrap{max-height:720px;overflow-y:auto}
table.fields{width:100%;border-collapse:collapse}
table.fields td{padding:9px 16px;border-bottom:1px solid #f0f1f3;font-size:13.5px;vertical-align:top}
table.fields td.k{width:210px;color:var(--sub);font-family:monospace;font-size:12px}
tr.low td{background:var(--warn-bg)}
tr.wrong td{background:var(--danger-bg)}
.conf{float:right;font-size:11px;font-weight:700;border-radius:5px;padding:2px 8px}
.conf.hi{color:var(--ok);background:var(--ok-bg)}
.conf.low{color:var(--warn);background:#fef0c7}
.truth{display:block;margin-top:4px;font-size:11.5px;color:var(--danger)}
.empty{padding:40px;text-align:center;color:var(--sub)}
footer{margin-top:26px;font-size:12px;color:#98a2b3;line-height:1.7;border-top:1px solid var(--line);padding-top:14px}
.cta{margin-top:22px;display:flex;gap:12px}
.btn{border:0;border-radius:10px;padding:14px 26px;font-size:15px;font-weight:700;text-decoration:none;display:inline-block}
.btn.primary{background:var(--kb-yellow);color:var(--ink)}
.btn.ghost{background:#fff;border:1px solid var(--line);color:var(--sub);cursor:pointer;font-family:inherit}
@media print{
  @page{size:A4 landscape;margin:10mm}
  body{width:auto;background:#fff}
  .topbar,.cta,.tabs{display:none!important}
  main{padding:0}
  .split{grid-template-columns:1fr}
  iframe{display:none}
  .pane,.stat{box-shadow:none;border:1px solid #ccc;break-inside:avoid}
  .fieldwrap{max-height:none;overflow:visible}
  [id^=p]{display:block!important}
}
"""


def e(s):
    return html.escape(str(s if s is not None else ""))


def flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in SKIP:
                continue
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}[{i}]"))
    elif obj is not None:
        out[prefix] = obj
    return out


def norm(v):
    return round(float(v), 2) if isinstance(v, (int, float)) else str(v).strip().upper()


def rows(extracted, truth=None):
    """(html, 정확 수, 전체 수) — truth가 있으면 대조 열 추가"""
    conf = extracted.get("field_confidence") or {}
    unread = set(extracted.get("unreadable_fields") or [])
    flat = flatten(extracted)
    tflat = flatten(truth) if truth else {}
    hit = 0
    out = []
    keys = list(tflat) if truth else list(flat)
    for k in keys:
        got = flat.get(k)
        c = conf.get(k.split(".")[0], conf.get(k))
        low = c is not None and c < 0.8
        wrong = False
        if truth:
            tv = tflat.get(k)
            if got is not None and norm(got) == norm(tv):
                hit += 1
            else:
                wrong = True
        cls = "wrong" if wrong else ("low" if low else "")
        badge = ""
        if c is not None:
            badge = f'<span class="conf {"low" if low else "hi"}">{c:.2f}{" 확인 필요" if low else ""}</span>'
        val = e(got) if got is not None else \
            ('<i style="color:#aab0b8">판독 불가</i>' if k in unread else '<i style="color:#aab0b8">—</i>')
        truth_note = f'<span class="truth">정답: {e(tflat.get(k))}</span>' if wrong else ""
        out.append(f'<tr class="{cls}"><td class="k">{e(k)}</td><td>{val} {badge}{truth_note}</td></tr>')
    return "".join(out), hit, len(keys)


def build(case_id, docs_data, doc_dir, truths=None):
    truths = truths or {}
    panes, tabs, stats = [], [], {"hit": 0, "tot": 0, "low": 0, "unread": 0}
    for i, (key, suffix, label) in enumerate(DOCS):
        data = docs_data.get(key)
        active = " active" if i == 0 else ""
        tabs.append(f'<button class="tab{active}" onclick="show({i})">{label}</button>')
        if not data:
            panes.append(f'<div class="pane-body" id="p{i}" style="display:{"block" if i == 0 else "none"}">'
                         f'<div class="empty">이 서류의 추출 결과가 없습니다.</div></div>')
            continue
        body, hit, tot = rows(data, truths.get(key))
        stats["hit"] += hit
        stats["tot"] += tot
        stats["low"] += sum(1 for v in (data.get("field_confidence") or {}).values() if v < 0.8)
        stats["unread"] += len(data.get("unreadable_fields") or [])
        src = f"{doc_dir}/{case_id}_{suffix}.html"
        panes.append(f"""<div id="p{i}" style="display:{'grid' if i == 0 else 'none'}" class="split">
  <div class="pane"><div class="pane-hd"><span>원본 서류</span><span>{e(Path(src).name)}</span></div>
    <iframe src="{e(src)}"></iframe></div>
  <div class="pane"><div class="pane-hd"><span>추출 필드</span>
    <span>신뢰도 0.8 미만 노랑 · 정답 불일치 빨강</span></div>
    <div class="fieldwrap"><table class="fields">{body}</table></div></div>
</div>""")

    acc = stats["hit"] / stats["tot"] if (truths and stats["tot"]) else None
    acc_card = (f'<div class="stat"><div class="k">필드 추출 정확도 (정답 대조)</div>'
                f'<div class="v {"ok" if acc and acc >= .9 else "warn"}">{acc:.1%}</div></div>' if acc is not None else "")

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<title>TradeGuard — 판독 결과 ({e(case_id)})</title><style>{CSS}</style></head><body>
<div class="topbar"><div class="logo-dot">T</div><b>TradeGuard</b>
  <span class="live">● LIVE — extract.py 실제 출력</span>
  <span class="step">① 업로드 → <b>② 판독</b> → ③ 하자 검사 → ④ 환노출·처방</span></div>
<main>
  <div class="summary">
    <div class="stat"><div class="k">케이스</div><div class="v">{e(case_id)}</div></div>
    <div class="stat"><div class="k">추출 필드 수</div><div class="v">{stats["tot"]}</div></div>
    {acc_card}
    <div class="stat"><div class="k">확인 필요 (신뢰도 &lt;0.8)</div>
      <div class="v {"warn" if stats["low"] else "ok"}">{stats["low"]}</div></div>
    <div class="stat"><div class="k">판독 불가</div><div class="v">{stats["unread"]}</div></div>
  </div>
  <div class="tabs">{"".join(tabs)}</div>
  {"".join(panes)}
  <div class="cta">
    <a class="btn primary" href="screen3_live.html">하자 검사 실행 →</a>
    <button class="btn ghost" onclick="window.print()">🖨 판독 결과 PDF 저장</button>
    <a class="btn ghost" href="index.html">← 데모 홈</a>
  </div>
  <footer>생성 {datetime.now():%Y-%m-%d %H:%M} · 추출 pipeline/extract.py ·
    이 화면의 필드값은 하드코딩이 아니라 실제 추출 결과입니다.</footer>
</main>
<script>
function show(i){{for(let k=0;k<3;k++){{
  const p=document.getElementById('p'+k); if(p) p.style.display = (k===i?'grid':'none');
  document.querySelectorAll('.tab')[k].classList.toggle('active', k===i);}}}}
</script></body></html>"""


def main():
    a = sys.argv[1:]

    def opt(n, d=None):
        return a[a.index(n) + 1] if n in a else d

    out = Path(opt("--out", "mockups/screen2_live.html"))
    doc_dir = opt("--docs", "../render/sample_output")

    if "--from-case" in a:  # 정답을 그대로 표시 (추출 없이 화면만 확인)
        case = json.loads(Path(opt("--from-case")).read_text(encoding="utf-8"))
        docs = case["documents"]
        page = build(case["case_id"], docs, doc_dir, truths=None)
    else:
        case_id = opt("--case", "CASE")
        docs = {}
        for key, suffix, _ in DOCS:
            p = opt(f"--{suffix}")
            if p:
                docs[key] = json.loads(Path(p).read_text(encoding="utf-8"))
        truths = None
        if "--truth" in a:
            truths = json.loads(Path(opt("--truth")).read_text(encoding="utf-8"))["documents"]
        page = build(case_id, docs, doc_dir, truths)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"[extraction] → {out}")


if __name__ == "__main__":
    main()
