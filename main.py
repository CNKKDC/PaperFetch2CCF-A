# main.py
import configparser
import json
import random
import re
import time
from datetime import datetime
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests

from extract import *
from build_html import *


def read_config(path: str) -> Dict:
    cfg = configparser.ConfigParser()
    cfg.read(path, encoding="utf-8")

    s = cfg["spider"]
    years = [y.strip() for y in s.get("years", "").split(",") if y.strip()]
    sleep_min = float(s.get("sleep_min", "0.8"))
    sleep_max = float(s.get("sleep_max", "2.2"))
    timeout_sec = float(s.get("timeout_sec", "20"))
    max_retries = int(s.get("max_retries", "4"))
    retry_backoff_base = float(s.get("retry_backoff_base", "0.8"))
    fetch_assets = s.getboolean("fetch_assets", fallback=True)
    max_assets_per_page = int(s.get("max_assets_per_page", "60"))
    output_json = s.get("output_json", "output.json")
    journal = s.get("journal", "").strip()

    return {
        "years": years,
        "sleep_min": sleep_min,
        "sleep_max": sleep_max,
        "timeout_sec": timeout_sec,
        "max_retries": max_retries,
        "retry_backoff_base": retry_backoff_base,
        "fetch_assets": fetch_assets,
        "max_assets_per_page": max_assets_per_page,
        "output_json": output_json,
        "journal": journal,
    }


def read_links(path: str) -> List[str]:
    links = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            links.append(line)
    return links


def make_session() -> requests.Session:
    sess = requests.Session()

    # 尽量贴近浏览器导航请求（requests 不适合硬塞 :authority/:path 等伪头）
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Sec-CH-UA": "\"Not:A-Brand\";v=\"99\", \"Google Chrome\";v=\"145\", \"Chromium\";v=\"145\"",
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": "\"Windows\"",
        # "Cookie": "dblp-search-mode=c; dblp-dismiss-new-feature-2022-01-27=true",
        "Referer": "https://dblp.uni-trier.de/",
    }
    sess.headers.update(headers)
    return sess


def fetch_with_retry(sess: requests.Session, url: str, cfg: Dict, *, stream: bool = False) -> requests.Response:
    last_exc = None
    for i in range(cfg["max_retries"]):
        try:
            resp = sess.get(url, timeout=cfg["timeout_sec"], allow_redirects=True, stream=stream)
            if resp.status_code in (429, 503, 520, 521, 522, 523, 524):
                # Slightly retreat
                backoff = cfg["retry_backoff_base"] * (2 ** i) + random.uniform(0, 0.4)
                time.sleep(backoff)
                continue
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            backoff = cfg["retry_backoff_base"] * (2 ** i) + random.uniform(0, 0.4)
            time.sleep(backoff)
    raise RuntimeError(f"GET failed after retries: {url} ({last_exc})")


# https://dblp.uni-trier.de/db/journals/jsac/index.html -> JSAC
def guess_journal_from_url(url: str) -> str:
    m = re.search(r"/db/journals/([^/]+)/", url)
    return (m.group(1).upper() if m else "").strip()


def warmup_assets(sess: requests.Session, assets: List[str], cfg: Dict):
    # Lightweight pull: only GET, not save; Avoid being too heavy and causing it to be more like an attack
    for u in assets:
        try:
            jitter_sleep(cfg)
            resp = fetch_with_retry(sess, u, cfg, stream=True)
            # A little bit of reading completes the handshake/cache logic
            for _ in resp.iter_content(chunk_size=8192):
                break
            resp.close()
        except Exception:
            # Resource failures do not affect the main process
            pass


def jitter_sleep(cfg: Dict):
    time.sleep(random.uniform(cfg["sleep_min"], cfg["sleep_max"]))


def crawl_one_index(sess: requests.Session, index_url: str, cfg: Dict) -> List[PaperItem]:
    jitter_sleep(cfg)
    index_resp = fetch_with_retry(sess, index_url, cfg)
    index_html = index_resp.text

    years = set(cfg["years"])
    journal = cfg["journal"] or guess_journal_from_url(index_url)

    volume_links = extract_volume_links(index_html, index_url, years)

    if len(volume_links) == 0:
        for i in range(cfg["max_retries"]):
            backoff = cfg["retry_backoff_base"] * (2 ** i) + random.uniform(0, 0.4)
            time.sleep(backoff)
            volume_links = extract_volume_links(index_html, index_url, years)
            if len(volume_links) != 0:
                break

    results: List[PaperItem] = []
    for volume_text, year, vol_url in volume_links:
        jitter_sleep(cfg)
        vol_resp = fetch_with_retry(sess, vol_url, cfg)
        vol_html = vol_resp.text

        soup = BeautifulSoup(vol_html, "lxml")
        if cfg["fetch_assets"]:
            assets = collect_assets(soup, vol_url, cfg["max_assets_per_page"])
            warmup_assets(sess, assets, cfg)

        print(f"--> Process {volume_text}")

        papers = extract_papers(vol_html, vol_url, volume_text, year, journal)
        results.extend(papers)

    return results


def main():
    cfg = read_config("Config.ini")
    index_links = read_links("IEEELink.txt")

    sess = make_session()

    print(f"Process {len(index_links)} links")

    all_items: List[PaperItem] = []
    for idx_url in index_links:
        if "/conf/" in idx_url:
            print(f"=> Skip conference {idx_url}.")
            continue
        all_items.extend(crawl_one_index(sess, idx_url, cfg))

    # Deduplication: Title+Link+Volume
    seen = set()
    dedup = []
    for it in all_items:
        key = (it.Title, it.Link, it.Volume)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(it)

    write_html(dedup, "index.html", "styles.css")
    with open(cfg["output_json"], "w", encoding="utf-8") as f:
        json.dump([asdict(x) for x in dedup], f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()