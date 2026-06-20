#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Спектральный анализ литературных произведений.

Скрипт загружает текст произведения по ссылке, делит его на «страницы»
(одна страница = заданное число слов), считает на каждой странице упоминания
цветов и строит «спектр» книги — последовательность цветовых полос вдоль всего
текста плюс сводное распределение цветов.

Результат сохраняется как самодостаточная HTML-страница (со встроенным SVG),
которую можно опубликовать через GitHub Pages.

Зависимостей нет — только стандартная библиотека Python 3.

Пример:
    python spectrum.py "http://az.lib.ru/d/dostoewskij_f_m/text_0100.shtml" \
        --words-per-page 300 --output docs/index.html \
        --title "Ф. М. Достоевский. Братья Карамазовы"
"""

from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
import urllib.request
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
#  Словарь цветов
# --------------------------------------------------------------------------- #
#
#  Текст приводится к нижнему регистру, а буква «ё» заменяется на «е», поэтому
#  все основы ниже записаны через «е».
#
#  Для каждого цвета задаётся набор основ (stems). Основа совмещается с группой
#  прилагательных окончаний ADJ, что отсекает большинство ложных совпадений
#  (напр. «серебро», «синтаксис», «бурный» не считаются за цвет).

# Прилагательные окончания (включая уменьшительные и «-оватый»).
ADJ = (
    r"(?:ый|ого|ому|ым|ом|ая|ой|ую|ое|ые|ых|ыми"
    r"|ий|его|ему|им|ем|яя|ей|юю|ее|ие|их|ими"
    r"|оват\w{0,4}|еньк\w{0,3}|оньк\w{0,3})"
)

# Каждый цвет: (ключ, человекочитаемое имя, hex, [основы])
# Порядок — спектральный (для сводной полосы): тёплые -> холодные -> ахроматические.
COLOR_DEFS = [
    ("red",    "красный",     "#E63946",
        ["красн", "ал", "багров", "багрян", "рдян", "рдел", "румян", "кровав"]),
    ("orange", "оранжевый",   "#E76F51",
        ["оранжев", "рыж", "морков"]),
    ("yellow", "жёлтый",      "#FFC300",
        ["желт", "золот", "золотист", "янтарн", "лимонн", "соломенн"]),
    ("green",  "зелёный",     "#2A9D8F",
        ["зелен", "изумрудн", "салатов", "малахитов"]),
    ("cyan",   "голубой",     "#48CAE4",
        ["голуб", "бирюзов", "лазурн"]),
    ("blue",   "синий",       "#1D5FB0",
        ["син", "васильков"]),
    ("violet", "фиолетовый",  "#7209B7",
        ["фиолетов", "лилов", "сиренев", "пурпурн", "фиалков"]),
    ("pink",   "розовый",     "#FF7BAC",
        ["розов"]),
    ("brown",  "коричневый",  "#7A4B2B",
        ["коричнев", "бур", "каштанов", "шоколадн", "смугл", "кофейн"]),
    ("gray",   "серый",       "#9AA0A6",
        ["сер", "сед", "серебрист", "пепельн", "дымчат"]),
    ("black",  "чёрный",      "#2B2B2B",
        ["черн", "смолян"]),
    ("white",  "белый",       "#FFFFFF",
        ["бел", "белоснежн", "бледн", "молочн"]),
]


# Глагольные и существительные формы цвета (то, что не покрывается прилагательными).
# PFX — необязательная приставка инхоативных глаголов (покраснел, побледнел …);
# основы вроде «красне», «бледне» специфичны, поэтому ложных срабатываний почти нет
# («прекрасный» не содержит «красне»).
PFX = r"(?:по|за|при|раз|рас|вы|об|на)?"
VRB = r"(?:ть|л\w{0,2}|ет\w{0,2}|еет|еют|ют|я|в|вш\w{0,3}|ется|лся|нн\w{0,3})"
NOUN = r"(?:а|у|ы|ой|е|ою|ам|ами|ах)"

# Доп. формы по цветам: глаголы (изменение цвета) и существительные цвета.
EXTRA = {
    "red":    [PFX + "красне" + VRB, "краснот" + NOUN,
               r"румянц\w{0,2}", "румянец", r"багрянц\w{0,2}", "багрянец"],
    "yellow": [PFX + "желте" + VRB, PFX + "золоти" + VRB,
               "желтизн" + NOUN, "позолот" + NOUN],
    "green":  [PFX + "зелене" + VRB, r"зелень\w{0,2}", r"зелени"],
    "cyan":   [PFX + "голубе" + VRB],
    "blue":   [PFX + "сине" + VRB, "синев" + NOUN, r"синь\w{0,2}", r"сини"],
    "pink":   [PFX + "розове" + VRB],
    "black":  [PFX + "черне" + VRB, "чернот" + NOUN],
    "white":  [PFX + "беле" + VRB, PFX + "бледне" + VRB,
               "белизн" + NOUN, r"бледност\w{0,2}"],
    "gray":   [PFX + "сере" + VRB, "седин" + NOUN],
}


def _build_patterns():
    pats = []
    for key, name, hexc, stems in COLOR_DEFS:
        stem_alt = "|".join(sorted(stems, key=len, reverse=True))
        body = r"(?:" + stem_alt + r")" + ADJ
        extras = EXTRA.get(key)
        if extras:
            body = body + "|" + "|".join(extras)
        rx = re.compile(r"\b(?:" + body + r")\b")
        pats.append((key, name, hexc, rx))
    return pats


COLOR_PATTERNS = _build_patterns()
HEX_BY_KEY = {key: hexc for key, name, hexc, stems in COLOR_DEFS}
NAME_BY_KEY = {key: name for key, name, hexc, stems in COLOR_DEFS}
COLOR_ORDER = [key for key, *_ in COLOR_DEFS]

WORD_RE = re.compile(r"[а-яa-z0-9]+")


# --------------------------------------------------------------------------- #
#  Загрузка и извлечение текста
# --------------------------------------------------------------------------- #

def fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (book-spectrum)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def decode(raw: bytes) -> str:
    """Определяем кодировку: az.lib.ru и многие старые сайты — cp1251."""
    head = raw[:2048].lower()
    if b"utf-8" in head:
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
    if b"windows-1251" in head or b"cp1251" in head:
        return raw.decode("cp1251", "replace")
    # эвристика: пробуем utf-8, иначе cp1251
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", "replace")


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)


def extract_text(page_html: str) -> str:
    """Вытащить «тело» произведения.

    Для страниц az.lib.ru текст находится между маркерами
    «Section Begins» и «Section Ends». Для остальных сайтов — общий разбор.
    """
    begin = page_html.find("Section Begins")
    end = page_html.find("Section Ends")
    if begin != -1 and end != -1 and end > begin:
        # начать после закрытия комментария <!--Section Begins-->
        close = page_html.find("-->", begin)
        begin = close + 3 if -1 < close < end else begin
        # закончить до открытия комментария <!--Section Ends-->
        open_end = page_html.rfind("<!--", begin, end)
        if open_end != -1:
            end = open_end
        body = page_html[begin:end]
    else:
        body = page_html

    body = _SCRIPT_RE.sub(" ", body)
    body = _TAG_RE.sub(" ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\xa0]+", " ", body)
    body = re.sub(r"\s*\n\s*", "\n", body)
    return body.strip()


# --------------------------------------------------------------------------- #
#  Анализ
# --------------------------------------------------------------------------- #

def normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def analyze(text: str, words_per_page: int):
    """Вернуть результат анализа.

    Возвращает dict с полями:
      total_words, pages, words_per_page,
      totals: {color_key: count},
      page_data: [ {counts:{key:n}, total:n, words:n} ... ]
    """
    norm = normalize(text)
    # позиции всех слов
    tokens = list(WORD_RE.finditer(norm))
    total_words = len(tokens)
    if total_words == 0:
        raise ValueError("В тексте не найдено слов — возможно, не та страница/кодировка.")

    n_pages = max(1, math.ceil(total_words / words_per_page))
    totals = {k: 0 for k in COLOR_ORDER}
    page_data = []

    for p in range(n_pages):
        lo = p * words_per_page
        hi = min(total_words, lo + words_per_page)
        if lo >= total_words:
            break
        start = tokens[lo].start()
        finish = tokens[hi - 1].end()
        chunk = norm[start:finish]
        # normalize() сохраняет длину строки, поэтому те же индексы дают
        # исходный (не приведённый) текст страницы для показа при наведении.
        page_text = text[start:finish].strip()

        counts = {}
        page_total = 0
        for key, name, hexc, rx in COLOR_PATTERNS:
            c = len(rx.findall(chunk))
            if c:
                counts[key] = c
                totals[key] += c
                page_total += c
        page_data.append({
            "counts": counts,
            "total": page_total,
            "words": hi - lo,
            "text": page_text,
        })

    return {
        "total_words": total_words,
        "pages": len(page_data),
        "words_per_page": words_per_page,
        "totals": totals,
        "total_color_words": sum(totals.values()),
        "page_data": page_data,
    }


# --------------------------------------------------------------------------- #
#  Визуализация (SVG)
# --------------------------------------------------------------------------- #

def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(c)) for c in rgb)


def blend(counts: dict):
    """Смешать цвета страницы пропорционально частоте упоминаний."""
    total = sum(counts.values())
    if total == 0:
        return None
    r = g = b = 0.0
    for key, c in counts.items():
        cr, cg, cb = _hex_to_rgb(HEX_BY_KEY[key])
        w = c / total
        r += cr * w
        g += cg * w
        b += cb * w
    return (r, g, b)


def flow_svg(page_data) -> str:
    """Полоса-«спектр»: каждая страница — вертикальный штрих смешанного цвета.

    Насыщенность штриха зависит от «цветности» страницы (числа упоминаний):
    бледные участки — мало цвета, яркие — много.
    """
    n = len(page_data)
    height = 100
    # масштаб насыщенности — по 90-му перцентилю числа цветослов на странице
    nonzero = sorted(d["total"] for d in page_data if d["total"] > 0)
    if nonzero:
        ref = nonzero[min(len(nonzero) - 1, int(len(nonzero) * 0.9))]
        ref = max(1, ref)
    else:
        ref = 1

    rects = []
    # Страницы без цвета — отдельное светло-серое «поле». Цветные страницы
    # подмешиваются поверх БЕЛОГО фона, поэтому упоминания белого дают яркую
    # белую полосу, заметную на сером поле (т.е. белый ≠ «нет цвета»).
    empty_fill = "#dde1e6"
    bg = (255, 255, 255)
    for i, d in enumerate(page_data):
        col = blend(d["counts"])
        if col is None:
            fill = empty_fill
        else:
            alpha = 0.30 + 0.70 * min(1.0, d["total"] / ref)
            mixed = tuple(col[j] * alpha + bg[j] * (1 - alpha) for j in range(3))
            fill = _rgb_to_hex(mixed)
        rects.append(f'<rect x="{i}" y="0" width="1.02" height="{height}" fill="{fill}"/>')

    return (
        f'<svg class="flow" viewBox="0 0 {n} {height}" preserveAspectRatio="none" '
        f'shape-rendering="crispEdges" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="Спектр книги по страницам">'
        + "".join(rects) + "</svg>"
    )


def aggregate_svg(totals: dict) -> str:
    """Сводная полоса: доли каждого цвета во всём тексте (спектральный порядок)."""
    total = sum(totals.values())
    width = 1000.0
    height = 60
    if total == 0:
        return f'<svg viewBox="0 0 {width:.0f} {height}"></svg>'
    x = 0.0
    segs = []
    for key in COLOR_ORDER:
        c = totals.get(key, 0)
        if not c:
            continue
        w = width * c / total
        segs.append(
            f'<rect x="{x:.2f}" y="0" width="{w:.2f}" height="{height}" '
            f'fill="{HEX_BY_KEY[key]}" stroke="#d9d9d9" stroke-width="1" '
            f'vector-effect="non-scaling-stroke">'
            f'<title>{NAME_BY_KEY[key]}: {c}</title></rect>'
        )
        x += w
    return (
        f'<svg class="aggregate" viewBox="0 0 {width:.0f} {height}" '
        f'preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="Сводное распределение цветов">'
        + "".join(segs) + "</svg>"
    )


# --------------------------------------------------------------------------- #
#  HTML-страница
# --------------------------------------------------------------------------- #

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — спектр</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0 16px 64px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    color: #1a1a1a; background: #fafafa; line-height: 1.5;
  }}
  .wrap {{ max-width: 1000px; margin: 0 auto; }}
  header {{ padding: 40px 0 8px; }}
  h1 {{ font-size: 1.7rem; margin: 0 0 4px; }}
  .sub {{ color: #666; font-size: .95rem; }}
  .sub a {{ color: #1d5fb0; }}
  h2 {{ font-size: 1.1rem; margin: 40px 0 10px; }}
  .flow-box {{
    position: relative; width: 100%; height: 140px; border-radius: 10px;
    overflow: hidden; border: 1px solid #e3e3e3; background: #fff; cursor: crosshair;
  }}
  svg.flow {{ width: 100%; height: 100%; display: block; }}
  .cursor {{ position:absolute; top:0; bottom:0; width:1px; background:rgba(0,0,0,.55);
             pointer-events:none; display:none; }}
  .axis {{ display:flex; justify-content:space-between; color:#888; font-size:.8rem; margin-top:6px; }}
  .page-view {{ margin-top:14px; border:1px solid #e3e3e3; border-radius:10px; background:#fff; }}
  .pv-head {{ padding:10px 14px; border-bottom:1px solid #f0f0f0; font-size:.9rem;
              display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
  .pv-text {{ padding:12px 14px; max-height:280px; overflow:auto; white-space:pre-wrap;
              font-size:.93rem; line-height:1.6; color:#222; }}
  .chip {{ display:inline-flex; align-items:center; gap:5px; background:#f4f4f5;
           border-radius:20px; padding:2px 9px; font-size:.8rem; }}
  .chip i {{ width:11px; height:11px; border-radius:3px; display:inline-block;
             border:1px solid rgba(0,0,0,.15); }}
  .muted {{ color:#999; }}
  .agg-box {{ width:100%; height:60px; border-radius:8px; overflow:hidden; border:1px solid #e3e3e3; }}
  svg.aggregate {{ width:100%; height:100%; display:block; }}
  .stats {{ display:flex; flex-wrap:wrap; gap:14px; margin:14px 0 0; }}
  .stat {{ background:#fff; border:1px solid #e9e9e9; border-radius:10px; padding:12px 16px; min-width:130px; }}
  .stat .n {{ font-size:1.4rem; font-weight:700; }}
  .stat .l {{ color:#777; font-size:.8rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 10px; background:#fff;
           border:1px solid #ececec; border-radius:10px; overflow:hidden; }}
  th, td {{ padding: 9px 12px; text-align: left; border-bottom: 1px solid #f0f0f0; font-size:.95rem; }}
  th {{ background:#f6f6f6; font-weight:600; }}
  tr:last-child td {{ border-bottom: none; }}
  .sw {{ display:inline-block; width:16px; height:16px; border-radius:4px; vertical-align:-3px;
         margin-right:8px; border:1px solid rgba(0,0,0,.12); }}
  .bar {{ height:10px; border-radius:5px; }}
  td.num {{ text-align:right; font-variant-numeric: tabular-nums; }}
  footer {{ margin-top:48px; color:#999; font-size:.82rem; }}
  footer a {{ color:#888; }}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>{title}</h1>
  <div class="sub">Спектральный анализ · источник: <a href="{url}">{url}</a></div>
</header>

<div class="stats">
  <div class="stat"><div class="n">{total_words}</div><div class="l">слов всего</div></div>
  <div class="stat"><div class="n">{pages}</div><div class="l">страниц (по {wpp} слов)</div></div>
  <div class="stat"><div class="n">{total_color}</div><div class="l">упоминаний цвета</div></div>
  <div class="stat"><div class="n">{density}</div><div class="l">цветослов на страницу</div></div>
</div>

<h2>Спектр по ходу текста</h2>
<p class="sub">Слева направо — от начала к концу книги. Каждая полоса — одна страница;
её цвет — смесь упомянутых на ней цветов, яркость — насколько страница «цветная».
Светло-серое поле — страницы без цвета; упоминания белого видны как яркие
белые полосы.</p>
<div class="flow-box" id="flowBox">{flow}<div class="cursor" id="cursor"></div></div>
<div class="axis"><span>начало</span><span>конец</span></div>

<div class="page-view">
  <div class="pv-head" id="pvHead">Наведите курсор на спектр, чтобы прочитать текст страницы.</div>
  <div class="pv-text" id="pvText"></div>
</div>

<h2>Сводное распределение</h2>
<p class="sub">Доля каждого цвета среди всех цветовых упоминаний (в спектральном порядке).</p>
<div class="agg-box">{aggregate}</div>

<h2>Цвета по частоте</h2>
<table>
  <thead><tr><th>Цвет</th><th class="num">Упоминаний</th><th class="num">Доля</th><th style="width:32%">&nbsp;</th></tr></thead>
  <tbody>
  {rows}
  </tbody>
</table>

<footer>
  Сгенерировано {generated} · <a href="https://github.com/MaximSemin/book-spectrum">book-spectrum</a>.
  Анализ учитывает русские названия цветов (прилагательные и их формы).
</footer>
</div>
<!--PAGE_SCRIPT-->
</body>
</html>
"""

