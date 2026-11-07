# -*- coding: utf-8 -*-
"""Ядро поиска: запрос в несколько поисковиков -> загрузка страниц -> выжимка сути.

Не зависит от Kivy, поэтому можно проверять на компьютере:
    python search_core.py "ваш запрос"
"""
import base64
import math
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "ru,en;q=0.8",
}
TIMEOUT = 10
MAX_PAGE_BYTES = 1_500_000
MAX_TEXT_CHARS = 25_000


# --------------------------------------------------------------------------
# Вспомогательное
# --------------------------------------------------------------------------
def _clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _soup(markup, **kw):
    return BeautifulSoup(markup, "html.parser", **kw)


def _item(title, url, snippet=""):
    return {"title": _clean(title), "url": url, "snippet": _clean(snippet)}


def _unwrap(url):
    """Раскрывает ссылки-редиректы DuckDuckGo и Bing."""
    if url.startswith("//"):
        url = "https:" + url
    p = urlparse(url)
    host = p.netloc.lower()
    q = parse_qs(p.query)
    if host.endswith("duckduckgo.com") and "uddg" in q:
        return unquote(q["uddg"][0])
    if host.endswith("bing.com") and p.path.startswith("/ck/") and "u" in q:
        v = q["u"][0]
        if v.startswith("a1"):
            v = v[2:]
            try:
                v += "=" * (-len(v) % 4)
                return base64.urlsafe_b64decode(v).decode("utf-8")
            except Exception:
                return url
    return url


def _norm_url(u):
    p = urlparse(u)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host + p.path.rstrip("/")


# --------------------------------------------------------------------------
# Поисковики (HTML-версии, без API-ключей)
# --------------------------------------------------------------------------
def search_duckduckgo(query, limit, **_):
    r = requests.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for res in _soup(r.text).select("div.result"):
        if "result--ad" in (res.get("class") or []):
            continue
        a = res.select_one("a.result__a")
        if not a:
            continue
        sn = res.select_one(".result__snippet")
        out.append(_item(a.get_text(), _unwrap(a.get("href", "")), sn.get_text() if sn else ""))
        if len(out) >= limit:
            break
    return out


