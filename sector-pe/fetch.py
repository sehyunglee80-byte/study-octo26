"""S&P 500 섹터별 선행 PER (FactSet Earnings Insight 주간 보고서)을 받아 out/sectors.csv 로 저장한다.

FactSet 보고서의 'S&P 500 Sector-Level Forward 12-Month P/E Ratios' 차트 아래 표
(섹터별 선행 PER, 5년 평균, 10년 평균)는 그림으로만 들어 있어서, 그림을 꺼내 칸마다 OCR 로 읽는다.

검증
  - 12개 칸(11개 섹터 + S&P 500)의 이름이 모두 알려진 이름과 맞아야 한다.
  - 숫자는 모두 'NN.N' 형식이고 3~80 사이여야 한다.
  - S&P 500 칸의 세 값이 보고서 본문 글자("forward 12-month P/E ratio is X ... 5-year average (Y) ... 10-year average (Z)")와 같아야 한다.
검증에 실패하면 파일을 쓰지 않고 오류로 끝난다(이전 데이터가 그대로 남는다).

필요: poppler-utils(pdftotext, pdfimages), tesseract-ocr, Pillow
사용: python sector-pe/fetch.py            최신 보고서를 읽어 저장
      python sector-pe/fetch.py 111326     특정 날짜(MMDDYY) 보고서를 읽기만 함(시험용)
      python sector-pe/fetch.py chart.png  저장된 차트 그림으로 표만 읽기(시험용)
"""
import csv
import difflib
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageOps

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
BASE = "https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/EarningsInsight_{}.pdf"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 차트 머리글 → (키, 한국어 이름, 순서)
SECTORS = {
    "S&P 500": ("sp500", "S&P500", 0),
    "Info. Technology": ("it", "IT", 1),
    "Comm. Services": ("comm", "통신", 2),
    "Consumer Disc.": ("discretionary", "경기소비", 3),
    "Consumer Staples": ("staples", "필수소비", 4),
    "Health Care": ("health", "헬스케어", 5),
    "Financials": ("financials", "금융", 6),
    "Industrials": ("industrials", "산업재", 7),
    "Energy": ("energy", "에너지", 8),
    "Materials": ("materials", "소재", 9),
    "Utilities": ("utilities", "유틸", 10),
    "Real Estate": ("realestate", "부동산", 11),
}


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def latest_report(tmp, only=None):
    """오늘부터 거슬러 올라가며 보고서 PDF 를 찾는다. (pdf 경로, 보고서 날짜). only 는 특정 날짜(date)."""
    today = datetime.now(KST).date()
    for back in range(0, 21):
        d = only or today - timedelta(days=back)
        url = BASE.format(d.strftime("%m%d%y"))
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                body = r.read()
        except Exception:  # noqa: BLE001  (없는 날짜는 404)
            continue
        if body[:4] == b"%PDF":
            path = os.path.join(tmp, "ei.pdf")
            with open(path, "wb") as f:
                f.write(body)
            return path, d.isoformat()
        if only:
            break
        time.sleep(0.5)
    raise RuntimeError("최근 21일 안에 FactSet Earnings Insight 보고서를 찾지 못함")


def chart_image(pdf, tmp):
    """'Forward 12M P/E Ratio: Sector Level' 쪽에서 선행 PER 차트 그림을 꺼낸다."""
    pages = run(["pdftotext", "-layout", pdf, "-"]).split("\f")
    page = next(i + 1 for i, t in enumerate(pages) if "Forward 12M P/E Ratio: Sector Level" in t)
    run(["pdfimages", "-f", str(page), "-l", str(page), "-png", pdf, os.path.join(tmp, "img")])
    for path in sorted(glob.glob(os.path.join(tmp, "img-*.png"))):
        im = Image.open(path)
        if im.width < 600:
            continue
        title = ocr(im.crop((0, 0, im.width, int(im.height * 0.08))), psm=7)
        if "P/E" in title or "P/E".lower() in title.lower():
            return im, pages[page - 1], "\n".join(pages)
    raise RuntimeError("선행 PER 차트 그림을 찾지 못함")


def ocr(im, psm=7, digits=False):
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        im.save(f.name)
        cmd = ["tesseract", f.name, "-", "--psm", str(psm)]
        if digits:
            cmd += ["-c", "tessedit_char_whitelist=0123456789."]
        return run(cmd).strip()


def dark(px):
    r, g, b = px[:3]
    return r + g + b < 3 * 150


def table_grid(im):
    """그림 아래 표의 가로·세로 선 위치를 찾는다."""
    w, h = im.size
    rgb = im.convert("RGB")
    lo = int(h * 0.80)
    rows = []
    for y in range(lo, h):
        n = sum(1 for x in range(0, w, 2) if dark(rgb.getpixel((x, y))))
        if n > w * 0.30:
            rows.append(y)
    # 붙어 있는 선은 하나로
    ys = [y for i, y in enumerate(rows) if i == 0 or y - rows[i - 1] > 2]
    if len(ys) < 4:
        raise RuntimeError(f"표 가로선을 찾지 못함: {ys}")
    top, bottom = ys[0], ys[-1]
    cols = []
    for x in range(w):
        n = sum(1 for y in range(top, bottom) if dark(rgb.getpixel((x, y))))
        if n > (bottom - top) * 0.7:
            cols.append(x)
    xs = [x for i, x in enumerate(cols) if i == 0 or x - cols[i - 1] > 2]
    return xs, ys


