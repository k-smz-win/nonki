"""
data/CSV を読み docs/index.html を生成するエントリ。

report_data: データ構造・CSV読み込み・集計
report_html: 祝日・フォーマット・HTML/SVG生成
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from report_data import (
    AvgRow,
    DetailRow,
    build_detail_stats_by_day,
    read_avg_csv,
    read_details_csv,
)
from report_html import (
    FMT_GENERATED_AT,
    backup_existing_html,
    build_detail_payload_by_day,
    escape,
    fmt_date_jp_with_weekday_html_no_color,
    fmt_yen,
    html_monthly_summary,
    html_table_avg,
    load_jp_holidays_for_range,
    svg_line_chart,
)

# 入力・出力・タイトル
DEFAULT_AVG_PATH = "data/konohana_daily_avg.csv"
DEFAULT_DETAILS_PATH = "data/konohana_daily_details.csv"
DEFAULT_OUT_PATH = "docs/index.html"
TITLE = "此花区 日別相場レポート"


# data/CSV を読み docs/index.html を生成
#
# 引数:
#   （なし。argparse で --avg, --details, --out, --title を受け付ける）
#
# 戻り値:
#   int: 終了コード（0=正常）
def main() -> int:
    parser = argparse.ArgumentParser(description="data フォルダの CSV をもとに index.html を生成")
    parser.add_argument("--avg", default=DEFAULT_AVG_PATH, help=f"平均CSV (default: {DEFAULT_AVG_PATH})")
    parser.add_argument("--details", default=DEFAULT_DETAILS_PATH, help=f"明細CSV (default: {DEFAULT_DETAILS_PATH})")
    parser.add_argument("--out", default=DEFAULT_OUT_PATH, help=f"出力HTML (default: {DEFAULT_OUT_PATH})")
    parser.add_argument("--title", default=TITLE, help=f"HTMLタイトル (default: {TITLE})")
    args = parser.parse_args()

    # 引数からパスを取得。存在しない CSV は空リスト扱い
    avg_path = Path(args.avg)
    details_path = Path(args.details)
    out_path = Path(args.out)
    avg_rows: List[AvgRow] = read_avg_csv(avg_path) if avg_path.exists() else []
    details_rows: List[DetailRow] = read_details_csv(details_path) if details_path.exists() else []

    # 平均 or 明細の日付範囲で祝日を読み込み
    if avg_rows:
        holidays = load_jp_holidays_for_range(avg_rows[0].checkin, avg_rows[-1].checkin)
    elif details_rows:
        holidays = load_jp_holidays_for_range(details_rows[0].checkin, details_rows[-1].checkin)
    else:
        holidays = set()

    # 日別統計・モーダル用 JSON を事前計算
    detail_stats_by_day = build_detail_stats_by_day(details_rows) if details_rows else {}
    detail_payload_by_day = build_detail_payload_by_day(details_rows, holidays) if details_rows else {}

    title = args.title
    # HTML をパーツ単位で組み立て
    html_parts: List[str] = []
    html_parts.append("<!DOCTYPE html>")
    html_parts.append('<html lang="ja">')
    html_parts.append("<head>")
    html_parts.append('<meta charset="UTF-8">')
    html_parts.append('<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">')
    html_parts.append('<meta http-equiv="Pragma" content="no-cache">')
    html_parts.append('<meta http-equiv="Expires" content="0">')
    html_parts.append(f"<title>{escape(title)}</title>")
    html_parts.append(_STYLE)
    html_parts.append('<script id="day-details-data" type="application/json">')
    html_parts.append(json.dumps(detail_payload_by_day, ensure_ascii=False).replace("</", "<\\/"))
    html_parts.append("</script>")
    html_parts.append(_SCRIPT)
    html_parts.append("</head>")
    html_parts.append("<body>")
    # タイトル・日付範囲・生成日時
    if avg_rows:
        html_parts.append("<div style='display:flex; align-items:center; gap:16px; margin-bottom:10px;'>")
        html_parts.append(f"<h1 style='margin:0; line-height:1;'>{escape(title)}</h1>")
        html_parts.append("<div style='font-size:12px; color:#666; line-height:1; display:flex; align-items:center;'>")
        min_date = fmt_date_jp_with_weekday_html_no_color(min(r.checkin for r in avg_rows))
        max_date = fmt_date_jp_with_weekday_html_no_color(max(r.checkin for r in avg_rows))
        html_parts.append(f"<b>{min_date}</b> ～&nbsp;<b>{max_date}</b>")
        html_parts.append("</div>")
        html_parts.append("</div>")
    else:
        html_parts.append(f"<h1>{escape(title)}</h1>")
    generated_at = datetime.now().strftime(FMT_GENERATED_AT)
    html_parts.append(f"<p style='margin:4px 0 10px 0; font-size:12px; color:#666;'>生成日時: {escape(generated_at)}</p>")
    # 概要カード（1ヶ月単位の推移表）
    if avg_rows:
        html_parts.append("<div style='max-width:50%; margin-right:auto;'>")
        html_parts.append("<div class='card' style='padding:17px;'>")
        html_parts.append("<h2 style='font-size:13px; margin-top:0; margin-bottom:12px; font-weight:600;'>概要</h2>")
        html_parts.append("<p style='margin:4px 0 0 0; font-size:11px; color:#666; line-height:1.6;'>レビュー4.8より下は除外、レビュー数20未満は除外、上限は直近1ヶ月45,000円、直近2ヶ月45,000円、通常期50,000円、3連休45,000円、繁忙期（お盆・正月・年末・GW）50,000円としています。</p>")
        html_parts.append("<div style='margin-top:12px;'>")
        html_parts.append("<h3 style='margin:0 0 8px 0; font-size:13px; font-weight:600;'>1ヶ月単位の推移</h3>")
        html_parts.append(html_monthly_summary(avg_rows, detail_stats_by_day))
        html_parts.append("</div>")
        html_parts.append("</div>")
        html_parts.append("</div>")

        # 日別価格グラフ（折れ線 SVG）
        html_parts.append("<div class='card' style='margin-top:12px; max-width:1100px;'>")
        html_parts.append('<div class="card-head">')
        html_parts.append("<h2 style='font-size:13px; margin-top:0; margin-bottom:12px; font-weight:600;'>日別価格グラフ</h2>")
        html_parts.append("<div class='legend'><div class='legend-item'><span class='swatch' style='border-top-color:#2563eb; border-top-width:3.5px'></span>平均</div><div class='legend-item'><span class='swatch' style='border-top-color:#8b5cf6; border-top-width:2.8px'></span>中央値</div><div class='legend-item'><span class='swatch' style='border-top-color:#10b981; border-top-width:2.2px; border-top-style:dashed'></span>下位25%点</div><div class='legend-item'><span class='swatch' style='border-top-color:#f59e0b; border-top-width:2.2px; border-top-style:dashed'></span>上位25%点</div></div>")
        html_parts.append("</div>")
        html_parts.append(svg_line_chart(avg_rows, detail_stats_by_day, holidays))
        html_parts.append("<p class='note'>点にマウスを置くと日付・平均・中央値・下位25%点・上位25%点・件数が見られます。</p>")
        html_parts.append("</div>")

        # 日別明細テーブル（▶でモーダル表示）
        html_parts.append("<div class='card' style='margin-top:12px; max-width:1100px;'>")
        html_parts.append("<h2 style='font-size:13px; margin-top:0; margin-bottom:12px; font-weight:600;'>日別明細</h2>")
        html_parts.append("<div class='scroll-box avg'>")
        html_parts.append(html_table_avg(avg_rows, detail_stats_by_day, holidays))
        html_parts.append("</div>")
        html_parts.append("</div>")
    else:
        html_parts.append("<div class='card'><p>平均CSVが見つからないか、読み込めませんでした。</p></div>")
    # 明細なし時は注意メッセージ
    if not details_rows:
        html_parts.append("<div class='card' style='margin-top:12px'><p>明細CSVが見つからないため、▶から明細を開けません。</p></div>")
    # モーダル（日付クリックで明細表示）
    html_parts.append("""<div class="modal-backdrop" id="modalBackdrop" aria-hidden="true">
  <div class="modal" role="dialog" aria-modal="true" aria-label="明細">
    <div class="modal-header">
      <div class="modal-title" id="modalTitle">明細</div>
      <button type="button" class="modal-close" id="modalClose" aria-label="閉じる">×</button>
    </div>
    <div class="modal-body" id="modalBody">
      <div id="modalStats" style="margin-bottom:10px"></div>
      <div class="details-table-wrap">
        <table>
          <thead><tr><th style='text-align:center;'>No.</th><th style='text-align:center;'>価格</th><th style='text-align:center;'>タイトル</th><th style='text-align:center;'>レビュー</th><th style='text-align:center;'>レビュー数</th><th style='text-align:center;'>補足情報</th><th style='text-align:center;'>詳細</th><th style='text-align:center;'>備考</th></tr></thead>
          <tbody id="modalTableBody"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>""")

    html_parts.append("</body></html>")
    # 既存ファイルを退避してから書き出し
    out_path.parent.mkdir(parents=True, exist_ok=True)
    backup_existing_html(out_path)
    out_path.write_text("\n".join(html_parts), encoding="utf-8")
    print(f"HTMLを出力しました: {out_path}")
    return 0


_STYLE = """<style>
body { font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic", "Meiryo", system-ui, -apple-system, Segoe UI, Arial, sans-serif; padding: 18px; color: #111; }
h1 { margin: 0 0 10px; font-size: 20px; }
.muted { color: #666; }
.grid { display: grid; grid-template-columns: 0.8fr 2.7fr; gap: 12px; align-items: stretch; max-width: 1127px; }
.card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 12px; background: #fff; }
.card-head { display:flex; align-items: baseline; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.legend { display:flex; gap: 12px; flex-wrap: wrap; align-items: center; }
.legend-item { display:flex; gap: 6px; align-items: center; font-size: 12px; color: #111; }
.swatch { width: 22px; height: 0; border-top: 3px solid #999; }
.note { color: #666; font-size: 12px; margin: 8px 0 0; }
.graph-tooltip { position: fixed; background: #fff; border: 1px solid #d1d5db; border-radius: 8px; padding: 8px 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); font-size: 12px; line-height: 1.5; z-index: 1000; pointer-events: none; min-width: 160px; max-width: 200px; }
.graph-tooltip .tooltip-header { font-weight: 600; color: #111; margin-bottom: 6px; padding-bottom: 4px; border-bottom: 1px solid #e5e7eb; }
.graph-tooltip .tooltip-row { margin: 2px 0; display: flex; justify-content: space-between; align-items: baseline; }
.graph-tooltip .tooltip-label { color: #666; font-size: 11px; }
.graph-tooltip .tooltip-value { color: #111; font-weight: 500; text-align: right; }
.graph-tooltip .tooltip-section { margin-top: 6px; padding-top: 6px; border-top: 1px solid #f3f4f6; }
.graph-tooltip .tooltip-section:first-child { margin-top: 0; padding-top: 0; border-top: none; }
.scroll-box { max-height: 520px; overflow: auto; border: 1px solid #e5e7eb; border-radius: 10px; -webkit-overflow-scrolling: touch; }
.scroll-box.pad { padding: 6px; }
.scroll-box.avg { max-height: 420px; }
.jump-btn { border: 1px solid #e5e7eb; background: #fff; border-radius: 8px; padding: 4px 9px; cursor: pointer; font-size: 12px; min-width: 28px; min-height: 24px; }
.jump-btn:hover { background: #f3f4f6; }
.jump-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.45); display: none; align-items: center; justify-content: center; padding: 18px; z-index: 9999; }
.modal-backdrop.open { display: flex; }
.modal { width: min(1280px, 98vw); max-height: min(95vh, 1000px); background: #fff; border-radius: 14px; border: 1px solid #e5e7eb; overflow: hidden; box-shadow: 0 12px 40px rgba(0,0,0,0.25); display: flex; flex-direction: column; }
.modal-header { display:flex; align-items:center; justify-content: space-between; gap: 10px; padding: 12px 14px; border-bottom: 1px solid #e5e7eb; flex-shrink: 0; }
.modal-title { font-weight: 600; font-size: 13px; }
.modal-close { border: none; background: none; cursor: pointer; font-size: 20px; line-height: 1; color: #666; padding: 4px; border-radius: 4px; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; transition: background-color 0.2s, color 0.2s; }
.modal-close:hover { background: #f3f4f6; color: #333; }
.modal-body { padding: 10px 12px; overflow-y: auto; overflow-x: hidden; flex: 1; min-height: 0; -webkit-overflow-scrolling: touch; }
.modal table { table-layout: fixed; border-collapse: collapse; border-spacing: 0; }
.modal th, .modal td { padding: 3px 5px; font-size: 11px; line-height: 1.25; vertical-align: middle; border: 1px solid #e5e7eb; position: relative; }
.modal th { background: #f9fafb; position: sticky; top: 0; z-index: 10; border-bottom: 2px solid #e5e7eb; }
.modal td:nth-child(1) { text-align: center; }
.modal th:nth-child(1), .modal td:nth-child(1) { width: 40px; text-align: center; }
.modal th:nth-child(2), .modal td:nth-child(2) { width: 60px; }
.modal th:nth-child(3), .modal td:nth-child(3) { width: 140px; }
.modal th:nth-child(4), .modal td:nth-child(4) { width: 50px; }
.modal th:nth-child(5), .modal td:nth-child(5) { width: 50px; }
.modal th:nth-child(6), .modal td:nth-child(6) { width: 300px; }
.modal th:nth-child(7), .modal td:nth-child(7) { width: 50px; }
.modal th:nth-child(8), .modal td:nth-child(8) { width: 200px; }
.modal td:nth-child(8) { word-break: break-word; white-space: normal; line-height: 1.4; }
.modal td:nth-child(3) { word-break: break-word; white-space: normal; line-height: 1.4; }
.modal td:nth-child(6) { word-break: break-word; white-space: normal; line-height: 1.4; }
.modal .truncate-url { max-width: 100%; display: inline-block; }
.modal .link-btn, .link-btn { display: inline-flex; align-items: center; justify-content: center; padding: 2px 8px; background: #60a5fa; color: #fff; text-decoration: none; border: none; border-radius: 4px; font-size: 10px; white-space: nowrap; height: 20px; line-height: 1; box-shadow: none; outline: none; }
.modal .link-btn:hover, .link-btn:hover { background: #3b82f6; }
.modal .link-btn:focus, .link-btn:focus { outline: none; box-shadow: none; }
.modal .details-table-wrap { max-height: none; overflow: visible; border: none; margin-top: 0; }
.modal-body .details-table-wrap { max-height: calc(95vh - 180px); overflow-y: auto; overflow-x: auto; border: 1px solid #e5e7eb; border-radius: 8px; margin-top: 8px; -webkit-overflow-scrolling: touch; background: #fff; }
.modal-body #modalStats { flex-shrink: 0; margin-bottom: 10px; }
.pill { display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 11px; background: #f3f4f6; color: #111; margin-right: 8px; margin-bottom: 4px; font-weight: 500; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #e5e7eb; padding: 6px 8px; text-align: left; vertical-align: top; word-break: break-word; }
td a { word-break: break-all; }
th { background: #f9fafb; position: sticky; top: 0; }
tr:nth-child(even) { background: #fcfcfd; }
table.small th, table.small td { padding: 5px 7px; font-size: 13px; vertical-align: middle; }
.weekday-table th, .weekday-table td { vertical-align: middle; }
details { margin: 10px 0; }
summary { cursor: pointer; font-weight: 600; }
.details-table-wrap { max-height: 420px; overflow: auto; border: 1px solid #e5e7eb; border-radius: 10px; margin-top: 8px; -webkit-overflow-scrolling: touch; }
.weekday-table tbody td:first-child { color: #0f766e; font-weight: 700; }
.weekday-table tr.sat td:first-child { color: #2563eb; }
.weekday-table tr.sun td:first-child { color: #dc2626; }
.wday { font-weight: 700; }
.wday.sat { color: #2563eb; }
.wday.sun { color: #dc2626; }
.wday.holiday { color: #16a34a; }
.wday.weekday { color: inherit; }
.avg-table th, .avg-table td { padding: 4px 6px; font-size: 12px; vertical-align: middle; }
.avg-table th:nth-child(1), .avg-table td:nth-child(1) { width: 34px; text-align: center; padding: 4px 4px; }
.avg-table th:nth-child(2), .avg-table td:nth-child(2) { width: 180px; text-align: center; }
.avg-table th:nth-child(3), .avg-table td:nth-child(3) { width: 80px; }
.avg-table th:nth-child(4), .avg-table td:nth-child(4) { width: 80px; }
.avg-table th:nth-child(5), .avg-table td:nth-child(5) { width: 90px; }
.avg-table th:nth-child(6), .avg-table td:nth-child(6) { width: 90px; }
.avg-table th:nth-child(7), .avg-table td:nth-child(7) { width: 60px; }
.avg-table th:nth-child(8), .avg-table td:nth-child(8) { width: 80px; }
.avg-table th:nth-child(9), .avg-table td:nth-child(9) { width: 80px; }
.avg-table th:nth-child(10), .avg-table td:nth-child(10) { width: 80px; }
.avg-table { min-width: 980px; }
.truncate-url { display: inline-block; max-width: 240px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; vertical-align: bottom; }
@media (max-width: 900px) { body { padding: 12px; } .grid { grid-template-columns: 1fr; } .card { padding: 10px; } .card-head { flex-direction: column; align-items: flex-start; } .legend { gap: 8px; } .scroll-box.avg { max-height: 55vh; } .truncate-url { max-width: 120px; } .avg-table th, .avg-table td { font-size: 11px; padding: 3px 5px; } .avg-table { min-width: 940px; } }
@media (max-width: 600px) { h1 { font-size: 18px; } h2 { font-size: 16px; margin: 6px 0; } .legend-item { font-size: 11px; } .modal-backdrop { padding: 0; } .modal { width: 100vw; max-height: 100vh; height: 100vh; border-radius: 0; } .modal-body { padding: 8px 10px; } .modal-body .details-table-wrap { max-height: calc(100vh - 160px); } .modal th, .modal td { font-size: 10px; padding: 3px 4px; } .modal th:nth-child(1), .modal td:nth-child(1) { width: 30px; text-align: center; } .modal th:nth-child(2), .modal td:nth-child(2) { width: 50px; } .modal th:nth-child(3), .modal td:nth-child(3) { width: 90px; } .modal th:nth-child(4), .modal td:nth-child(4) { width: 40px; } .modal th:nth-child(5), .modal td:nth-child(5) { width: 40px; } .modal th:nth-child(6), .modal td:nth-child(6) { width: 120px; } .modal th:nth-child(7), .modal td:nth-child(7) { width: 40px; } .modal th:nth-child(8), .modal td:nth-child(8) { width: 100px; } .pill { font-size: 11px; } }
</style>"""

_SCRIPT = """<script>
function escHtml(s){ return String(s).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#x27;"); }
function fmtYen(n){ if(n===null||n===undefined||n==="") return "-"; const v=Number(n); if(!Number.isFinite(v)) return "-"; return "¥"+v.toLocaleString("ja-JP"); }
function mean(arr){ if(arr.length===0) return null; let s=0; for(const v of arr) s+=v; return Math.round(s/arr.length); }
function median(arr){ if(arr.length===0) return null; const xs=[...arr].sort((a,b)=>a-b); const m=Math.floor(xs.length/2); if(xs.length%2===1) return xs[m]; return Math.round((xs[m-1]+xs[m])/2); }
function quantile(arr,p){ if(arr.length===0) return null; const xs=[...arr].sort((a,b)=>a-b); if(xs.length===1) return xs[0]; const i=(xs.length-1)*p; const lo=Math.floor(i); const hi=Math.min(lo+1,xs.length-1); const w=i-lo; return Math.round(xs[lo]*(1-w)+xs[hi]*w); }
let DAY_DETAILS={};
window.addEventListener("DOMContentLoaded",()=>{ try{ DAY_DETAILS=JSON.parse(document.getElementById("day-details-data").textContent||"{}"); }catch(e){ DAY_DETAILS={}; }
  const backdrop=document.getElementById("modalBackdrop"); const btnClose=document.getElementById("modalClose");
  if(backdrop) backdrop.addEventListener("click",(e)=>{ if(e.target===backdrop) closeDayModal(); });
  if(btnClose) btnClose.addEventListener("click",closeDayModal);
  document.addEventListener("keydown",(e)=>{ if(e.key==="Escape") closeDayModal(); });
  document.body.addEventListener("click",(e)=>{ const btn=e.target.closest(".jump-btn"); if(!btn||btn.disabled) return; const iso=btn.getAttribute("data-iso-date"); if(iso) openDayModal(iso); });
  initGraphTooltip();
});
function openDayModal(isoDate){ const title=document.getElementById("modalTitle"); const stats=document.getElementById("modalStats"); const body=document.getElementById("modalTableBody"); const item=DAY_DETAILS[isoDate];
  if(!title||!stats||!body) return;
  title.innerHTML=item? (item.dateLabelHtml||escHtml(isoDate)) : escHtml(isoDate);
  if(!item){ stats.innerHTML=""; body.innerHTML='<tr><td colspan="8" class="muted">この日の明細はありません</td></tr>'; document.getElementById("modalBackdrop")&&document.getElementById("modalBackdrop").classList.add("open"); document.getElementById("modalBody")&&(document.getElementById("modalBody").scrollTop=0); return; }
  const rows=(item.rows||[]).slice();
  const prices=rows.map(r=>Number(r.price)).filter(v=>Number.isFinite(v));
  const minV=prices.length?Math.min(...prices):null; const maxV=prices.length?Math.max(...prices):null;
  stats.innerHTML=['<span class="pill">件数 '+rows.length+'</span>','<span class="pill">平均 '+escHtml(fmtYen(mean(prices)))+'</span>','<span class="pill">中央値 '+escHtml(fmtYen(median(prices)))+'</span>','<span class="pill">下位25%点 '+escHtml(fmtYen(quantile(prices,0.25)))+'</span>','<span class="pill">上位25%点 '+escHtml(fmtYen(quantile(prices,0.75)))+'</span>','<span class="pill">最小 '+escHtml(fmtYen(minV))+'</span>','<span class="pill">最大 '+escHtml(fmtYen(maxV))+'</span>'].join(" ");
  rows.sort((a,b)=>Number(a.price)-Number(b.price));
  const html=rows.map((r,idx)=>{ const url=r.url||""; const label=r.label||""; const title=r.title||""; const reviewsCount=r.reviews_count!=null?String(r.reviews_count):""; const rating=r.rating!=null?parseFloat(r.rating).toFixed(2):""; const subtitle=r.subtitle||""; const detailLink=url?`<a class="link-btn" href="${escHtml(url)}" target="_blank" rel="noreferrer">詳細</a>`:"-"; const labelShort=label.length>180?label.slice(0,180)+"…":label; const titleShort=title.length>90?title.slice(0,90)+"…":title; const subtitleShort=subtitle.length>150?subtitle.slice(0,150)+"…":subtitle; return `<tr><td>${idx+1}</td><td style='text-align:right;'>${escHtml(fmtYen(r.price))}</td><td>${escHtml(titleShort||"-")}</td><td style='text-align:right;'>${escHtml(rating||"-")}</td><td style='text-align:right;'>${escHtml(reviewsCount||"-")}</td><td>${escHtml(subtitleShort||"-")}</td><td style="text-align:center;">${detailLink}</td><td>${escHtml(labelShort)}</td></tr>`; }).join("");
  body.innerHTML=html||`<tr><td colspan="8" class="muted">明細なし</td></tr>`;
  body.querySelectorAll('.link-btn').forEach(btn=>{ btn.removeAttribute('title'); btn.addEventListener('mouseenter',e=>{ e.preventDefault(); e.stopPropagation(); }); });
  body.querySelectorAll('td').forEach((cell)=>{ const row=cell.parentElement; if(row&&cell===row.lastElementChild){ cell.removeAttribute('title'); cell.style.cursor='default'; cell.addEventListener('focus',e=>{ e.preventDefault(); e.stopPropagation(); }); cell.addEventListener('mouseenter',e=>{ e.preventDefault(); e.stopPropagation(); }); const observer=new MutationObserver(m=>{ m.forEach(mut=>{ if(mut.type==='attributes'&&mut.attributeName==='title') cell.removeAttribute('title'); }); }); observer.observe(cell,{attributes:true,attributeFilter:['title']}); } });
  document.getElementById("modalBackdrop").classList.add("open");
  document.getElementById("modalBody").scrollTop=0;
}
function closeDayModal(){ const b=document.getElementById("modalBackdrop"); if(b) b.classList.remove("open"); }
let tooltipEl=null; let tooltipPinned=false;
function updateTooltipPosition(circle){ if(!tooltipEl) return; tooltipEl.style.display='block'; const circleRect=circle.getBoundingClientRect(); const px=circleRect.left+circleRect.width/2; const circleTop=circleRect.top; const offset=8; const tooltipHeight=tooltipEl.offsetHeight; const tooltipWidth=tooltipEl.offsetWidth; let tooltipTop=circleTop-tooltipHeight-offset; let tooltipLeft=px+12; tooltipEl.style.left=tooltipLeft+'px'; tooltipEl.style.top=tooltipTop+'px'; const tooltipRect=tooltipEl.getBoundingClientRect(); if(tooltipRect.right>window.innerWidth-10) tooltipEl.style.left=(px-tooltipWidth-12)+'px'; if(tooltipRect.left<10) tooltipEl.style.left='10px'; if(tooltipRect.top<10) tooltipEl.style.top=(circleRect.bottom+offset)+'px'; const finalRect=tooltipEl.getBoundingClientRect(); if(finalRect.bottom>window.innerHeight-10) tooltipEl.style.top=(window.innerHeight-finalRect.height-10)+'px'; }
function showTooltipForCircle(c){ const date=c.getAttribute('data-tooltip-date'); const avg=c.getAttribute('data-tooltip-avg'); const median=c.getAttribute('data-tooltip-median'); const p25=c.getAttribute('data-tooltip-p25'); const p75=c.getAttribute('data-tooltip-p75'); const min=c.getAttribute('data-tooltip-min'); const max=c.getAttribute('data-tooltip-max'); const count=c.getAttribute('data-tooltip-count'); if(!date||!tooltipEl) return; tooltipEl.innerHTML=`<div class="tooltip-header">${escHtml(date)}</div><div class="tooltip-section"><div class="tooltip-row"><span class="tooltip-label">平均</span><span class="tooltip-value">${escHtml(avg)}</span></div><div class="tooltip-row"><span class="tooltip-label">中央値</span><span class="tooltip-value">${escHtml(median)}</span></div></div><div class="tooltip-section"><div class="tooltip-row"><span class="tooltip-label">下位25%点</span><span class="tooltip-value">${escHtml(p25)}</span></div><div class="tooltip-row"><span class="tooltip-label">上位25%点</span><span class="tooltip-value">${escHtml(p75)}</span></div></div><div class="tooltip-section"><div class="tooltip-row"><span class="tooltip-label">最小</span><span class="tooltip-value">${escHtml(min)}</span></div><div class="tooltip-row"><span class="tooltip-label">最大</span><span class="tooltip-value">${escHtml(max)}</span></div></div><div class="tooltip-row" style="margin-top:8px; padding-top:8px; border-top:1px solid #f3f4f6;"><span class="tooltip-label">件数</span><span class="tooltip-value">${escHtml(count)}</span></div>`; updateTooltipPosition(c); tooltipEl.style.display='block'; }
function hideTooltip(){ if(tooltipEl) tooltipEl.style.display='none'; tooltipPinned=false; }
function initGraphTooltip(){ if(!tooltipEl){ tooltipEl=document.createElement('div'); tooltipEl.className='graph-tooltip'; tooltipEl.style.display='none'; document.body.appendChild(tooltipEl); } document.addEventListener("click",(e)=>{ if(tooltipPinned&&tooltipEl&&!tooltipEl.contains(e.target)&&!e.target.closest('svg circle[data-tooltip-date]')) hideTooltip(); }); function tryInit(){ const circles=document.querySelectorAll('svg circle[data-tooltip-date]'); if(circles.length) attachTooltipListeners(circles); else setTimeout(tryInit,100); } setTimeout(tryInit,50); setTimeout(tryInit,200); setTimeout(tryInit,500); }
function attachTooltipListeners(circles){ circles.forEach(circle=>{ if(circle.hasAttribute('data-tooltip-attached')) return; circle.setAttribute('data-tooltip-attached','true'); circle.style.pointerEvents='auto'; circle.addEventListener('mouseenter',e=>{ e.stopPropagation(); const c=e.target; if(!c.getAttribute('data-tooltip-date')) return; if(!tooltipEl){ tooltipEl=document.createElement('div'); tooltipEl.className='graph-tooltip'; tooltipEl.style.display='none'; document.body.appendChild(tooltipEl); } showTooltipForCircle(c); }); circle.addEventListener('mouseleave',e=>{ e.stopPropagation(); if(!tooltipPinned&&tooltipEl) tooltipEl.style.display='none'; }); circle.addEventListener('mousemove',e=>{ e.stopPropagation(); if(!tooltipEl||tooltipEl.style.display==='none') return; updateTooltipPosition(e.target); }); circle.addEventListener('click',e=>{ e.stopPropagation(); e.preventDefault(); const c=e.target; if(!c.getAttribute('data-tooltip-date')) return; if(!tooltipEl){ tooltipEl=document.createElement('div'); tooltipEl.className='graph-tooltip'; tooltipEl.style.display='none'; document.body.appendChild(tooltipEl); } tooltipPinned=true; showTooltipForCircle(c); }); }); }
</script>"""


if __name__ == "__main__":
    raise SystemExit(main())
