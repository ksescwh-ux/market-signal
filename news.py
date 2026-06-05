"""
news.py — 뉴스 헤드라인 수집 (RSS) + 위험 키워드 강조
================================================================
주요 금융 뉴스 사이트의 RSS(공개 피드)에서 최신 헤드라인을 모아옵니다.

[원칙]
  - 뉴스는 '판정 입력'이 아니라 '옆에 띄우는 맥락'입니다. (헤드라인 감성은 노이즈가 커서
    위험 등급을 자동으로 바꾸면 오판이 잦음 — 개발지시문의 정성지표 비자동화 방침)
  - 위험 키워드(Fed·침체·전쟁 등)가 잡힌 헤드라인만 ⚠️로 강조합니다.
  - 일부 피드가 막혀도 프로그램은 안 죽고, 되는 피드만 모읍니다(소스별 독립 try/except).
  - 저작권 고려: 제목 + 출처 + 원문 링크만 표시(본문 재배포 안 함).
"""

import re
import time
from email.utils import parsedate_to_datetime
from datetime import datetime

import requests
import xml.etree.ElementTree as ET

import config
import util

log = util.get_logger()

_UA = {"User-Agent": "Mozilla/5.0 (compatible; market-signal/1.0)"}
_ATOM = "{http://www.w3.org/2005/Atom}"


def _parse_feed(url: str) -> list:
    """한 RSS/Atom 피드를 파싱해 (제목, 링크, 발행시각문자열) 리스트로."""
    resp = requests.get(url, headers=_UA, timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    out = []
    # RSS 2.0 (item)
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        if title:
            out.append((title, link, pub))
    # Atom (entry) — RSS 가 없을 때만
    if not out:
        for it in root.findall(f".//{_ATOM}entry"):
            title = (it.findtext(f"{_ATOM}title") or "").strip()
            le = it.find(f"{_ATOM}link")
            link = le.get("href") if le is not None else ""
            pub = it.findtext(f"{_ATOM}updated") or it.findtext(f"{_ATOM}published")
            if title:
                out.append((title, link, pub))
    return out


def _to_kst(pub: str):
    """발행시각 문자열 → (정렬용 epoch, 'MM/DD HH:MM' KST). 실패하면 (0, '')."""
    if not pub:
        return 0.0, ""
    dt = None
    try:
        dt = parsedate_to_datetime(pub)  # RSS 형식
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))  # Atom ISO
        except ValueError:
            return 0.0, ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=util.UTC)
    kst = dt.astimezone(util.KST)
    return kst.timestamp(), kst.strftime("%m/%d %H:%M")


def _risk_match(title: str) -> list:
    """
    제목에서 위험 키워드를 찾아 리스트로.
    단어 경계(\\b)로 매칭해 'war'가 'Warren' 안에서 잘못 잡히는 일을 막습니다.
    """
    low = title.lower()
    found = []
    for kw in config.RISK_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", low):
            found.append(kw)
    return found


def fetch_news() -> list:
    """
    모든 피드에서 헤드라인을 모아 최신순으로 정렬, 중복 제거 후 상위 N개 반환.
    각 항목: {title, link, source, time, risk(bool), matched[list]}
    """
    collected = []
    for feed in config.NEWS_FEEDS:
        items = None
        for attempt in range(2):  # 간단 재시도
            try:
                items = _parse_feed(feed["url"])
                break
            except Exception as e:
                if attempt == 0:
                    time.sleep(1)
                else:
                    log.warning(f"[뉴스:{feed['name']}] 수집 실패(건너뜀): {e}")
        if not items:
            continue
        for title, link, pub in items:
            epoch, when = _to_kst(pub)
            collected.append({
                "title": title, "link": link, "source": feed["name"],
                "time": when, "_epoch": epoch,
            })

    # 중복 제거(제목 기준) + 최신순 정렬
    seen, unique = set(), []
    for n in sorted(collected, key=lambda x: x["_epoch"], reverse=True):
        key = n["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        matched = _risk_match(n["title"])
        n["risk"] = bool(matched)
        n["matched"] = matched
        n.pop("_epoch", None)
        unique.append(n)

    result = unique[: config.NEWS_MAX]
    risk_n = sum(1 for n in result if n["risk"])
    log.info(f"뉴스 {len(result)}건 수집 (위험 키워드 {risk_n}건)")
    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    for n in fetch_news():
        mark = "⚠️ " if n["risk"] else "   "
        kw = f"  [{', '.join(n['matched'])}]" if n["matched"] else ""
        log.info(f"{mark}[{n['source']} {n['time']}] {n['title'][:70]}{kw}")