# JS вынесен из шаблона (чтобы фигурные скобки не конфликтовали с .format()).
# Подставляется через str.replace по маркеру <!--PAGE_SCRIPT-->.
PAGE_SCRIPT = """
<script id="pages-data" type="application/json">__PAGES__</script>
<script id="colors-meta" type="application/json">__COLORS__</script>
<script>
(function () {
  var PAGES = JSON.parse(document.getElementById('pages-data').textContent);
  var COLORS = JSON.parse(document.getElementById('colors-meta').textContent);
  var box = document.getElementById('flowBox');
  var cursor = document.getElementById('cursor');
  var head = document.getElementById('pvHead');
  var textEl = document.getElementById('pvText');
  if (!box || !PAGES.length) return;

  function chip(k, c) {
    var m = COLORS[k] || { n: k, h: '#ccc' };
    return '<span class="chip"><i style="background:' + m.h + '"></i>' +
           m.n + ' · ' + c + '</span>';
  }
  function show(i) {
    if (i < 0 || i >= PAGES.length) return;
    var p = PAGES[i];
    var chips = (p.c && p.c.length)
      ? p.c.map(function (x) { return chip(x[0], x[1]); }).join('')
      : '<span class="muted">цветов на странице нет</span>';
    head.innerHTML = '<b>Страница ' + (i + 1) + ' / ' + PAGES.length + '</b>' +
                     '<span class="muted">' + p.w + ' слов</span>' + chips;
    textEl.textContent = p.t;
  }
  function at(clientX) {
    var r = box.getBoundingClientRect();
    var x = (clientX - r.left) / r.width;
    if (x < 0) x = 0; if (x > 0.9999) x = 0.9999;
    cursor.style.display = 'block';
    cursor.style.left = (x * 100) + '%';
    show(Math.floor(x * PAGES.length));
  }
  box.addEventListener('mousemove', function (e) { at(e.clientX); });
  box.addEventListener('mouseleave', function () { cursor.style.display = 'none'; });
  box.addEventListener('touchstart', function (e) { at(e.touches[0].clientX); }, { passive: true });
  box.addEventListener('touchmove', function (e) { at(e.touches[0].clientX); e.preventDefault(); }, { passive: false });

  show(0);
})();
</script>
"""