def search_bing(query, limit, **_):
    r = requests.get(
        "https://www.bing.com/search?q=" + quote_plus(query) + "&count=" + str(limit),
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for li in _soup(r.text).select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a:
            continue
        p = li.select_one(".b_caption p") or li.select_one("p")
        out.append(_item(a.get_text(), _unwrap(a.get("href", "")), p.get_text() if p else ""))
        if len(out) >= limit:
            break
    return out


def search_brave(query, limit, **_):
    r = requests.get(
        "https://search.brave.com/search?source=web&q=" + quote_plus(query),
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for div in _soup(r.text).select('div.snippet[data-type="web"]'):
        a = div.select_one('a[href^="http"]')
        if not a:
            continue
        t = div.select_one(".title") or div.select_one(".snippet-title") or a
        d = div.select_one(".snippet-description") or div.select_one(".content")
        out.append(_item(t.get_text(), a.get("href"), d.get_text() if d else ""))
        if len(out) >= limit:
            break
    return out


def search_mojeek(query, limit, **_):
    r = requests.get(
        "https://www.mojeek.com/search?q=" + quote_plus(query),
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for li in _soup(r.text).select("ul.results-standard > li"):
        a = li.select_one("a.title") or li.select_one("h2 a")
        if not a:
            continue
        p = li.select_one("p.s")
        out.append(_item(a.get_text(), a.get("href"), p.get_text() if p else ""))
        if len(out) >= limit:
            break
    return out


def search_wikipedia(query, limit, **_):
    lang = "ru" if re.search(r"[а-яА-ЯёЁ]", query) else "en"
    r = requests.get(
        "https://%s.wikipedia.org/w/api.php" % lang,
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": min(limit, 5),
        },
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for it in r.json().get("query", {}).get("search", []):
        title = it["title"]
        url = "https://%s.wikipedia.org/wiki/%s" % (lang, quote_plus(title.replace(" ", "_")))
        out.append(_item(title, url, _soup(it.get("snippet", "")).get_text()))
    return out


def search_searxng(query, limit, searx_url="", **_):
    """Мета-поисковик SearXNG (свой или публичный инстанс с включённым JSON).
    Через него можно получить выдачу Google, Yandex и других."""
    if not searx_url:
        return []
    r = requests.get(
        searx_url.rstrip("/") + "/search",
        params={"q": query, "format": "json"},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return [
        _item(x.get("title", ""), x.get("url", ""), x.get("content", ""))
        for x in r.json().get("results", [])[:limit]
    ]


ENGINES = {
    "DuckDuckGo": search_duckduckgo,
    "Bing": search_bing,
    "Brave": search_brave,
    "Mojeek": search_mojeek,
    "Wikipedia": search_wikipedia,
    "SearXNG": search_searxng,
}
DEFAULT_ENGINES = ["DuckDuckGo", "Bing", "Brave", "Mojeek", "Wikipedia"]


def _safe_engine(name, query, limit, searx_url):
    try:
        res = ENGINES[name](query, limit, searx_url=searx_url)
        res = [x for x in res if x["url"].startswith("http") and x["title"]]
        return name, res, None
    except Exception as e:  # сеть, блокировка, смена вёрстки
        return name, [], "%s: %s" % (type(e).__name__, e)


# --------------------------------------------------------------------------
# Загрузка страницы и извлечение основного текста
# --------------------------------------------------------------------------
_JUNK = (
    "cookie", "javascript", "подпишитесь", "подписывайтесь", "все права защищены",
    "политика конфиденциальности", "reserved", "subscribe", "sign up", "log in",
)


def fetch_text(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "").lower()
    if ctype and "html" not in ctype and "xml" not in ctype:
        r.close()
        return ""
    raw = b""
    for chunk in r.iter_content(65536):
        raw += chunk
        if len(raw) > MAX_PAGE_BYTES:
            break
    r.close()
    enc = None
    if "charset=" in ctype:
        enc = ctype.split("charset=")[-1].split(";")[0].strip()
    return extract_main_text(_soup(raw, from_encoding=enc))


def extract_main_text(soup):
    for t in soup(["script", "style", "noscript", "svg", "nav", "footer",
                   "aside", "form", "header", "iframe", "button"]):
        t.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    parts, seen, total = [], set(), 0
    for el in root.find_all(["p", "li", "h1", "h2", "h3", "blockquote"]):
        t = _clean(el.get_text(" "))
        low = t.lower()
        if len(t) < 40 or len(t.split()) < 6 or t in seen:
            continue
        if any(j in low for j in _JUNK):
            continue
        seen.add(t)
        parts.append(t)
        total += len(t)
        if total > MAX_TEXT_CHARS:
            break
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Выжимка сути (экстрактивная, без нейросетей — быстро и офлайн)
# --------------------------------------------------------------------------
STOP = set("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня
еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там
потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже
себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас
были куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про всего
них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда
конечно всю между это как так также который которые которая которого
the and for that with this from are was were have has had not you your but can will all any one our their they them
his her its who what when where which while about into than then there these those been being also more most such
""".split())

_WORD = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]{3,}")
_SENT = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z0-9«\"“(])")


def stems(text):
    """Грубое приведение к основе: первые 6 букв (работает и для русского, и для английского)."""
    out = []
    for w in _WORD.findall(text):
        w = w.lower().replace("ё", "е")
        if w not in STOP:
            out.append(w[:6])
    return out


def split_sentences(text):
    out = []
    for para in text.split("\n"):
        for s in _SENT.split(para):
            s = _clean(s)
            if 40 <= len(s) <= 320 and len(s.split()) >= 6:
                out.append(s)
    return out


def _similar(a, b, thr=0.5):
    if not a or not b:
        return False
    return len(a & b) / float(len(a | b)) >= thr


def summarize(query, docs, per_doc=2, total=6):
    """docs: список {'text', 'snippet'}.
    Возвращает (общая_суть [(предложение, индекс_источника)], по_источникам [[предложения]])."""
    qset = set(stems(query))
    doc_sents = []
    for d in docs:
        sents = split_sentences(d.get("text", ""))[:250]
        if not sents and d.get("snippet"):
            sents = [d["snippet"]]
        doc_sents.append(sents)

    df, tf = Counter(), Counter()
    for sents in doc_sents:
        seen = set()
        for s in sents:
            for w in stems(s):
                tf[w] += 1
                seen.add(w)
        for w in seen:
            df[w] += 1

    def word_score(w):
        sc = df[w] * (1 + math.log(tf[w]))  # слова, встречающиеся на многих сайтах, весомее
        return sc * 2 + 3 if w in qset else sc

    candidates = []  # (score, doc_idx, order, sentence)
    for di, sents in enumerate(doc_sents):
        for si, s in enumerate(sents):
            ws = stems(s)
            if not ws:
                continue
            uniq = set(ws)
            score = sum(word_score(w) for w in uniq) / (len(ws) ** 0.6)
            if si < 3:
                score *= 1.15
            candidates.append((score, di, si, s, uniq))

    per_source = [[] for _ in docs]
    for di in range(len(docs)):
        picked = []
        for score, d_i, si, s, uniq in sorted((c for c in candidates if c[1] == di), reverse=True):
            if any(_similar(uniq, p[3]) for p in picked):
                continue
            picked.append((si, s, score, uniq))
            if len(picked) >= per_doc:
                break
        per_source[di] = [p[1] for p in sorted(picked)]

    overall, used = [], Counter()
    for score, di, si, s, uniq in sorted(candidates, reverse=True):
        if used[di] >= 2:
            continue
        if any(_similar(uniq, o[2]) for o in overall):
            continue
        overall.append((s, di, uniq))
        used[di] += 1
        if len(overall) >= total:
            break
    return [(s, di) for s, di, _ in overall], per_source


# --------------------------------------------------------------------------
# Главная функция
# --------------------------------------------------------------------------
def run_search(query, engines=None, per_engine=8, max_pages=12, searx_url="", progress=None):
    def say(msg):
        if progress:
            progress(msg)

    engines = [e for e in (engines or DEFAULT_ENGINES) if e in ENGINES]
    say("Ищу в поисковиках: " + ", ".join(engines) + "…")

    by_engine, errors = {}, {}
    with ThreadPoolExecutor(max_workers=max(1, len(engines))) as ex:
        futs = [ex.submit(_safe_engine, n, query, per_engine, searx_url) for n in engines]
        for f in futs:
            name, res, err = f.result()
            by_engine[name] = res
            if err:
                errors[name] = err

    # Объединяем выдачу «по кругу», убираем дубли; ссылки, найденные
    # несколькими поисковиками, считаем более релевантными.
    merged, index = [], {}
    longest = max([len(v) for v in by_engine.values()] or [0])
    for i in range(longest):
        for name in engines:
            lst = by_engine.get(name, [])
            if i < len(lst):
                it = lst[i]
                key = _norm_url(it["url"])
                if key in index:
                    if name not in index[key]["engines"]:
                        index[key]["engines"].append(name)
                else:
                    it = dict(it, engines=[name])
                    index[key] = it
                    merged.append(it)
    merged.sort(key=lambda x: -len(x["engines"]))  # сортировка устойчивая
    merged = merged[:max_pages]
    say("Найдено страниц: %d. Читаю сайты…" % len(merged))

    def load(it):
        try:
            it["text"] = fetch_text(it["url"])
        except Exception:
            it["text"] = ""
        return it

    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for _ in as_completed([ex.submit(load, it) for it in merged]):
            done += 1
            say("Прочитано сайтов: %d из %d…" % (done, len(merged)))

    docs = [it for it in merged if it.get("text") or it.get("snippet")]
    say("Выделяю суть…")
    overall, per_source = summarize(query, docs)

    sources = []
    for it, ess in zip(docs, per_source):
        sources.append({
            "title": it["title"],
            "url": it["url"],
            "engines": it["engines"],
            "essence": ess,
            "full_text": bool(it.get("text")),
        })
    return {
        "query": query,
        "summary": [{"text": s, "source": di} for s, di in overall],
        "sources": sources,
        "errors": errors,
    }


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "что такое квантовый компьютер"
    res = run_search(q, progress=lambda m: print("..", m, file=sys.stderr))
    print("\n=== СУТЬ ===")
    for s in res["summary"]:
        print("-", s["text"], "[%d]" % (s["source"] + 1))
    print("\n=== ИСТОЧНИКИ ===")
    for i, s in enumerate(res["sources"], 1):
        print("%d. %s (%s)\n   %s" % (i, s["title"], ", ".join(s["engines"]), s["url"]))
    if res["errors"]:
        print("\nОшибки поисковиков:", res["errors"])
