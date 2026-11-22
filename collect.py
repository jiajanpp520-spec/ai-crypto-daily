#!/usr/bin/env python3
"""AI × 币圈 每日素材包：数据采集。

只负责抓数据，不调用 AI。抓完写到 output_dir/data/YYYY-MM-DD.json 和
YYYY-MM-DD_原始数据.md，之后在 Claude Code 里说「出今天的素材包」生成草稿。

用法：python3 collect.py [--config config.toml] [--date YYYY-MM-DD]
"""
import argparse
import html
import json
import re
import sys
import time
import tomllib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ai-crypto-daily/0.1"
CST = timezone(timedelta(hours=8))
ERRORS = []


def fetch(url, *, as_json=True, tries=4, timeout=30, accept_empty=True):
    """GET with retries; returns parsed JSON / bytes, or None after logging the error."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            if not as_json:
                return body
            data = json.loads(body)
            if accept_empty or data:
                return data
            last = "empty response"
        except Exception as e:  # network errors, HTTP errors, bad JSON
            last = repr(e)[:160]
        time.sleep(2 + 2 * i)
    ERRORS.append(f"{url[:90]} → {last}")
    return None


def clean(text, limit=400):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------- 行情 ----------

def collect_market(cfg):
    coins = cfg["coins"]
    ids = ",".join(c["coingecko_id"] for c in coins)
    rows = fetch("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd"
                 f"&ids={ids}&price_change_percentage=24h,7d") or []
    by_id = {r["id"]: r for r in rows}
    out = []
    for c in coins:
        r = by_id.get(c["coingecko_id"], {})
        fr = fetch(f"https://www.okx.com/api/v5/public/funding-rate?instId={c['okx_swap']}") or {}
        oi = fetch(f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={c['okx_swap']}") or {}
        fr0 = (fr.get("data") or [{}])[0]
        oi0 = (oi.get("data") or [{}])[0]
        out.append({
            "symbol": c["symbol"],
            "price_usd": r.get("current_price"),
            "change_24h_pct": r.get("price_change_percentage_24h_in_currency"),
            "change_7d_pct": r.get("price_change_percentage_7d_in_currency"),
            "volume_24h_usd": r.get("total_volume"),
            "market_cap_usd": r.get("market_cap"),
            "funding_rate_pct": round(float(fr0["fundingRate"]) * 100, 4) if fr0.get("fundingRate") else None,
            "open_interest_usd": float(oi0["oiUsd"]) if oi0.get("oiUsd") else None,
            "source": "CoinGecko / OKX",
        })
    fng = fetch("https://api.alternative.me/fng/?limit=2") or {}
    fng_rows = fng.get("data") or []
    fear_greed = {
        "today": fng_rows[0] if fng_rows else None,
        "yesterday": fng_rows[1] if len(fng_rows) > 1 else None,
        "source": "https://alternative.me/crypto/fear-and-greed-index/",
    }
    return {"coins": out, "fear_greed": fear_greed}


# ---------- 新闻 ----------

def parse_feed(raw, name, topic, since):
    items = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # 有些中文站的 RSS 里有裸 & 和控制字符，修一下再试
        text = raw.decode("utf-8", "replace")
        text = re.sub(r"&(?!#?\w+;)", "&amp;", text)
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        try:
            root = ET.fromstring(text.encode("utf-8"))
        except ET.ParseError as e:
            ERRORS.append(f"{name} RSS 解析失败 → {e}")
            return items
    atom = "{http://www.w3.org/2005/Atom}"
    for it in root.iter("item"):
        items.append((it.findtext("title"), it.findtext("link"),
                      it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date"),
                      it.findtext("description")))
    for it in root.iter(atom + "entry"):
        link = it.find(atom + "link")
        items.append((it.findtext(atom + "title"), link.get("href") if link is not None else None,
                      it.findtext(atom + "published") or it.findtext(atom + "updated"),
                      it.findtext(atom + "summary") or it.findtext(atom + "content")))
    out = []
    for title, link, date, desc in items:
        d = parse_date(date)
        if not link or (d and d < since):
            continue
        out.append({"source": name, "topic": topic, "title": clean(title, 200), "url": link.strip(),
                    "published": d.astimezone(CST).isoformat(timespec="minutes") if d else None,
                    "summary": clean(desc)})
    return out


def collect_news(cfg, since):
    def one(f):
        raw = fetch(f["url"], as_json=False, tries=3, timeout=120)
        return parse_feed(raw, f["name"], f["topic"], since) if raw else []
    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(one, cfg["feeds"]))
    news = [n for r in results for n in r]
    news.sort(key=lambda n: n["published"] or "", reverse=True)
    return news


# ---------- X 博主 ----------

def collect_kols(cfg, since):
    groups = cfg.get("kols", {})
    handles = [(g, h) for g, hs in groups.items() for h in hs]

    def one(gh):
        group, h = gh
        d = fetch(f"https://api.fxtwitter.com/2/profile/{h}/statuses", accept_empty=False)
        if not d:
            return []
        out = []
        for t in d.get("results") or []:
            author = (t.get("author") or {})
            if t.get("reposted_by") or t.get("replying_to") or author.get("screen_name", "").lower() != h.lower():
                continue
            created = parse_date(t.get("created_at"))
            if created and created < since:
                continue
            followers = author.get("followers") or 1
            out.append({"group": group, "handle": h, "name": author.get("name"), "followers": followers,
                        "url": t.get("url"), "created": created.astimezone(CST).isoformat(timespec="minutes") if created else None,
                        "text": clean(t.get("text"), 600), "views": t.get("views"), "likes": t.get("likes"),
                        "replies": t.get("replies"), "reposts": t.get("reposts"), "bookmarks": t.get("bookmarks"),
                        "views_per_follower": round((t.get("views") or 0) / followers, 3),
                        "has_video": bool((t.get("media") or {}).get("videos")),
                        "quote_url": (t.get("quote") or {}).get("url")})
        return out

    with ThreadPoolExecutor(5) as ex:
        results = list(ex.map(one, handles))
    tweets = [t for r in results for t in r]
    tweets.sort(key=lambda t: t["views_per_follower"], reverse=True)
    return tweets


# ---------- GitHub ----------

def collect_github(cfg, seen, today):
    g = cfg["github"]
    since = (datetime.now(timezone.utc) - timedelta(days=g["created_within_days"])).date().isoformat()
    repos = {}
    for i, q in enumerate(g["queries"]):
        if i:
            time.sleep(7)  # 未登录的搜索接口每分钟限 10 次
        query = f"{q} created:>{since} stars:>={g['min_stars']}"
        d = fetch("https://api.github.com/search/repositories?sort=stars&order=desc&per_page=10&q="
                  + urllib.parse.quote(query)) or {}
        for r in d.get("items") or []:
            name = r["full_name"]
            if name in repos:
                repos[name]["matched_queries"].append(q)
                continue
            repos[name] = {"repo": name, "url": r["html_url"], "description": clean(r.get("description"), 300),
                           "stars": r["stargazers_count"], "forks": r["forks_count"], "language": r.get("language"),
                           "topics": r.get("topics", []), "created": r["created_at"][:10], "pushed": r["pushed_at"][:10],
                           "matched_queries": [q], "seen_before": seen.get("repos", {}).get(name, today) < today}
    return sorted(repos.values(), key=lambda r: (r["seen_before"], -r["stars"]))


# ---------- 输出 ----------

def fmt_usd(v):
    if v is None:
        return "—"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"${v / div:.2f}{unit}"
    return f"${v:,.2f}"


def fmt_pct(v):
    return "—" if v is None else f"{v:+.2f}%"


def write_markdown(path, data):
    m = data["market"]
    lines = [f"# 原始数据 {data['date']}", "",
             f"采集时间：{data['collected_at']}，回看 {data['lookback_hours']} 小时。此文件是给 Claude Code 读的原料，不是成稿。", "",
             "## 行情", "", "| 币 | 价格 | 24h | 7d | 24h 成交额 | 资金费率 | 持仓量 |", "|---|---|---|---|---|---|---|"]
    for c in m["coins"]:
        fr = "—" if c["funding_rate_pct"] is None else f"{c['funding_rate_pct']:+.4f}%"
        lines.append(f"| {c['symbol']} | {fmt_usd(c['price_usd'])} | {fmt_pct(c['change_24h_pct'])} | "
                     f"{fmt_pct(c['change_7d_pct'])} | {fmt_usd(c['volume_24h_usd'])} | {fr} | {fmt_usd(c['open_interest_usd'])} |")
    fg = m["fear_greed"]
    if fg.get("today"):
        y = fg.get("yesterday") or {}
        lines += ["", f"恐慌贪婪指数：{fg['today']['value']}（{fg['today']['value_classification']}），"
                      f"昨天 {y.get('value', '—')}。来源 {fg['source']}"]
    lines += ["", "来源：CoinGecko 行情，OKX 永续资金费率与持仓量。", ""]

    for topic, title in (("crypto", "币圈新闻"), ("ai", "AI 新闻")):
        items = [n for n in data["news"] if n["topic"] == topic]
        lines += [f"## {title}（{len(items)} 条）", ""]
        for n in items:
            new = "" if not n.get("seen_before") else "（昨天已出现）"
            lines.append(f"- [{n['source']}] {n['title']}{new} — {n['published'] or '时间未知'}\n  {n['url']}\n  {n['summary']}")
        lines.append("")

    lines += [f"## X 博主推文（{len(data['tweets'])} 条，按 浏览/粉丝 排序）", ""]
    for t in data["tweets"]:
        lines.append(f"- @{t['handle']}（{t['group']}，{t['followers']} 粉）{t['created']} 浏览 {t['views']} 赞 {t['likes']} "
                     f"藏 {t['bookmarks']} 评 {t['replies']} 浏览/粉 {t['views_per_follower']}{' 🎬' if t['has_video'] else ''}\n"
                     f"  {t['url']}\n  {t['text']}")
    lines += ["", f"## GitHub 新项目（{len(data['github'])} 个）", ""]
    for r in data["github"]:
        lines.append(f"- {r['repo']} ⭐{r['stars']} {r['language'] or ''} 建于 {r['created']} 最近更新 {r['pushed']}"
                     f"{'（以前出现过）' if r['seen_before'] else ''}\n  {r['url']}\n  {r['description']}")
    if data["errors"]:
        lines += ["", "## 采集失败", ""] + [f"- {e}" for e in data["errors"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.toml")))
    ap.add_argument("--date", default=datetime.now(CST).date().isoformat())
    args = ap.parse_args()
    cfg = tomllib.loads(Path(args.config).read_text(encoding="utf-8"))
    out_dir = Path(cfg["output_dir"]).expanduser()
    (out_dir / "data").mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "data" / "seen.json"
    seen = json.loads(state_path.read_text()) if state_path.exists() else {"links": {}, "repos": {}}

    since = datetime.now(timezone.utc) - timedelta(hours=cfg["lookback_hours"])
    t0 = time.time()
    with ThreadPoolExecutor(3) as ex:
        f_market = ex.submit(collect_market, cfg)
        f_news = ex.submit(collect_news, cfg, since)
        f_kols = ex.submit(collect_kols, cfg, since)
        github = collect_github(cfg, seen, args.date)
        market, news, tweets = f_market.result(), f_news.result(), f_kols.result()

    for n in news:
        n["seen_before"] = n["url"] in seen["links"] and seen["links"][n["url"]] < args.date
    data = {"date": args.date, "collected_at": datetime.now(CST).isoformat(timespec="minutes"),
            "lookback_hours": cfg["lookback_hours"], "market": market, "news": news, "tweets": tweets,
            "github": github, "errors": ERRORS}
    (out_dir / "data" / f"{args.date}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    write_markdown(out_dir / "data" / f"{args.date}_原始数据.md", data)

    # 记住已出现过的链接和项目，第二天标注「已出现」，避免重复选题；只留 14 天
    cutoff = (datetime.now(CST).date() - timedelta(days=14)).isoformat()
    for n in news:
        seen["links"].setdefault(n["url"], args.date)
    for r in github:
        seen["repos"].setdefault(r["repo"], args.date)
    seen = {k: {x: d for x, d in v.items() if d >= cutoff} for k, v in seen.items()}
    state_path.write_text(json.dumps(seen, ensure_ascii=False), encoding="utf-8")

    print(f"完成，用时 {time.time() - t0:.0f} 秒：行情 {len(market['coins'])} 个币，新闻 {len(news)} 条，"
          f"推文 {len(tweets)} 条，GitHub {len(github)} 个，失败 {len(ERRORS)} 处")
    print(f"→ {out_dir / 'data' / (args.date + '_原始数据.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
