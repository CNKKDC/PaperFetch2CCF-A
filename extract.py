# main.py
import re
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple
from datetime import datetime
from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

import requests

@dataclass
class PaperItem:
    Title: str
    Link: str
    Volume: str
    Page: str
    Date: str
    Journal: str


def normalize_whitespace(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def extract_volume_links(index_html: str, base_url: str, years: Set[str]) -> List[Tuple[str, str, str]]:
    """
    return: [(volume_text, year, volume_url)]
    volume_text example: "Volume 40, 2022"
    """
    soup = BeautifulSoup(index_html, "lxml")
    out: List[Tuple[str, str, str]] = []

    # dblp index: <li><a href="...">Volume 40, 2022</a></li>
    all_volume = soup.select("li > a[href]")
    for a in all_volume:
        text = normalize_whitespace(a.get_text(" ", strip=True))
        if not text.lower().startswith("volume "):
            continue

        # Match "Volume 41, 2023" "Volume 34, 2026"
        m = re.match(r"^Volume\s+\d+\s*,?:?\s*(\d{4})\s*$", text, flags=re.I)
        if not m:
            continue
        year = m.group(1)
        if years and year not in years:
            continue

        href = a.get("href")
        if not href:
            continue
        vol_url = urljoin(base_url, href)
        out.append((text, year, vol_url))

    # <li>2025: Volumes <a>302</a>, <a>303</a> ...</li>
    for li in soup.select("li"):
        li_text = normalize_whitespace(li.get_text(" ", strip=True))
        m_year = re.search(r"\b(\d{4})\b\s*:\s*Volumes?\b", li_text, flags=re.I)
        if not m_year:
            continue
        year = m_year.group(1)
        if years and year not in years:
            continue

        for a in li.select("a[href]"):
            vol_no = normalize_whitespace(a.get_text(" ", strip=True))
            if not re.fullmatch(r"\d+", vol_no):
                continue
            href = a.get("href")
            if not href:
                continue
            vol_url = urljoin(base_url, href)
            out.append((f"Volume {vol_no}, {year}", year, vol_url))

    if len(out) == 0 and "/journals/" in base_url:
        print(f"!! There is {len(all_volume)} volume here {base_url}, html length {len(index_html)}")
        # exit(0)

    print(f"=> There is {len(all_volume)} volume here {base_url}, html length {len(index_html)}")

    # Deduplication maintains order
    seen = set()
    uniq = []
    for t, y, u in out:
        key = (t, u)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((t, y, u))

    return uniq


def collect_assets(soup: BeautifulSoup, page_url: str, limit: int) -> List[str]:
    assets = []

    # css
    for tag in soup.select('link[rel~="stylesheet"][href]'):
        assets.append(urljoin(page_url, tag["href"]))

    # js
    for tag in soup.select("script[src]"):
        assets.append(urljoin(page_url, tag["src"]))

    # img
    for tag in soup.select("img[src]"):
        assets.append(urljoin(page_url, tag["src"]))

    # Deduplication + Limit
    seen = set()
    dedup = []
    for u in assets:
        if u in seen:
            continue
        seen.add(u)
        dedup.append(u)
        if len(dedup) >= limit:
            break
    return dedup


def extract_volume_date(soup):
    """
    从 <header class="h2"><h2>Volume 39, Number 1, January 2021</h2></header>
    解析出完整日期字符串，格式：YYYY-MM-DD
    dblp 没有具体日，默认补 01
    """
    header = soup.select_one("header.h2 > h2")
    if not header:
        return ""

    text = header.get_text(strip=True)
    # Match "... , January 2021"
    m = re.search(r',\s*([A-Za-z]+)\s+(\d{4})', text)
    if not m:
        return ""

    month_str = m.group(1)
    year_str = m.group(2)

    try:
        dt = datetime.strptime(f"{month_str} {year_str}", "%B %Y")
        return dt.strftime("%Y-%m-01")  # default 01
    except Exception:
        return f"{year_str}"


def extract_papers(volume_html: str, volume_url: str, volume_text: str, year: str, journal: str) -> List[PaperItem]:
    soup = BeautifulSoup(volume_html, "lxml")
    items: List[PaperItem] = []

    volume_date = extract_volume_date(soup)

    # 每篇一般有 cite.data.tts-content；里面含 title 与 pagination
    cites = soup.select("cite.data.tts-content")
    for i, cite in enumerate(cites):
        title_tag = cite.select_one("span.title")
        if not title_tag:
            continue
        title = normalize_whitespace(title_tag.get_text(" ", strip=True))

        # pagination
        page = ""
        pag_tag = cite.select_one('[itemprop="pagination"]')
        if pag_tag:
            page = normalize_whitespace(pag_tag.get_text(" ", strip=True))

        # datePublished meta（dblp 常见 content="2021"）
        date = volume_date if volume_date else year
        meta = cite.select_one('meta[itemprop="datePublished"]')
        if meta and meta.get("content"):
            date = normalize_whitespace(meta["content"])

        # Link: Firstly doi.org；else “ee” forgen link
        link = ""
        # nav after cite，muitl a
        nav = cite.find_previous_sibling("nav")
        if nav is None:
            nav = cite.find_previous("nav")

        if nav:
            a_doi = nav.select_one('a[href^="https://doi.org/"], a[href^="http://doi.org/"]')
            if a_doi and a_doi.get("href"):
                link = a_doi["href"].strip()
            else:
                # 兜底：第一个外链
                a_any = nav.select_one("a[href]")
                if a_any and a_any.get("href"):
                    link = a_any["href"].strip()

        print(f"\r---> Collect {i+1}/{len(cites)}...", end="")

        # 再兜底：在 cite 内找 itemprop=url（作者页）不合适；就留空
        items.append(
            PaperItem(
                Title=title,
                Link=link,
                Volume=volume_text,
                Page=page,
                Date=date,
                Journal=journal,
            )
        )
    print()
    
    return items