def even_run(vals, count, tol=3):
    """간격이 일정한 선 count 개를 고른다(그림 테두리 같은 다른 선은 버린다)."""
    for i in range(len(vals) - count + 1):
        seq = vals[i:i + count]
        gaps = [b - a for a, b in zip(seq, seq[1:])]
        if max(gaps) - min(gaps) <= tol and min(gaps) > 5:
            return seq
    raise RuntimeError(f"간격이 일정한 선 {count}개를 찾지 못함: {vals}")


def read_table(im):
    xs, ys = table_grid(im)
    # 섹터 12칸의 경계선 13개, 데이터 3줄의 경계선 4개
    xs = even_run(xs, 13)
    data_ys = even_run(ys, 4)
    above = [y for y in ys if y < data_ys[0]]
    head_top = above[-1] if above else data_ys[0] - 2 * (data_ys[1] - data_ys[0])
    cols = []
    for i in range(12):
        x0, x1 = xs[i] + 2, xs[i + 1] - 1
        hcell = im.crop((x0, head_top + 2, x1, data_ys[0] - 1))
        hcell = ImageOps.grayscale(hcell).resize((hcell.width * 4, hcell.height * 4), Image.LANCZOS)
        head = " ".join(ocr(hcell, psm=6).split())
        vals = []
        for r in range(3):
            cell = im.crop((x0, data_ys[r] + 2, x1, data_ys[r + 1] - 1))
            vals.append(read_number(cell))
        cols.append((head, vals))
    return cols


def norm(v):
    # 표의 숫자는 항상 소수점 한 자리. OCR 이 점을 놓치면 마지막 자리 앞에 점을 넣는다.
    v = v.strip()
    if re.fullmatch(r"\d{2,3}", v):
        v = v[:-1] + "." + v[-1]
    return v


def read_number(cell):
    """확대 배율을 바꿔 세 번 읽고, 두 번 이상 같은 값만 받는다."""
    reads = []
    for scale in (4, 6, 8):
        g = ImageOps.grayscale(cell).resize((cell.width * scale, cell.height * scale), Image.LANCZOS)
        g = g.point(lambda p: 255 if p > 160 else 0)
        g = ImageOps.expand(g, border=30, fill=255)
        reads.append(norm(ocr(g, psm=7, digits=True)))
    best = max(set(reads), key=reads.count)
    if reads.count(best) < 2:
        raise RuntimeError(f"숫자 판독이 일치하지 않음: {reads}")
    return best


def parse(cols):
    names = list(SECTORS)
    out = {}
    for head, vals in cols:
        m = difflib.get_close_matches(head, names, n=1, cutoff=0.6)
        if not m:
            raise RuntimeError(f"섹터 이름을 알 수 없음: {head!r}")
        if m[0] in out:
            raise RuntimeError(f"섹터 이름 중복: {head!r} → {m[0]}")
        nums = []
        for v in vals:
            if not re.fullmatch(r"\d{1,2}\.\d", v):
                raise RuntimeError(f"{m[0]} 숫자 형식이 이상함: {vals}")
            x = float(v)
            if not 3 <= x <= 80:
                raise RuntimeError(f"{m[0]} 숫자 범위가 이상함: {vals}")
            nums.append(x)
        out[m[0]] = nums
    if len(out) != 12:
        raise RuntimeError(f"섹터 수가 12개가 아님: {list(out)}")
    return out


def check_text(values, text):
    t = " ".join(text.split())
    m = re.search(r"forward 12-month P/E ratio (?:for the S&P 500 )?is (\d+\.\d)[^.]*?5-year average (?:of )?\(?(\d+\.\d)\)?[^.]*?10-year average (?:of )?\(?(\d+\.\d)\)?", t)
    if not m:
        raise RuntimeError("본문에서 S&P 500 선행 PER 문장을 찾지 못함")
    want = [float(x) for x in m.groups()]
    if values["S&P 500"] != want:
        raise RuntimeError(f"S&P 500 값이 본문과 다름: 표 {values['S&P 500']} / 본문 {want}")


def write(values, report_date):
    os.makedirs(OUT, exist_ok=True)
    rows = sorted(((SECTORS[k], v) for k, v in values.items()), key=lambda x: x[0][2])
    with open(os.path.join(OUT, "sectors.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["key", "name", "report_date", "fwd_pe", "avg_5y", "avg_10y", "vs_5y_pct", "vs_10y_pct"])
        for (key, name, _), (pe, a5, a10) in rows:
            w.writerow([key, name, report_date, pe, a5, a10, round((pe / a5 - 1) * 100, 1), round((pe / a10 - 1) * 100, 1)])
    meta = {"generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"), "report_date": report_date,
            "source": "FactSet Earnings Insight"}
    with open(os.path.join(HERE, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg.endswith(".png"):  # 시험용: 차트 그림만 읽기
        values = parse(read_table(Image.open(arg)))
        for k, v in values.items():
            print(k, v)
        return
    only = datetime.strptime(arg, "%m%d%y").date() if re.fullmatch(r"\d{6}", arg) else None
    with tempfile.TemporaryDirectory() as tmp:
        pdf, report_date = latest_report(tmp, only)
        im, _, text = chart_image(pdf, tmp)
        cols = read_table(im)
        print(json.dumps(cols, ensure_ascii=False))
        values = parse(cols)
        check_text(values, text)
    if only:  # 시험용: 지난 보고서는 읽기만 하고 쓰지 않는다
        print("check only", report_date, values)
        return
    write(values, report_date)
    print("report", report_date)
    for k, v in values.items():
        print(k, v)


if __name__ == "__main__":
    main()