def render_html(result: dict, title: str, url: str) -> str:
    totals = result["totals"]
    total_color = result["total_color_words"]
    pages = result["pages"]

    rows = []
    ranked = sorted(
        [(k, totals.get(k, 0)) for k in COLOR_ORDER],
        key=lambda kv: kv[1], reverse=True,
    )
    max_count = max((c for _, c in ranked), default=0)
    for key, c in ranked:
        if c == 0:
            continue
        share = (c / total_color * 100) if total_color else 0
        bar_w = (c / max_count * 100) if max_count else 0
        rows.append(
            f'<tr><td><span class="sw" style="background:{HEX_BY_KEY[key]}"></span>'
            f'{NAME_BY_KEY[key]}</td>'
            f'<td class="num">{c}</td>'
            f'<td class="num">{share:.1f}%</td>'
            f'<td><div class="bar" style="width:{bar_w:.1f}%;background:{HEX_BY_KEY[key]}"></div></td></tr>'
        )
    if not rows:
        rows.append('<tr><td colspan="4">Цветовых упоминаний не найдено.</td></tr>')

    density = (total_color / pages) if pages else 0

    page = PAGE_TEMPLATE.format(
        title=html.escape(title),
        url=html.escape(url, quote=True),
        total_words=f"{result['total_words']:,}".replace(",", " "),
        pages=pages,
        wpp=result["words_per_page"],
        total_color=f"{total_color:,}".replace(",", " "),
        density=f"{density:.1f}",
        flow=flow_svg(result["page_data"]),
        aggregate=aggregate_svg(totals),
        rows="\n  ".join(rows),
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )

    # Данные для интерактивного просмотра текста страниц при наведении.
    pages_payload = [
        {
            "w": d["words"],
            "c": sorted(d["counts"].items(), key=lambda kv: kv[1], reverse=True),
            "t": d["text"],
        }
        for d in result["page_data"]
    ]
    colors_payload = {k: {"n": NAME_BY_KEY[k], "h": HEX_BY_KEY[k]} for k in COLOR_ORDER}

    def _json(obj):
        # Безопасно для вставки внутрь <script>: экранируем "</".
        return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")

    script = (PAGE_SCRIPT
              .replace("__PAGES__", _json(pages_payload))
              .replace("__COLORS__", _json(colors_payload)))
    return page.replace("<!--PAGE_SCRIPT-->", script)


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(description="Спектральный анализ литературного произведения.")
    ap.add_argument("url", help="Ссылка на текст произведения (напр. страница az.lib.ru)")
    ap.add_argument("--words-per-page", "-w", type=int, default=300,
                    help="Среднее число слов на одной странице (по умолчанию 300)")
    ap.add_argument("--output", "-o", default="docs/index.html",
                    help="Путь к HTML-файлу результата (по умолчанию docs/index.html)")
    ap.add_argument("--title", "-t", default=None,
                    help="Заголовок страницы (по умолчанию — из URL)")
    args = ap.parse_args(argv)

    if args.words_per_page < 1:
        ap.error("--words-per-page должно быть положительным числом")

    print(f"Загрузка: {args.url}", file=sys.stderr)
    raw = fetch(args.url)
    text = extract_text(decode(raw))
    print(f"Извлечено символов: {len(text)}", file=sys.stderr)

    result = analyze(text, args.words_per_page)
    print(f"Слов: {result['total_words']}, страниц: {result['pages']}, "
          f"упоминаний цвета: {result['total_color_words']}", file=sys.stderr)

    title = args.title or args.url
    out = render_html(result, title, args.url)

    import os
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Готово: {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
