#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""캠핑장 빈자리 알림 (숲나들e -> 디스코드)

숲나들e 로그인 후 '월별 예약 조회' 화면이 쓰는 조회용 주소를 읽기만 합니다.
예약, 결제, 자동 클릭은 하지 않습니다. 빈자리가 새로 생기면 디스코드로 알려줍니다.

조회 방식은 공개 저장소 nomadamas/k-skill (MIT 라이선스)의 foresttrip-vacancy 를 참고했습니다.

사용법:
  python camp_alert.py setup      처음 한 번: 질문에 답하면 설정이 끝납니다
  python camp_alert.py run        계속 감시하기
  python camp_alert.py once       한 번만 확인하기
  python camp_alert.py discord    디스코드 테스트 메시지 보내기
  python camp_alert.py autostart  컴퓨터 켜면 자동으로 시작하게 등록
"""
from __future__ import annotations

import argparse
import concurrent.futures
import html as htmllib
import json
import os
import random
import re
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
STATE_PATH = BASE / "state.json"
LOG_PATH = BASE / "log.txt"

LOGIN_URL = "https://www.foresttrip.go.kr/com/login.do"
RSRVT_PAGE = "https://www.foresttrip.go.kr/rep/or/sssn/monthRsrvtSmplStatus.do"
POST_URL = "https://www.foresttrip.go.kr/rep/or/selectRsrvtAvailInfoListForMonthRsrvtSmpl.do"
SITE_URL = "https://www.foresttrip.go.kr"
# 숲나들e 휴양림 '일반예약'(예약 가능 상품 보기) 화면. 실제 사이트의 휴양림 소개 화면 메뉴에서 확인한 주소예요.
BOOK_TPL = SITE_URL + "/rep/or/sssn/fcfsRsrvtPssblGoodsDetls.do?hmpgId={fid}&menuId=001001"
# 예약한 뒤 결제 기다리는 내역 화면 (결제 안 하면 자동 취소될 수 있어서 알림에 같이 안내해요)
PAY_TPL = SITE_URL + "/rep/mypage/selectSttlmWtng.do?hmpgId={fid}&menuId=006001001"

DAY_NAMES = ["월", "화", "수", "목", "금", "토", "일"]

# 쉬는 날(공휴일·대체공휴일). 틀린 게 있으면 config.json 의 extra_holidays 에 YYYYMMDD 로 더하세요.
HOLIDAYS = {
    "20261005", "20261009", "20261225",
    "20270101", "20270206", "20270207", "20270208", "20270209", "20270301", "20270505",
    "20270513", "20270816", "20270914", "20270915", "20270916", "20271004", "20271011",
    "20271227",
}
RESERVE_MARK = "예비"  # 운영자가 따로 잡아둔 방은 건너뜀
WEBHOOK_RE = re.compile(r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$")

DEFAULT_CONFIG: dict[str, Any] = {
    "webhook": "",
    "forest_id": "",
    "forest_pw": "",
    "weekdays": [4, 5, 6],         # 금(4), 토(5), 일(6) 밤. 일요일이 1순위, 토요일 2순위, 금요일 3순위
    "holiday_eve": True,           # 다음날이 쉬는 날(공휴일)인 밤도 함께 찾기
    "extra_holidays": [],          # 직접 더할 쉬는 날 (예: ["20261120"])
    "include_today": True,         # 오늘 밤은 요일과 상관없이 항상 찾기 (당일치기용)
    "weeks": 6,                    # 앞으로 몇 주를 볼지
    "categories": ["02"],          # 02 = 야영(캠핑), 01 = 숙박(휴양림 방)
    "regions": ["부산", "울산", "경남", "경상남", "경북", "경상북", "대구"],
    "interval_minutes": 15,        # 몇 분마다 확인할지
    "ping_url": "",                # (선택) 바깥에서 지켜주는 무료 서비스 주소. 있으면 확인할 때마다 인사해요
    "tier_version": 1,
    "picky": True,                 # True = 폰 알림은 '놓치면 아까운 자리'만. (모든 새 자리는 log.txt 에 기록)
    "special_within_days": 10,     # 오늘부터 이 날짜 안쪽 자리만 '취소표'로 보고 알려줘요 (멀리 있는 건 그냥 새로 열린 것)
    "rare_ratio": 0.3,             # 이 휴양림이 평소 빈자리 나는 비율이 이 값 이하면 '귀한 곳'
    "min_history": 24,             # 평소 모습을 배우는 데 필요한 확인 횟수 (15분마다면 약 6시간)
    "deal_ratio": 0.6,             # 평소 요금(중간값)의 이 비율 이하면 '특가'로 봐요 (요금 정보가 있을 때만)
    "rain_mode": True,             # 비 소식이 있는 날은 '비모드': 캠핑 대신 당일 나들이 장소를 추천
    "rain_lat": 35.1331,           # 날씨를 볼 곳 (진해)
    "rain_lon": 128.6906,
    "rain_prob": 60,               # 비 올 확률(%)이 이 이상이면 비 오는 날로 봐요
    "rain_mm": 5,                  # 또는 예상 비의 양(mm)이 이 이상이면
    "rain_days": [5, 6],           # 나들이 하는 요일 (토·일). 쉬는 날도 함께
    "rain_eve_hour": 18,           # 전날 이 시각 이후 '내일 비 와요' 알림
    "rain_max_minutes": 60,        # 진해에서 이 시간(분) 안쪽 장소만 추천
    "kid_label": "",                 # 알림에서 '아이와 가기 좋은 점'을 말할 때 부르는 이름 (비우면 '아이')
    "great_within_days": 45,       # '대박'(S등급·특가·💎)은 이 날짜 안쪽이면 멀어도 미리 알려줘요
    "holiday_within_days": 30,     # 쉬는 날 앞날 밤은 이 날짜 안쪽이면 취소표로 봐요
    "strong_mention": "@everyone", # 세게 알릴 때 글 맨 앞에 붙이는 말 (비우면 안 붙임)
    "vip_forests": [],             # 늘 귀하게 칠 휴양림 이름 일부 (예: ["용지봉", "대운산"])
    "digest": True,                # 일요일 밤 '갈 수 있는 곳 전부'를 아침에 알려주기
    "digest_weekday": 6,           # 6 = 일요일 (토요일 아침에는 '내일 밤', 일요일 아침에는 '오늘 밤')
    "heartbeat_hour": 8,           # 매일 이 시각 이후 '잘 돌아가고 있어요' 알림
}


# ---------------------------------------------------------------- 기본 도구
def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1_000_000:
            tail = LOG_PATH.read_text(encoding="utf-8", errors="replace")[-300_000:]
            LOG_PATH.write_text(tail, encoding="utf-8")
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


CLOUD = bool(os.environ.get("CAMP_CLOUD"))  # 클라우드(GitHub)에서 돌 때 True


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    # 클라우드에서는 비밀값(디스코드 주소·아이디·비밀번호)을 금고(Secrets)에서 읽어요
    for env, key in (("CAMP_WEBHOOK", "webhook"), ("FOREST_ID", "forest_id"),
                     ("FOREST_PW", "forest_pw"), ("KID_LABEL", "kid_label")):
        if os.environ.get(env):
            cfg[key] = os.environ[env].strip()
    if CONFIG_PATH.exists():
        if cfg.get("tier_version") != 1:  # 등급 버전으로 올라올 때: 금요일도 같이 보게 한 번만 바꿔요
            if cfg.get("weekdays") == [5, 6]:
                cfg["weekdays"] = [4, 5, 6]
            cfg["tier_version"] = 1
            try:
                save_config(cfg)
            except Exception:
                pass
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"open": {}, "last_heartbeat": ""}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 디스코드
def send_discord(webhook: str, text: str, timeout: int = 20) -> tuple[bool, str]:
    """디스코드로 글 보내기. (성공 여부, 이유)"""
    body = json.dumps({"content": text[:1900], "username": "캠핑 빈자리 알림", "flags": 4}).encode("utf-8")  # flags 4 = 링크 미리보기 큰 상자 끄기
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "CampAlert/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return (200 <= res.status < 300), f"http_{res.status}"
    except urllib.error.HTTPError as e:
        return False, f"http_{e.code}"
    except Exception as e:  # 인터넷 끊김 등
        return False, str(e)


def notify(cfg: dict[str, Any], text: str) -> bool:
    ok, why = send_discord(cfg["webhook"], text)
    if not ok:
        log(f"디스코드 전송 실패: {why}")
    return ok


def notify_long(cfg: dict[str, Any], text: str) -> None:
    """긴 글을 디스코드 한 칸(1900자)에 맞게 나눠 보내요."""
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > 1800:
            notify(cfg, chunk)
            time.sleep(1)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        notify(cfg, chunk)


# ---------------------------------------------------------------- 날짜 계산
def holiday_set(cfg: dict[str, Any]) -> set[str]:
    return HOLIDAYS | set(cfg.get("extra_holidays", []))


def target_dates(cfg: dict[str, Any], today=None) -> list[str]:
    """찾을 '잠자는 날' 목록. 고른 요일 + (옵션) 다음날이 쉬는 날인 밤."""
    today = today or datetime.now().date()
    holidays = holiday_set(cfg)
    out = []
    for i in range(int(cfg["weeks"]) * 7 + 1):
        d = today + timedelta(days=i)
        tomorrow = (d + timedelta(days=1)).strftime("%Y%m%d")
        if (
            d.weekday() in cfg["weekdays"]
            or (cfg.get("holiday_eve") and tomorrow in holidays)
            or (i == 0 and cfg.get("include_today"))
        ):
            out.append(d.strftime("%Y%m%d"))
    return out


def build_windows(dates: list[str]) -> list[tuple[str, str]]:
    """사이트가 한 번에 최대 5일치만 주므로, 날짜를 5일 단위 묶음으로 나눕니다."""
    ds = sorted(datetime.strptime(x, "%Y%m%d").date() for x in dates)
    wins: list[tuple[str, str]] = []
    i = 0
    while i < len(ds):
        start = ds[i]
        end = start + timedelta(days=4)
        j = i
        while j < len(ds) and ds[j] <= end:
            j += 1
        wins.append((start.strftime("%Y%m%d"), ds[j - 1].strftime("%Y%m%d")))
        i = j
    return wins


def pretty_date(yyyymmdd: str, holidays: set[str] | None = None) -> str:
    d = datetime.strptime(yyyymmdd, "%Y%m%d")
    text = f"{d.month}/{d.day}({DAY_NAMES[d.weekday()]})"
    if holidays and (d + timedelta(days=1)).strftime("%Y%m%d") in holidays:
        text += " ※다음날 쉬는 날"
    return text


# ---------------------------------------------------------------- 숲나들e 접속
class LoginError(Exception):
    pass


@dataclass
class Session:
    cookies: dict[str, str]
    csrf: str
    user_agent: str
    forests: dict[str, str]                      # 휴양림 번호 -> 이름
    regions: dict[str, str] = field(default_factory=dict)  # 휴양림 번호 -> 지역(시도)
    created: float = field(default_factory=time.time)
    price_obs: dict = field(default_factory=dict)   # 휴양림 번호 -> [(주말인지, 요금), ...]
    probe: dict = field(default_factory=dict)       # 바로가기 링크 규칙 점검 결과


def bootstrap_session(user_id: str, user_pw: str, probe: bool = False) -> Session:
    from playwright.sync_api import sync_playwright  # 설치 파일이 미리 깔아줍니다

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(LOGIN_URL, timeout=60000)
            page.fill("#mmberId", user_id)
            page.fill("#gnrlMmberPssrd", user_pw)
            page.click("input.loginBtn")
            page.wait_for_load_state("networkidle")
            page.goto(RSRVT_PAGE, timeout=60000)
            page.wait_for_load_state("networkidle")

            if page.locator("#mmberId").count() > 0:
                raise LoginError(
                    "로그인이 안 됐어요. 아이디/비밀번호가 맞는지, 숲나들e 사이트에서 직접 로그인이 되는지 확인해 주세요."
                )
            if page.locator("#srchSido").count() == 0 or page.locator("#srchInstt").count() == 0:
                raise LoginError("로그인은 됐는데 예약 조회 화면을 못 열었어요. 숲나들e 화면이 바뀌었을 수 있어요.")

            csrf_loc = page.locator('input[name="_csrf"]')
            if csrf_loc.count() == 0:
                raise LoginError("숲나들e에서 보안 확인값을 못 받았어요. 잠시 뒤 다시 시도할게요.")
            csrf = csrf_loc.first.get_attribute("value") or ""

            forests: dict[str, str] = {}
            regions: dict[str, str] = {}
            sidos = page.evaluate(
                "() => Array.from(document.querySelector('#srchSido').options).slice(1)"
                ".map(o => ({value: o.value, text: o.textContent.trim()}))"
            )
            for sido in sidos:
                if not sido.get("value"):
                    continue
                page.select_option("#srchSido", value=sido["value"])
                page.wait_for_timeout(500)
                opts = page.evaluate(
                    "() => Array.from(document.querySelector('#srchInstt').options).slice(1)"
                    ".map(o => ({value: o.value, text: o.textContent.trim()}))"
                )
                for o in opts:
                    fid = str(o.get("value") or "").strip()
                    name = str(o.get("text") or "").strip()
                    if fid and name:
                        forests[fid] = name
                        regions[fid] = sido["text"]

            cookies = {c["name"]: c["value"] for c in page.context.cookies()}
            ua = page.evaluate("() => navigator.userAgent")
            probe_result: dict[str, Any] = {}
            if probe and forests:
                try:
                    probe_result = probe_deeplink(page, next(iter(forests)))
                except Exception as e:  # 점검이 실패해도 감시는 계속
                    probe_result = {"error": str(e)[:100]}
        finally:
            browser.close()

    if not forests:
        raise LoginError("휴양림 목록을 못 가져왔어요. 숲나들e 화면이 바뀌었을 수 있어요.")
    return Session(cookies=cookies, csrf=csrf, user_agent=ua, forests=forests, regions=regions, probe=probe_result)


def _login_page(url: str) -> bool:
    return "login" in url.lower()


DATE_KEYS = ("srchDate", "useDt", "rsrvtBgnDt", "bgnDt", "stayBgnDt", "checkInDt", "startDt", "srchBgnDt", "rsrvtDt")


def probe_deeplink(page, fid: str) -> dict[str, Any]:
    """숲나들e 화면에서 '일반예약' 링크를 사람처럼 찾아 진짜 주소를 알아내요. 주소에 날짜를 넣으면 미리 골라지는지도 시험해요."""
    result: dict[str, Any] = {}
    date = (datetime.now() + timedelta(days=3)).strftime("%Y%m%d")
    # 메뉴 링크가 실제로 보이는 화면(예약신청내역)에서 시작해요. 이 화면은 주소로 바로 열려요.
    page.goto(PAY_TPL.format(fid=fid), timeout=60000)
    page.wait_for_load_state("networkidle")
    result["start"] = page.url[:120]
    links = page.evaluate(
        "() => Array.from(document.querySelectorAll('a')).map(e => ({"
        "t: (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 20), h: e.href || '', r: e.getAttribute('href') || ''}))"
        ".filter(x => /예약/.test(x.t))")
    result["links"] = [f"{l['t']}|{l['r']}"[:110] for l in links[:8]]
    cand = [l for l in links if l["t"].replace(" ", "") == "일반예약" and l["h"].startswith("http")]
    if not cand:
        cand = [l for l in links if "일반예약" in l["t"] and l["h"].startswith("http")]
    result["book_ok"] = False
    for l in cand:
        href = l["h"]
        try:
            page.goto(PAY_TPL.format(fid=fid), timeout=60000)
            page.wait_for_load_state("networkidle")
            page.locator("a", has_text="일반예약").first.click(timeout=8000)
            page.wait_for_load_state("networkidle")
            result["click_landed"] = page.url[:150]
            if "error.do" not in page.url and not _login_page(page.url) and "hmpgId=" in href:
                test = re.sub(r"hmpgId=[A-Za-z0-9]*", "hmpgId=" + fid, href)
                page.goto(test, timeout=60000)
                page.wait_for_load_state("networkidle")
                result["landed"] = page.url[:150]
                if "error.do" not in page.url:  # 주소로 바로 열릴 때만 링크로 써요(눌러서만 열리면 쓰지 않아요)
                    result["book_ok"] = True
                    result["tpl"] = re.sub(r"hmpgId=[A-Za-z0-9]*", "hmpgId={fid}", href)
                    break
        except Exception:
            pass
        if "hmpgId=" in href:
            test = re.sub(r"hmpgId=[A-Za-z0-9]*", "hmpgId=" + fid, href)
            tpl = re.sub(r"hmpgId=[A-Za-z0-9]*", "hmpgId={fid}", href)
        else:
            test, tpl = href, href
        page.goto(test, timeout=60000)
        page.wait_for_load_state("networkidle")
        result["landed"] = page.url[:150]
        if "error.do" not in page.url and not _login_page(page.url):
            result["book_ok"] = True
            result["tpl"] = tpl
            break
    pay = [l for l in links if "예약신청내역" in l["t"].replace(" ", "") and l["h"].startswith("http")]
    if pay:
        ph = pay[0]["h"]
        if "hmpgId=" in ph:
            page.goto(re.sub(r"hmpgId=[A-Za-z0-9]*", "hmpgId=" + fid, ph), timeout=60000)
            page.wait_for_load_state("networkidle")
            if "error.do" not in page.url and not _login_page(page.url):
                result["pay_tpl"] = re.sub(r"hmpgId=[A-Za-z0-9]*", "hmpgId={fid}", ph)
    if not result["book_ok"]:
        return result
    base = test
    for key in DATE_KEYS:
        sep = "&" if "?" in base else "?"
        page.goto(base + sep + urllib.parse.urlencode({key: date}), timeout=60000)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        hit = page.evaluate(
            "(d) => Array.from(document.querySelectorAll('input,select')).some(e => (e.value || '').replace(/[^0-9]/g, '').includes(d))",
            date)
        if hit:
            result["date_key"] = key
            break
    return result


def book_url(state: dict[str, Any], fid: str, use_dt: str) -> tuple[str, bool]:
    """(예약 화면 주소, 날짜까지 미리 골라지는 주소인지). 점검으로 진짜 주소를 못 찾았으면 예약 현황 화면으로 보내요."""
    dl = state.get("deeplink") or {}
    tpl = dl.get("tpl") or ""
    if not tpl:
        return RSRVT_PAGE, False
    url = tpl.replace("{fid}", fid)
    key = dl.get("date_key") or ""
    if key:
        return url + ("&" if "?" in url else "?") + urllib.parse.urlencode({key: use_dt}), True
    return url, False


def pay_url(fid: str, state: dict[str, Any] | None = None) -> str:
    tpl = ((state or {}).get("deeplink") or {}).get("pay_tpl") or PAY_TPL
    return tpl.replace("{fid}", fid)


def detail_url(fid: str) -> str:
    """휴양림 야영장 소개 화면(로그인 없이 볼 수 있어요). 숲나들e의 휴양림 번호가 그대로 소개 화면 번호예요."""
    return f"{SITE_URL}/pot/rm/fa/selectCmpgrArmpListView.do?hmpgId={fid}&menuId=002002002"


FAC_KEYS = ("원", "요금", "사이트", "데크", "전기", "샤워", "온수", "입실", "퇴실", "주차", "화로", "숯불", "반려",
            "개수대", "화장실", "취사", "차박", "㎡", "동반", "카라반", "파쇄석", "매점")
FAC_SKIP = ("로그인", "개인정보", "저작권", "관리자", "이용약관", "바로가기", "메뉴", "검색", "공지", "Copyright")


def parse_facility_text(html: str) -> list[str]:
    text = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html)
    text = re.sub(r"(?is)<br\s*/?>|</(p|div|li|tr|h\d|td|th|dd|dt)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = htmllib.unescape(text)
    out: list[str] = []
    for raw in text.split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if not (4 <= len(line) <= 110) or any(b in line for b in FAC_SKIP):
            continue
        if any(k in line for k in FAC_KEYS) and line not in out:
            out.append(line)
    return out[:7]


def fetch_facility(session: Session | None, fid: str, state: dict[str, Any], now_ts: float | None = None) -> list[str]:
    """숲나들e의 공식 야영장 소개 화면에서 요금·시설 문장을 뽑아와요(30일 동안 기억해요). 못 읽으면 빈 목록."""
    if not session or not fid:
        return []
    now_ts = now_ts or time.time()
    cache = state.setdefault("facility", {})
    c = cache.get(fid)
    if c and now_ts - c.get("ts", 0) < (30 * 86400 if c.get("lines") else 86400):
        return c.get("lines", [])
    lines: list[str] = []
    try:
        req = urllib.request.Request(detail_url(fid), headers={
            "User-Agent": session.user_agent, "Accept-Language": "ko-KR,ko;q=0.9",
            "Cookie": "; ".join(f"{k}={v}" for k, v in session.cookies.items())})
        with urllib.request.urlopen(req, timeout=15) as res:
            lines = parse_facility_text(res.read().decode("utf-8", errors="replace"))
    except Exception as e:
        log(f"공식 안내를 못 읽었어요({fid}): {e}")
    cache[fid] = {"ts": now_ts, "lines": lines}
    return lines


def fetch_one(session: Session, forest_id: str, category: str, start: str, end: str):
    """휴양림 하나, 5일 이내 구간 하나를 조회. (rows or None, 오류)"""
    payload = {
        "insttId": forest_id,
        "upperGoodsClsscCd": category,
        "srchDate": start,
        "lastDay": end,
        "inqurSctin": "02",
    }
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Content-Type": "application/json; charset=UTF-8",
        "Cookie": "; ".join(f"{k}={v}" for k, v in session.cookies.items()),
        "Origin": SITE_URL,
        "Referer": RSRVT_PAGE,
        "User-Agent": session.user_agent,
        "X-CSRF-Token": session.csrf,
        "X-Requested-With": "XMLHttpRequest",
    }
    last_err = "unknown"
    for attempt in range(2):
        req = urllib.request.Request(POST_URL, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                data = json.loads(res.read().decode("utf-8"))
                if isinstance(data, list):
                    return data, None
                last_err = "unexpected_payload"
        except urllib.error.HTTPError as e:
            last_err = f"http_{e.code}"
        except Exception as e:
            last_err = str(e)
        time.sleep(1 + attempt)
    return None, last_err


PRICE_KEY_RE = re.compile(r"(amt|prc|price|fee|chrg|pay)", re.I)


def row_price(row: dict[str, Any]) -> int | None:
    """응답에 요금 같은 항목이 있으면 숫자로 꺼내요. 없으면 None."""
    best = None
    for k, v in row.items():
        if not PRICE_KEY_RE.search(str(k)):
            continue
        try:
            n = int(float(str(v).replace(",", "")))
        except (TypeError, ValueError):
            continue
        if 1000 <= n <= 2_000_000:
            best = n if best is None else max(best, n)
    return best


def is_available(row: dict[str, Any]) -> bool:
    try:
        return row.get("rsrvtAvail") == "Y" and int(row.get("rsrvtCnt")) == 0
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- 한 번 확인하기
def pick_targets(cfg: dict[str, Any], session: Session) -> dict[str, str]:
    wanted = cfg["regions"]
    targets = {
        fid: name
        for fid, name in session.forests.items()
        if any(w in session.regions.get(fid, "") for w in wanted)
    }
    if not targets:
        log("지역 이름이 맞는 휴양림이 없어서 전국 전체를 확인합니다. 지역 목록: " + ", ".join(sorted(set(session.regions.values()))))
        targets = dict(session.forests)
    return targets


def collect(cfg: dict[str, Any], session: Session, dates: list[str]):
    """주어진 날짜들의 빈자리를 조회. (찾은 자리, 휴양림별 성공여부, 오류목록, 대상 휴양림, 작업수)"""
    date_set = set(dates)
    windows = build_windows(dates)
    targets = pick_targets(cfg, session)
    cats = list(cfg["categories"])

    jobs = [(fid, cat, ws, we) for fid in targets for cat in cats for (ws, we) in windows]
    found: dict[str, dict[str, Any]] = {}
    ok_by_forest = {fid: True for fid in targets}
    errors: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futs = {pool.submit(fetch_one, session, fid, cat, ws, we): (fid, cat) for fid, cat, ws, we in jobs}
        for fut in concurrent.futures.as_completed(futs):
            fid, cat = futs[fut]
            rows, err = fut.result()
            if err is not None or rows is None:
                ok_by_forest[fid] = False
                errors.append(f"{targets[fid]}:{err}")
                continue
            for row in rows:
                pr = row_price(row)
                if pr is not None:
                    try:
                        wk = datetime.strptime(row.get("useDt") or "", "%Y%m%d").weekday() >= 4
                    except ValueError:
                        wk = False
                    session.price_obs.setdefault(fid, []).append((wk, pr))
                if not is_available(row) or RESERVE_MARK in (row.get("goodsNm") or ""):
                    continue
                use_dt = row.get("useDt") or ""
                if use_dt not in date_set:
                    continue
                name = row.get("goodsNm") or ""
                key = f"{fid}|{use_dt}|{cat}|{name}"
                found[key] = {
                    "forest": targets[fid],
                    "region": session.regions.get(fid, ""),
                    "use_dt": use_dt,
                    "name": name,
                    "capacity": row.get("mxmmAccptCnt"),
                    "fid": fid,
                    "price": row_price(row),
                }
    return found, ok_by_forest, errors, targets, len(jobs)


def run_cycle(cfg: dict[str, Any], session: Session, state: dict[str, Any]) -> dict[str, Any]:
    found, ok_by_forest, errors, targets, total_jobs = collect(cfg, session, target_dates(cfg))
    prev: dict[str, Any] = state.get("open", {})
    # 조회에 실패한 휴양림은 이전 기록을 그대로 둬서, 성공 때 같은 알림이 또 오지 않게 합니다
    new_open = dict(found)
    for key, info in prev.items():
        fid = key.split("|", 1)[0]
        if fid in ok_by_forest and not ok_by_forest[fid] and key not in new_open:
            new_open[key] = info
    new_pairs = [(key, info) for key, info in found.items() if key not in prev]
    new_items = [info for _, info in new_pairs]
    state["open"] = new_open
    today = datetime.now()
    window_end = (today + timedelta(days=int(cfg.get("special_within_days", 10)))).strftime("%Y%m%d")
    today_s = today.strftime("%Y%m%d")
    hist = state.setdefault("hist", {})
    near_forests = {k.split("|", 1)[0] for k, i in found.items() if today_s <= i["use_dt"] <= window_end}
    for fid, ok in ok_by_forest.items():
        if ok:
            h = hist.setdefault(fid, [0, 0])
            h[0] += 1
            h[1] += 1 if fid in near_forests else 0
    if cfg.get("rain_mode", True):
        try:
            refresh_forecast(cfg, state)
        except Exception:
            pass
    special = pick_special(cfg, state, new_pairs, today_s, holiday_set(cfg))
    prev_open = set(state.get("open_forests", []))
    last_open = state.setdefault("last_open", {})
    appear = state.setdefault("appear", {})
    for fid in near_forests:
        if fid not in prev_open:
            lo = last_open.get(fid)
            gap = (datetime.strptime(today_s, "%Y%m%d") - datetime.strptime(lo, "%Y%m%d")).days if lo else None
            appear[fid] = {"gap_days": gap, "at": today_s}
        last_open[fid] = today_s
    state["open_forests"] = sorted(near_forests | {f for f in prev_open if f in ok_by_forest and not ok_by_forest[f]})
    saw_price = bool(session.price_obs)
    update_price_base(state, session)
    if state.get("last_rank") != today_s:           # 순위표는 하루 한 번 새로 써요
        write_ranking(cfg, state, list(targets.items()), load_tiers())
        state["last_rank"] = today_s
    if not state.get("price_checked") and ok_by_forest and any(ok_by_forest.values()):
        state["price_checked"] = True
        log("요금 정보: " + ("응답에 요금 항목이 있어요 (특가 알림 사용 가능)" if saw_price
                          else "응답에서 요금 항목을 못 찾았어요 (특가 알림은 쉬어요)"))

    return {
        "scanned": len(targets),
        "ok_forests": sum(ok_by_forest.values()),
        "total_jobs": total_jobs,
        "errors": errors,
        "open_now": len(found),
        "new": new_items,
        "special": special,
        "found": found,
    }


REVIEW_WEIGHTS = {"내경험": 3.0, "영수증": 2.0, "실경험": 1.5, "커뮤니티": 1.2, "기사": 0.3, "광고": 0.1}
REVIEW_HALF_LIFE_DAYS = 365


def load_tiers() -> dict[str, Any]:
    """휴양림등급.json(처음 의견) + 후기모음.json(후기)을 읽어요. 매번 다시 읽으니 파일만 바꿔도 바로 적용돼요."""
    out: dict[str, Any] = {"S": {}, "A": {}, "R": []}
    try:
        data = json.loads((BASE / "휴양림등급.json").read_text(encoding="utf-8"))
        out["S"], out["A"] = dict(data.get("S", {})), dict(data.get("A", {}))
    except Exception:
        pass
    try:
        data = json.loads((BASE / "후기모음.json").read_text(encoding="utf-8"))
        out["R"] = [r for r in data.get("후기", []) if isinstance(r, dict)]
    except Exception:
        pass
    return out


def review_score(forest: str, reviews: list[dict[str, Any]], today: datetime | None = None) -> tuple[float, float, int]:
    """(후기 점수 1~5, 믿음의 크기, 후기 수).  실경험·최신 후기는 힘이 세고, 기사·광고·오래된 후기는 약해요."""
    today = today or datetime.now()
    tot_w = tot = 0.0
    n = 0
    for r in reviews:
        if str(r.get("휴양림", "")) not in forest or not r.get("휴양림"):
            continue
        try:
            stars = float(r["별점"])
            age = max(0, (today - datetime.strptime(str(r.get("날짜", "20200101"))[:8], "%Y%m%d")).days)
        except (KeyError, ValueError, TypeError):
            continue
        w = REVIEW_WEIGHTS.get(str(r.get("종류", "실경험")), 1.0) * (0.5 ** (age / REVIEW_HALF_LIFE_DAYS))
        w = max(w, 0.02)
        tot_w += w
        tot += w * stars
        n += 1
    prior_w = 2.0                       # 후기가 적을 땐 중간(3.5점)쪽으로 끌어당겨 한두 개에 휘둘리지 않게
    score = (tot + prior_w * 3.5) / (tot_w + prior_w) if n else 0.0
    return score, tot_w, n


def tier_of(forest: str, tiers: dict[str, Any], vips: list[str] | None = None,
            today: datetime | None = None) -> tuple[str, str]:
    """휴양림 등급(S/A/B)과 이유.  후기가 충분하면 후기가 정하고, 부족하면 처음 의견을 따라요."""
    for v in vips or []:
        if v and v in forest:
            return "S", "내가 찜한 곳"
    reviews = tiers.get("R") or []
    if reviews:
        score, conf, n = review_score(forest, reviews, today)
        if n and conf >= 1.5:
            note = f"후기 {score:.1f}점 (믿음 {conf:.1f}, {n}건)"
            if score < 3.3:
                return "B", note + " - 후기가 안 좋아요"
            if score >= 4.3 and conf >= 3.0:
                return "S", note
            if score >= 3.8:
                return "A", note
            return "B", note
    for t in ("S", "A"):
        for key, why in tiers.get(t, {}).items():
            if key in forest:
                return t, why
    return "B", ""


def deal_info(cfg: dict[str, Any], state: dict[str, Any], fid: str, info: dict[str, Any]) -> tuple[bool, str]:
    """평소 요금보다 확 싸면 (True, 설명). 요금 정보가 없거나 기록이 모자라면 (False, '')."""
    price = info.get("price")
    base = state.get("price_base", {}).get(fid)
    if not price or not base:
        return False, ""
    try:
        wk = datetime.strptime(info["use_dt"], "%Y%m%d").weekday() >= 4
    except ValueError:
        return False, ""
    median = base.get("wk" if wk else "wd")
    if median and price <= float(cfg.get("deal_ratio", 0.6)) * median:
        return True, f"평소 {int(median):,}원 → 지금 {int(price):,}원"
    return False, ""


def update_price_base(state: dict[str, Any], session: Session) -> None:
    """요금 기록의 중간값(평소 요금)을 휴양림별·평일/주말별로 배워요."""
    import statistics
    base = state.setdefault("price_base", {})
    for fid, obs in session.price_obs.items():
        for wk, key in ((True, "wk"), (False, "wd")):
            vals = [p for w, p in obs if w == wk]
            if len(vals) >= 10:
                old = base.setdefault(fid, {}).get(key)
                med = statistics.median(vals)
                base[fid][key] = med if not old else (old * 0.8 + med * 0.2)  # 천천히 따라가요
    session.price_obs.clear()


def write_ranking(cfg: dict[str, Any], state: dict[str, Any], forests: list[str], tiers: dict[str, Any]) -> None:
    """순위표.txt : 지금 기준 휴양림 순위와 이유 (즐겨찾기처럼 보는 용도)."""
    hist = state.get("hist", {})
    names = {}
    rows = []
    for fid, name in forests:
        t, why = tier_of(name, tiers, cfg.get("vip_forests", []))
        n, o = hist.get(fid, [0, 0])
        rare = f"자리 나는 비율 {int(100 * o / n)}%" if n >= int(cfg.get("min_history", 24)) else "자리 패턴 배우는 중"
        score = review_score(name, tiers.get("R") or [])[0]
        rows.append(({"S": 0, "A": 1, "B": 2}[t], -score, name, t, why, rare))
    rows.sort()
    lines = [f"휴양림 순위표 ({datetime.now():%Y-%m-%d} 기준, 후기·자리 패턴이 쌓이면 저절로 바뀌어요)", ""]
    for _, _, name, t, why, rare in rows:
        lines.append(f"[{t}] {name} - {why or '아직 후기 없음'} / {rare}")
    try:
        (BASE / "순위표.txt").write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


def day_rank(use_dt: str, holidays: set[str]) -> tuple[int, bool]:
    """(1=일요일 밤, 2=토요일 밤, 3=금요일 밤, 4=그 밖의 날, 쉬는 날 앞날인지)"""
    d = datetime.strptime(use_dt, "%Y%m%d")
    eve = (d + timedelta(days=1)).strftime("%Y%m%d") in holidays
    return {6: 1, 5: 2, 4: 3}.get(d.weekday(), 4), eve


def alert_level(cfg: dict[str, Any], state: dict[str, Any], fid: str, info: dict[str, Any],
                today_s: str, holidays: set[str], tiers: dict[str, dict[str, str]]) -> tuple[int, str, bool]:
    """(알림 세기 0=기록만 1=가볍게 2=세게, 이유, 평소 잘 안 나는 곳인지).  이미 지난 날짜나 너무 먼 날짜는 0."""
    dr, eve = day_rank(info["use_dt"], holidays)
    d = datetime.strptime(info["use_dt"], "%Y%m%d")
    days_left = (d - datetime.strptime(today_s, "%Y%m%d")).days
    limit = int(cfg.get("holiday_within_days", 30)) if eve else int(cfg.get("special_within_days", 10))
    far_limit = int(cfg.get("great_within_days", 45))
    if days_left < 0 or days_left > max(limit, far_limit):
        return 0, "", False
    far = days_left > limit
    tier, why = tier_of(info["forest"], tiers, cfg.get("vip_forests", []))
    n, o = state.get("hist", {}).get(fid, [0, 0])
    rare = n >= int(cfg.get("min_history", 24)) and (o / n) <= float(cfg.get("rare_ratio", 0.3))
    if tier == "B":
        level = 1 if (rare and dr <= 2) else 0       # 등급 낮아도 평소 거의 안 나는 곳이 주말에 나면 가볍게
    else:
        good_day = dr <= 3 or eve
        level = 0 if not good_day else (2 if tier == "S" else 1)
        if tier == "A" and rare and (dr <= 2 or eve):
            level = 2                                  # 좋은 곳이 평소엔 안 나다 났다 = 보석
        if tier == "S" and dr == 3 and not rare:
            level = 1                                  # 금요일 S는 가볍게 (일·토가 우선)
    if cfg.get("car_priority", True) and tier == "A" and level == 1 and (dr <= 2 or eve) and car_flag(info) == "yes":
        level = 2                                       # 좋은 곳 + 차를 바로 옆에 대는 자리 = 세게 (B등급은 올리지 않아요)
        why = "차를 사이트 바로 옆에 댈 수 있어요" + (" / " + why if why else "")
    deal, deal_txt = deal_info(cfg, state, fid, info)
    if deal:
        level = max(level, 2 if dr <= 2 or eve else 1)
        why = deal_txt + (" / " + why if why else "")
    if far:
        # 멀리 있는 날짜는 평범한 예약 오픈일 수 있어서, 정말 대박일 때만 알려요
        if deal or (rare and tier in ("S", "A")):
            level = 2
        elif tier == "S" and dr <= 2:
            level = 1
        else:
            level = 0
    return level, why, rare


def pick_special(cfg: dict[str, Any], state: dict[str, Any], new_pairs, today_s: str,
                 holidays: set[str] | None = None, tiers: dict | None = None) -> list[dict[str, Any]]:
    holidays = holidays or set()
    tiers = tiers if tiers is not None else load_tiers()
    out = []
    for key, info in new_pairs:
        fid = key.split("|", 1)[0]
        level, why, rare = alert_level(cfg, state, fid, info, today_s, holidays, tiers)
        if level > 0 and cfg.get("rain_mode", True) and info["use_dt"] in state.get("rainy_dates", []):
            # 비 오는 날 밤은 S 등급(또는 특가)만 알려요. 나머지는 기록만.
            if tier_of(info["forest"], tiers, cfg.get("vip_forests", []))[0] != "S" and not deal_info(cfg, state, fid, info)[0]:
                level = 0
        if level > 0:
            dr, eve = day_rank(info["use_dt"], holidays)
            days_left = (datetime.strptime(info["use_dt"], "%Y%m%d") - datetime.strptime(today_s, "%Y%m%d")).days
            lim = int(cfg.get("holiday_within_days", 30)) if eve else int(cfg.get("special_within_days", 10))
            out.append(dict(info, level=level, why=why, rare=rare, dr=dr, eve=eve, far=days_left > lim, days_left=days_left,
                            deal=deal_info(cfg, state, fid, info)[0]))
    return out


def load_info() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads((BASE / "휴양림정보.json").read_text(encoding="utf-8"))
        return {k: v for k, v in data.get("휴양림", {}).items() if isinstance(v, dict)}
    except Exception:
        return {}


def find_info(forest: str, infos: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for key, v in infos.items():
        if key in forest:
            return v
    return {}


CAR_WORDS = ("오토", "자동차", "차량")
_INFOS_CACHE: dict[str, dict[str, Any]] | None = None


def car_side(prof: dict[str, Any], names: list[str], fac: list[str] | None = None) -> tuple[str, str]:
    """차를 사이트 바로 옆에 댈 수 있는지. ('yes'=옆에 대요, 'some'=일부만, 'no'=주차장에서 짐 옮겨요, 'unknown'=모름, 설명)
    판단 순서: 사이트 이름에 '오토' → 내가 정리한 휴양림 정보('차옆') → 숲나들e 공식 안내 글."""
    auto = [n for n in names if any(w in n for w in CAR_WORDS)]
    if auto:
        return "yes", f"사이트 이름에 '오토캠핑'이 들어 있어요({auto[0]}). 차를 사이트 바로 옆에 대는 구조예요"
    mark = (prof or {}).get("차옆")
    if mark in ("예", "일부", "아니오"):
        return {"예": "yes", "일부": "some", "아니오": "no"}[mark], (prof.get("차옆메모") or prof.get("차") or "")
    txt = " ".join(fac or [])
    if re.search(r"(사이트|데크)\s*(바로\s*)?(옆|앞)\s*(에\s*)?(주차|차량)|차량\s*(진입|접근)\s*(이\s*)?가능|오토\s*캠핑", txt):
        return "yes", "숲나들e 안내에 차량 진입(오토캠핑) 설명이 있어요"
    if re.search(r"주차장에서\s*(도보|걸어|카트|리어카)|차량\s*(진입|접근)\s*(이\s*)?(불가|금지)|도보\s*\d+\s*분", txt):
        return "no", "숲나들e 안내상 주차장에서 걸어서 짐을 옮겨야 해요"
    return "unknown", ""


def car_flag(info: dict[str, Any]) -> str:
    """알림 세기 계산용: 이 자리가 차를 바로 옆에 대는 자리인지 (이름·정리해 둔 정보만 봐요)."""
    global _INFOS_CACHE
    if _INFOS_CACHE is None:
        _INFOS_CACHE = load_info()
    return car_side(find_info(info.get("forest", ""), _INFOS_CACHE), [info.get("name", "")])[0]


def site_kind(name: str) -> str:
    for k in ("파쇄석", "데크", "오토", "자동차", "카라반", "캐빈", "숲속", "산림휴양관", "휴양관"):
        if k in name:
            return {"자동차": "오토캠핑", "오토": "오토캠핑", "숲속": "숲속의집"}.get(k, k)
    return "야영"


def sites_text(names: list[str]) -> str:
    kinds: dict[str, list[str]] = {}
    for n in names:
        kinds.setdefault(site_kind(n), []).append(n)
    parts = []
    for k, ns in kinds.items():
        ex = ", ".join(ns[:3])
        parts.append(f"{k} {len(ns)}곳 ({ex}{' …' if len(ns) > 3 else ''})")
    return " / ".join(parts)


def site_memos(profile: dict[str, Any], names: list[str]) -> list[str]:
    out = []
    for memo in profile.get("사이트메모", []):
        lo, hi = memo.get("번호", [0, 0])
        hit = []
        for n in names:
            m = re.findall(r"\d+", n)
            if m and lo <= int(m[-1]) <= hi:
                hit.append(n)
        if hit:
            out.append(f"{', '.join(hit[:3])}: {memo.get('메모', '')}")
    return out


def free_cancel_date(use_dt: str) -> tuple[str, int]:
    """(공짜로 취소할 수 있는 마지막 날 MM/DD, 며칠 전). 성수기(7/15~8/24)는 5일 전, 아니면 2일 전 기준(블로그 정리, 곳마다 다를 수 있음)."""
    d = datetime.strptime(use_dt, "%Y%m%d")
    md = d.month * 100 + d.day
    days = 5 if 715 <= md <= 824 else 2
    last = d - timedelta(days=days)
    return f"{last.month}/{last.day}({DAY_NAMES[last.weekday()]})", days


def weather_text(cfg: dict[str, Any], fc: dict[str, list[Any]], use_dt: str) -> tuple[str, list[str]]:
    v = fc.get(use_dt)
    if not v:
        return "", []
    pr, mm = v[0], v[1]
    hi = v[2] if len(v) > 2 else None
    lo = v[3] if len(v) > 3 else None
    icon = "☔" if is_rainy(cfg, fc, use_dt) else "☀️"
    txt = f"{icon} 날씨 예보: 비 올 확률 {int(pr)}%"
    if hi is not None and lo is not None:
        txt += f", 낮 {hi:.0f}℃ / 밤 {lo:.0f}℃"
    tips = []
    if lo is not None and lo <= 5:
        tips.append("밤에 많이 추워요. 겨울 침낭·핫팩, 전기가 되면 전기장판")
    elif lo is not None and lo <= 12:
        tips.append("밤에 쌀쌀해요. 두꺼운 침낭이나 전기장판이 있으면 좋아요")
    if is_rainy(cfg, fc, use_dt):
        tips.append("비 예보: 타프를 치거나 차 뒷문 지붕 밑을 쓰세요")
    return txt, tips


REGION_MINUTES = {"경남": 60, "경상남": 60, "부산": 60, "울산": 80, "대구": 100, "경북": 150, "경상북": 150}


def region_minutes(region: str) -> int | None:
    for k, v in REGION_MINUTES.items():
        if k in region:
            return v
    return None


def reason_lines(cfg: dict[str, Any], forest: str, its: list[dict[str, Any]], ctx: dict[str, Any],
                 holidays: set[str], prof: dict[str, Any]) -> list[str]:
    """'왜 지금 가야 하나'를 한 줄씩. 사실(숫자)에 근거한 것만 써요."""
    state, tiers, now = ctx["state"], ctx["tiers"], ctx["now"]
    first = min(i["use_dt"] for i in its)
    fid = its[0].get("fid", "")
    out: list[str] = []
    tier, why = tier_of(forest, tiers, cfg.get("vip_forests", []))
    if tier in ("S", "A"):
        label = "꼭 가볼 곳(S등급)" if tier == "S" else "좋은 곳(A등급)"
        good = why if why and "참고 의견" not in why else (prof.get("한줄") or "")
        out.append(f"{label}{': ' + good if good else ''}")
    score, conf, cnt = review_score(forest, tiers.get("R") or [])
    if cnt and conf >= 1.0:
        out.append(f"후기 {score:.1f}점 ({cnt}건)")
    ap = (state.get("appear") or {}).get(fid)
    if ap:
        gap = ap.get("gap_days")
        if gap is None:
            out.append("처음으로 난 자리예요")
        elif gap >= 1:
            out.append(f"{gap}일 만에 난 자리예요 (그동안 만석)")
        else:
            out.append("방금 난 자리예요 (누가 취소한 듯해요)")
    n, o = (state.get("hist") or {}).get(fid, [0, 0])
    if n >= int(cfg.get("min_history", 24)) and o / n <= 0.5:
        out.append(f"평소엔 자리가 거의 없는 곳이에요 (있던 시간 {int(100 * o / n)}%)")
    dr, eve = day_rank(first, holidays)
    left = (datetime.strptime(first, "%Y%m%d") - datetime.strptime(now.strftime("%Y%m%d"), "%Y%m%d")).days
    when = "오늘" if left == 0 else ("내일" if left == 1 else f"{left}일 뒤")
    day_txt = {1: "일요일 밤(가장 가고 싶은 날)", 2: "토요일 밤", 3: "금요일 밤"}.get(dr, "")
    if eve:
        day_txt = (day_txt + ", " if day_txt else "") + "다음날 쉬는 날 앞날"
    if day_txt:
        out.append(f"{when}인 {day_txt}이에요")
    others = {}
    for info in (ctx.get("found") or {}).values():
        if info["use_dt"] == first and info["forest"] != forest:
            t = tier_of(info["forest"], tiers, cfg.get("vip_forests", []))[0]
            if t in ("S", "A"):
                others[info["forest"]] = t
    if tier in ("S", "A"):
        if others:
            names = ", ".join(sorted(others)[:3])
            out.append(f"같은 날 다른 좋은 곳도 {len(others)}곳 열려 있어요 ({names})")
        else:
            out.append("같은 날 다른 좋은 곳은 모두 만석이에요")
    if its[0].get("deal"):
        out.append("💲 특가: " + its[0]["why"].split(" / ")[0])
    n_sites = len({i["name"] + i["use_dt"] for i in its})
    out.append(f"지금 {n_sites}곳만 남았어요. 인기 있는 날은 몇 분 안에 사라질 수 있어요")
    return out


def weather_short(cfg: dict[str, Any], fc: dict[str, list[Any]], use_dt: str) -> str:
    v = fc.get(use_dt)
    if not v:
        return ""
    icon = "☔" if is_rainy(cfg, fc, use_dt) else "☀️"
    txt = f"{icon} 비 올 확률 {int(v[0])}%"
    if len(v) > 3 and v[2] is not None and v[3] is not None:
        txt += f" · {v[3]:.0f}~{v[2]:.0f}℃"
    return txt


def price_short(prof: dict[str, Any], fac: list[str]) -> str:
    if prof.get("요금"):
        t = re.sub(r"\([^)]*\)", "", prof["요금"]).split(", ")[0].strip()
        return "요금 " + t[:26]
    line = next((l for l in fac if "원" in l), "")
    return ("요금 " + line[:24]) if line else ""


def dday(use_dt: str, now: datetime) -> str:
    left = (datetime.strptime(use_dt, "%Y%m%d") - datetime.strptime(now.strftime("%Y%m%d"), "%Y%m%d")).days
    return "오늘" if left == 0 else ("내일" if left == 1 else f"{left}일 뒤")


def short_text(t: str, n: int = 36) -> str:
    """대괄호 꼬리표를 떼고 n글자 안으로 줄여요 (쉼표 단위로 자르려고 해요)."""
    t = re.sub(r"\s*\[[^\]]*\]", "", t).strip()
    if len(t) <= n:
        return t
    cut = t[:n]
    return cut.rsplit(", ", 1)[0] if ", " in cut else cut


def pick_reasons(rs: list[str], limit: int = 2) -> list[str]:
    """'왜 지금?'에 넣을 이유를 중요한 것부터 limit개만 골라요. (날짜·요일은 맨 위 날짜 줄에 이미 있어서 뺐어요)"""
    skip = ("꼭 가볼", "좋은 곳(", "💲", "지금 ")
    rs = [r for r in rs if not r.startswith(skip) and not re.match(r"^(오늘|내일|\d+일 뒤)인 ", r)]
    rank = lambda r: 0 if ("만에" in r or "방금" in r or "처음" in r) else (
        1 if "평소엔" in r else (2 if "다른 좋은" in r else 3))
    return sorted(rs, key=rank)[:limit]


def card_badges(tier: str, rare: bool, deal: bool, side: str, far: bool) -> str:
    """제목 앞 그림: 🚗=차를 바로 옆에 댐, ⭐=꼭 가볼 곳, 💲=특가. (이 3가지만 써요)"""
    return ("🚗" if side == "yes" else "") + ("⭐" if tier == "S" else "") + ("💲" if deal else "")


def build_card(cfg: dict[str, Any], forest: str, its: list[dict[str, Any]], ctx: dict[str, Any],
               holidays: set[str], mention: str) -> str:
    state, fc, infos, now = ctx["state"], ctx["fc"], ctx["infos"], ctx["now"]
    prof = find_info(forest, infos)
    fid = its[0].get("fid", "")
    name = prof.get("이름", forest)
    tier, tier_why = tier_of(forest, ctx["tiers"], cfg.get("vip_forests", []))
    fac = fetch_facility(ctx.get("session"), fid, state)
    side, side_why = car_side(prof, [i["name"] for i in its], fac)
    deal = bool(its[0].get("deal"))
    lines: list[tuple[int, str]] = []
    if mention:
        lines.append((0, mention))
    badges = card_badges(tier, False, deal, side, False)
    lines.append((0, f"# {badges + ' ' if badges else ''}{name}"))
    by_date: dict[str, list[str]] = {}
    for i in its:
        by_date.setdefault(i["use_dt"], []).append(i["name"])
    first = sorted(by_date)[0]
    for use_dt in sorted(by_date)[:3]:
        n = len(by_date[use_dt])
        lines.append((0, f"**{pretty_date(use_dt, holidays)} 1박 · {dday(use_dt, now)}** · {site_kind(by_date[use_dt][0])} {n}곳 남음"))
    box: list[str] = []
    if side == "yes":
        box.append("🚗 **차를 바로 옆에 대요** · 짐·차 안 쉬기·V2L OK")
    elif side == "some":
        box.append("🚗 **일부만 차를 가까이 대요**" + ((" · " + side_why[:40]) if side_why else ""))
    elif side == "no":
        box.append("🚶 차는 주차장에, 짐은 걸어서 옮겨요")
    if tier == "S":
        good = tier_why if tier_why and "참고 의견" not in tier_why else (prof.get("한줄") or "")
        box.append("⭐ **꼭 가볼 곳**" + (f" · {short_text(good)}" if good else ""))
    elif tier == "A":
        good = tier_why if tier_why and "참고 의견" not in tier_why else (prof.get("한줄") or "")
        box.append("👍 **좋은 곳**" + (f" · {short_text(good)}" if good else ""))
    if deal:
        box.append("💲 **특가** · " + its[0]["why"].split(" / ")[0])
    if box:
        lines.append((0, "\n".join("> " + b for b in box)))
    if side == "unknown":
        lines.append((2, "-# 🚗 차 옆에 댈 수 있는지는 아직 확인 못 했어요"))
    mins = prof.get("분")
    est = ""
    if not mins:
        mins = region_minutes(its[0].get("region", ""))
        est = " 쯤" if mins else ""
    info_bits = [b for b in (price_short(prof, fac), f"진해 {mins}분{est}" if mins else "", weather_short(cfg, fc, first)) if b]
    if info_bits:
        lines.append((1, " · ".join(info_bits)))
    _, tips = weather_text(cfg, fc, first)
    if tips:
        lines.append((2, "-# 🧥 " + tips[0]))
    why = pick_reasons(reason_lines(cfg, forest, its, ctx, holidays, prof))
    if why:
        lines.append((1, "**왜 지금?**\n" + "\n".join("・" + r for r in why)))
    cautions = prof.get("주의", [])[:1]
    for w in cautions:
        lines.append((2, f"⚠️ {w}"))
    if fac and not prof:
        lines.append((3, "-# 숲나들e 안내: " + " / ".join(fac[:2])))
    last, days = free_cancel_date(first)
    today_s = now.strftime("%Y%m%d")
    free_ok = datetime.strptime(first, "%Y%m%d") - timedelta(days=days) >= datetime.strptime(today_s, "%Y%m%d")
    lines.append((1, f"✅ {last}까지 무료 취소" if free_ok else "⚠️ 무료 취소 기간이 지났어요"))
    url, exact = book_url(state, fid, first)
    links = []
    if fid:
        links.append(f"[🔎 사진·시설]({detail_url(fid)})")
        links.append(f"[🛒 예약하기]({url})")
        links.append(f"[💳 결제 확인]({pay_url(fid, state)})")
    else:
        links.append(f"[🛒 예약하기]({RSRVT_PAGE})")
    lines.append((0, "\n" + " ｜ ".join(links)))
    if not exact:
        lines.append((1, f"-# 예약하기 → 숲나들e에서 [{name}] {pretty_date(first)} 고르기 → 예약 뒤 꼭 결제까지!"))
    if not prof and not fac:
        log(f"상세정보 없음(휴양림정보.json에 없는 곳): {forest}")
    for drop in (4, 3, 2, 1):
        text = "\n".join(t for _, t in lines)
        if len(text) <= 1900:
            return text
        lines = [(pr, t) for pr, t in lines if pr < drop]
    return "\n".join(t for _, t in lines)[:1900]


def build_light_line(cfg: dict[str, Any], forest: str, its: list[dict[str, Any]], ctx: dict[str, Any], holidays: set[str]) -> str:
    prof = find_info(forest, ctx["infos"])
    dates = sorted({i["use_dt"] for i in its})[:2]
    side = car_side(prof, [i["name"] for i in its])[0]
    tier = tier_of(forest, ctx["tiers"], cfg.get("vip_forests", []))[0]
    badge = card_badges(tier, False, bool(its[0].get("deal")), side, False)
    mins = prof.get("분") or region_minutes(its[0].get("region", ""))
    bits = [", ".join(pretty_date(d) for d in dates)]
    if mins:
        bits.append(f"진해 {mins}분")
    pr = price_short(prof, [])
    if pr:
        bits.append(pr)
    line = f"**{badge + ' ' if badge else ''}{prof.get('이름', forest)}** · " + " · ".join(bits)
    rs = pick_reasons(reason_lines(cfg, forest, its, ctx, holidays, prof), 1)
    if rs:
        line += "\n   ・" + rs[0]
    fid = its[0].get("fid", "")
    if fid:
        line += f"\n   [🔎 사진]({detail_url(fid)}) ｜ [🛒 예약]({book_url(ctx['state'], fid, dates[0])[0]})"
    return line


def format_special(items: list[dict[str, Any]], holidays: set[str] | None = None, mention: str = "",
                   ctx: dict[str, Any] | None = None) -> list[str]:
    """세게(2) 알림은 글마다 따로(자세한 카드), 가볍게(1) 알림은 한 글에 모아서."""
    holidays = holidays or set()
    cfg = (ctx or {}).get("cfg", DEFAULT_CONFIG)
    order = lambda x: (-x["level"], x["dr"], region_rank(x.get("region", "")), x["forest"], x["use_dt"])
    msgs: list[str] = []
    strong: dict[str, list[dict[str, Any]]] = {}
    light: dict[str, list[dict[str, Any]]] = {}
    for it in sorted(items, key=order):
        (strong if it["level"] >= 2 else light).setdefault(it["forest"], []).append(it)
    for forest, its in list(strong.items())[:4]:
        if ctx:
            msgs.append(build_card(cfg, forest, its, ctx, holidays, mention))
            continue
        far_all = all(i.get("far") for i in its)
        title = "미리 챙기세요! 계획 세울 만한 자리예요" if far_all else "지금 가세요! 놓치면 아까워요"
        lines = [f"{mention + ' ' if mention else ''}🔔🔔 **{title}** {forest}"]
        if its[0].get("deal"):
            lines.append("💲 특가예요!")
        if its[0]["rare"]:
            lines.append("💎 평소엔 거의 자리가 안 나는 곳이에요")
        if its[0]["why"]:
            lines.append(f"왜 좋은 곳? {its[0]['why']}")
        by_date: dict[str, list[str]] = {}
        for i in its:
            by_date.setdefault(i["use_dt"], []).append(i["name"])
        for use_dt, names in list(by_date.items())[:4]:
            lines.append(f"・{pretty_date(use_dt, holidays)} 1박 : {', '.join(names[:3])}")
        lines.append(f"👉 서둘러 예약하세요: {SITE_URL}")
        msgs.append("\n".join(lines))
    if light:
        lines = ["## 괜찮은 자리가 났어요", "-# 급하진 않아요. 시간 될 때 보세요"]
        for forest, its in list(light.items())[:6]:
            if ctx:
                lines.append(build_light_line(cfg, forest, its, ctx, holidays))
            else:
                dates = sorted({i["use_dt"] for i in its})[:3]
                tag = "💎" if its[0]["rare"] else ""
                early = " 📅미리" if all(i.get("far") for i in its) else ""
                lines.append(f"・{forest}{tag}{early} : " + ", ".join(pretty_date(d, holidays) for d in dates))
        if not ctx:
            lines.append(f"👉 {RSRVT_PAGE}")
        out_text = "\n".join(lines[:2])
        for item in lines[2:]:
            if len(out_text) + len(item) + 2 > 1900:
                break
            out_text += "\n\n" + item
        msgs.append(out_text[:1900])
    return msgs


# ---------------------------------------------------------------- 비모드 (비 오는 날 나들이)
def fetch_forecast(cfg: dict[str, Any], timeout: int = 15) -> dict[str, list[float]] | None:
    """Open-Meteo(무료)에서 앞으로 8일 날씨. {YYYYMMDD: [비 확률%, 비 양mm]}. 실패하면 None."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={cfg.get('rain_lat', 35.1331)}&longitude={cfg.get('rain_lon', 128.6906)}"
        "&daily=precipitation_probability_max,precipitation_sum,temperature_2m_max,temperature_2m_min&timezone=Asia%2FSeoul&forecast_days=8"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CampAlert/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as res:
            d = json.loads(res.read().decode("utf-8"))["daily"]
        out = {}
        tmax = d.get("temperature_2m_max") or [None] * len(d["time"])
        tmin = d.get("temperature_2m_min") or [None] * len(d["time"])
        for day, pr, mm, hi, lo in zip(d["time"], d["precipitation_probability_max"], d["precipitation_sum"], tmax, tmin):
            out[day.replace("-", "")] = [float(pr or 0), float(mm or 0), hi, lo]
        return out
    except Exception as e:
        log(f"날씨를 못 가져왔어요(다음에 다시 시도): {e}")
        return None


def refresh_forecast(cfg: dict[str, Any], state: dict[str, Any], now_ts: float | None = None) -> dict[str, list[float]]:
    """3시간에 한 번만 새로 가져오고, 비 오는 날짜 목록(state['rainy_dates'])도 갱신해요."""
    now_ts = now_ts or time.time()
    cache = state.get("forecast") or {}
    if (not cache or now_ts - cache.get("ts", 0) > 3 * 3600) and now_ts - state.get("forecast_fail", 0) > 30 * 60:
        fc = fetch_forecast(cfg)
        if fc is not None:
            state["forecast"] = {"ts": now_ts, "d": fc}
        else:
            state["forecast_fail"] = now_ts   # 실패하면 30분은 쉬었다가 다시
    fc = (state.get("forecast") or {}).get("d", {})
    state["rainy_dates"] = [d for d in fc if is_rainy(cfg, fc, d)]
    return fc


def is_rainy(cfg: dict[str, Any], fc: dict[str, list[float]], day: str) -> bool:
    v = fc.get(day)
    return bool(v) and (v[0] >= float(cfg.get("rain_prob", 60)) or v[1] >= float(cfg.get("rain_mm", 5)))


def load_places() -> list[dict[str, Any]]:
    try:
        data = json.loads((BASE / "비모드장소.json").read_text(encoding="utf-8"))
        return [p for p in data.get("장소", []) if isinstance(p, dict) and p.get("이름")]
    except Exception:
        return []


def pick_places(cfg: dict[str, Any], state: dict[str, Any], places: list[dict[str, Any]],
                reviews: list[dict[str, Any]], n: int = 3) -> list[dict[str, Any]]:
    """가까운 곳 중 후기 점수가 높고, 최근에 안 보여준 곳을 먼저."""
    limit = int(cfg.get("rain_max_minutes", 60))
    shown = state.setdefault("places_shown", {})
    cand = [p for p in places if int(p.get("분", 0)) <= limit]

    def key(p: dict[str, Any]):
        score, conf, cnt = review_score(p["이름"], reviews)
        return (-(score if cnt and conf >= 1.0 else 3.5), shown.get(p["이름"], ""), int(p.get("분", 0)))

    return sorted(cand, key=key)[:n]


def format_rain(cfg: dict[str, Any], fc: dict[str, list[float]], target: str, picks: list[dict[str, Any]],
                holidays: set[str], when: str) -> str:
    pr = fc.get(target, [0, 0])[0]
    lines = [f"## ☔ {when} {pretty_date(target, holidays)} 비 소식 (비 올 확률 {int(pr)}%)",
             "캠핑 대신 가볍게 나가보세요", ""]
    for i, p in enumerate(picks, 1):
        tags = " ".join(f"#{t}" for t in p.get("태그", []))
        low = " (확인 필요)" if p.get("확신") == "낮음" else ""
        lines.append(f"**{i}. {p['이름']}**{low} · 진해 {p.get('분', '?')}분 {tags}")
        if p.get("메모"):
            lines.append(f"   {p['메모']}")
    lines += [
        "",
        "-# 🍱 포장해서 차 뒷문 지붕 밑에서 먹으면 편해요 (포장 음식은 취사가 아니에요)",
        "-# 🔌 V2L로 데울 땐 3.5kW 안으로, 연결부는 비 안 맞게 하세요",
        "-# ⚠️ 공영주차장·공원·바닷가는 취사·야영이 막힌 곳이 많아요. 차박은 오토캠핑장에서만 하세요",
        "-# 다녀오면 Claude에게 \"○○ 좋았어/별로\"라고 알려주세요",
    ]
    return "\n".join(lines)[:1900]


def rain_due(cfg: dict[str, Any], state: dict[str, Any], fc: dict[str, list[float]], now: datetime,
             holidays: set[str]) -> tuple[str, str] | None:
    """(나들이 날짜, '내일'/'오늘') 또는 None. 같은 날짜는 한 번만."""
    if not cfg.get("rain_mode", True) or not fc:
        return None
    days = set(cfg.get("rain_days", [5, 6]))
    sent = set(state.get("rain_sent", []))
    cands = []
    if now.hour >= int(cfg.get("rain_eve_hour", 18)):
        cands.append(((now + timedelta(days=1)).strftime("%Y%m%d"), "내일"))
    if now.hour >= int(cfg.get("heartbeat_hour", 8)):
        cands.append((now.strftime("%Y%m%d"), "오늘"))
    for day, when in cands:
        wd = datetime.strptime(day, "%Y%m%d").weekday()
        if (wd in days or day in holidays) and day not in sent and is_rainy(cfg, fc, day):
            return day, when
    return None


def weekend_outlook(cfg: dict[str, Any], state: dict[str, Any], fc: dict[str, list[float]],
                    found: dict[str, dict[str, Any]], now: datetime, holidays: set[str]) -> str | None:
    """수요일 저녁 '이번 주말 미리보기': 토·일 날씨 + 지금 열려 있는 좋은 캠핑 자리."""
    if now.weekday() != 2 or now.hour < int(cfg.get("rain_eve_hour", 18)) or not cfg.get("outlook", True):
        return None
    week = now.strftime("%G-%V")
    if state.get("outlook_week") == week:
        return None
    sat = (now + timedelta(days=3)).strftime("%Y%m%d")
    sun = (now + timedelta(days=4)).strftime("%Y%m%d")
    lines = ["## 이번 주말 미리보기"]
    wx = []
    for day, name in ((sat, "토"), (sun, "일")):
        v = fc.get(day)
        if v:
            icon = "☔" if is_rainy(cfg, fc, day) else "☀️"
            wx.append(f"{icon} {name} {day[4:6]}/{day[6:]} · 비 {int(v[0])}%")
    if wx:
        lines.append("  ｜  ".join(wx))
    tiers = load_tiers()
    vips = cfg.get("vip_forests", [])
    rows: dict[tuple[str, str], list[str]] = {}
    for info in found.values():
        if info["use_dt"] in (sun, sat):
            t = tier_of(info["forest"], tiers, vips)[0]
            if t in ("S", "A"):
                rows.setdefault((t, info["forest"]), []).append(info["use_dt"])
    lines.append("")
    if rows:
        lines.append("**지금 열려 있는 좋은 자리**")
        for (t, forest), ds in sorted(rows.items(), key=lambda kv: ({"S": 0, "A": 1}[kv[0][0]], kv[0][1]))[:8]:
            days = "・".join(("일" if d == sun else "토") for d in sorted(set(ds), reverse=True))
            lines.append(f"・{'⭐' if t == 'S' else '👍'} {forest} · {days}")
    else:
        lines.append("-# 좋은 자리는 아직 없어요. 나오면 바로 알려드릴게요")
    if any(is_rainy(cfg, fc, d) for d in (sat, sun)):
        picks = pick_places(cfg, state, load_places(), tiers.get("R") or [], n=2)
        if picks:
            lines.append("")
            lines.append("☔ 비 오면 이런 곳 · " + " / ".join(p["이름"] for p in picks))
    state["outlook_week"] = week
    return "\n".join(lines)[:1900]


def maybe_rain(cfg: dict[str, Any], state: dict[str, Any], now: datetime | None = None,
               found: dict[str, dict[str, Any]] | None = None) -> bool:
    now = now or datetime.now()
    try:
        fc = refresh_forecast(cfg, state)
        out = weekend_outlook(cfg, state, fc, found or {}, now, holiday_set(cfg))
        if out:
            notify(cfg, out)
        due = rain_due(cfg, state, fc, now, holiday_set(cfg))
        if not due:
            return bool(out)
        day, when = due
        tiers = load_tiers()
        picks = pick_places(cfg, state, load_places(), tiers.get("R") or [])
        if not picks:
            return False
        notify(cfg, format_rain(cfg, fc, day, picks, holiday_set(cfg), when))
        for p in picks:
            state.setdefault("places_shown", {})[p["이름"]] = now.strftime("%Y%m%d%H%M")
        state["rain_sent"] = (state.get("rain_sent", []) + [day])[-30:]
        return True
    except Exception:
        log("비모드 오류:\n" + traceback.format_exc())
        return False


def rain_now(cfg: dict[str, Any]) -> int:
    """지금 날씨와 비모드 추천을 바로 보여줘요 (알림 기다리기 싫을 때)."""
    fc = fetch_forecast(cfg)
    if not fc:
        print("날씨를 못 가져왔어요. 인터넷을 확인하고 다시 해 주세요.")
        return 1
    state = load_state()
    holidays = holiday_set(cfg)
    lines = ["앞으로 날씨 (비 올 확률 / 예상 비)"]
    for d in sorted(fc):
        mark = "  ☔ 비 오는 날" if is_rainy(cfg, fc, d) else ""
        lines.append(f"  {pretty_date(d, holidays)} : {int(fc[d][0])}% / {fc[d][1]:.0f}mm{mark}")
    picks = pick_places(cfg, state, load_places(), load_tiers().get("R") or [], n=5)
    lines += ["", "비 오는 날 가볼 만한 곳 (진해에서 가까운 순·후기 순)"]
    for i, p in enumerate(picks, 1):
        low = " (확인 필요)" if p.get("확신") == "낮음" else ""
        lines.append(f"  {i}. {p['이름']}{low} - 약 {p.get('분')}분 {' '.join('#' + t for t in p.get('태그', []))}")
        lines.append(f"     {p.get('메모', '')}")
    lines += ["", "※ 포장 음식은 괜찮지만, 공영주차장·공원·바닷가는 취사가 막힌 곳이 많아요. 전기 조리는 장소 규정을 확인하세요."]
    text = "\n".join(lines)
    out = BASE / "비모드.txt"
    out.write_text(text, encoding="utf-8")
    print(text)
    if CLOUD:
        notify_long(cfg, text)
        return 0
    if os.name == "nt":
        try:
            os.startfile(str(out))  # type: ignore[attr-defined]
        except Exception:
            pass
    return 0


def format_messages(new_items: list[dict[str, Any]], max_messages: int = 6, holidays: set[str] | None = None) -> list[str]:
    """새 빈자리를 '휴양림 하나당 글 하나'로 묶어서 알림 폭탄을 막습니다."""
    by_forest: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for it in sorted(new_items, key=lambda x: (x["forest"], x["use_dt"], x["name"])):
        by_forest.setdefault(it["forest"], {}).setdefault(it["use_dt"], []).append(it)
    msgs: list[str] = []
    for forest, by_date in by_forest.items():
        lines = [f"⛺ **빈자리 났어요!** {forest}"]
        for use_dt, items in list(by_date.items())[:5]:
            names = ", ".join(i["name"] for i in items[:3])
            more = f" 외 {len(items) - 3}곳" if len(items) > 3 else ""
            lines.append(f"・{pretty_date(use_dt, holidays)} 1박 : {names}{more}")
        if len(by_date) > 5:
            lines.append(f"・그 밖에 {len(by_date) - 5}일 더")
        lines.append(f"👉 지금 들어가서 예약하세요: {SITE_URL}")
        msgs.append("\n".join(lines))
    if len(msgs) > max_messages:
        extra = len(msgs) - max_messages
        msgs = msgs[:max_messages] + [f"그 밖에도 {extra}개 휴양림에 빈자리가 더 있어요. 숲나들e에서 확인해 주세요."]
    return msgs


REGION_ORDER = ["경남", "경상남", "부산", "울산", "대구", "경북", "경상북"]  # 진해에서 가까운 쪽부터


def region_rank(region: str) -> int:
    for i, key in enumerate(REGION_ORDER):
        if key in region:
            return i
    return len(REGION_ORDER)


def digest_due(cfg: dict[str, Any], state: dict[str, Any], now: datetime | None = None) -> str | None:
    """오늘 아침 '일요일 밤 전체 목록'을 보낼 때가 되었으면 그 일요일 날짜(YYYYMMDD), 아니면 None."""
    now = now or datetime.now()
    wd = int(cfg.get("digest_weekday", 6))
    if not cfg.get("digest", True) or wd not in cfg["weekdays"]:
        return None
    if now.hour < int(cfg["heartbeat_hour"]) or state.get("last_digest") == now.strftime("%Y%m%d"):
        return None
    if now.weekday() == wd:
        return now.strftime("%Y%m%d")
    if now.weekday() == (wd - 1) % 7:
        return (now + timedelta(days=1)).strftime("%Y%m%d")
    return None


def format_digest(found: dict[str, dict[str, Any]], use_dt: str, holidays: set[str] | None = None,
                  now: datetime | None = None) -> list[str]:
    """그날 밤 갈 수 있는 곳을 지역별로 전부 정리한 글 (길면 여러 개로 나눔)."""
    now = now or datetime.now()
    rows = [v for v in found.values() if v["use_dt"] == use_dt]
    label = "오늘 밤" if use_dt == now.strftime("%Y%m%d") else "내일 밤"
    by_forest: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        region = r.get("region", "")
        by_forest.setdefault((region_rank(region), region, r["forest"]), []).append(r)
    head = f"## {label} {pretty_date(use_dt, holidays)} 갈 수 있는 곳\n-# 휴양림 {len(by_forest)}곳 · 자리 {len(rows)}곳"
    if not rows:
        return [head + "\n지금은 빈자리가 없어요."]
    msgs: list[str] = []
    cur = [head]
    size = len(head)
    last_region = None
    for key in sorted(by_forest):
        _, region, forest = key
        items = by_forest[key]
        lines = []
        if region != last_region:
            lines.append(f"**{region or '기타'}**")
            last_region = region
        names = ", ".join(i["name"] for i in sorted(items, key=lambda x: x["name"])[:2])
        lines.append(f"・{forest} ({len(items)}곳) {names}")
        add = "\n".join(lines)
        if size + len(add) > 1650:
            msgs.append("\n".join(cur))
            cur, size = ["(이어서)"], 0
        cur.append(add)
        size += len(add) + 1
    cur.append(f"[요금·예약 보기]({SITE_URL})")
    msgs.append("\n".join(cur))
    return msgs[:4]


def heartbeat_due(cfg: dict[str, Any], state: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return now.hour >= int(cfg["heartbeat_hour"]) and state.get("last_heartbeat") != now.strftime("%Y%m%d")


# ---------------------------------------------------------------- 지금 빈자리 보기
def show_now(cfg: dict[str, Any], days: int = 8) -> int:
    """알림 기다리지 않고, 지금 당장 있는 빈자리를 요일 상관없이 보여줍니다."""
    try:
        session = bootstrap_session(cfg["forest_id"], cfg["forest_pw"])
    except LoginError as e:
        print("\n" + str(e))
        if CLOUD:
            notify(cfg, "⚠️ 지금 빈자리를 못 봤어요: " + str(e))
        return 2
    today = datetime.now().date()
    dates = [(today + timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]
    found, ok_by_forest, errors, targets, _ = collect(cfg, session, dates)
    holidays = holiday_set(cfg)
    by_date: dict[str, dict[str, list[str]]] = {}
    for info in found.values():
        by_date.setdefault(info["use_dt"], {}).setdefault(info["forest"], []).append(info["name"])
    tiers = load_tiers()
    vips = cfg.get("vip_forests", [])
    lines = [f"지금 빈자리 ({datetime.now():%m/%d %H:%M} 기준, 오늘부터 {days}일, 휴양림 {len(targets)}곳 확인)",
             "★★=S등급(꼭 가볼 곳) ★=A등급 ・=그 밖의 곳", ""]
    rank = lambda d: (day_rank(d, holidays)[0], d)
    for d in sorted(dates, key=rank):
        dr, eve = day_rank(d, holidays)
        label = pretty_date(d, holidays) + (" ← 오늘" if d == dates[0] else "")
        forests = by_date.get(d, {})
        lines.append(f"■ {label} 1박 : {len(forests)}개 휴양림 / 자리 {sum(len(v) for v in forests.values())}곳")
        def forest_key(f: str):
            t = tier_of(f, tiers, vips)[0]
            return ({"S": 0, "A": 1, "B": 2}[t], f)
        for forest in sorted(forests, key=forest_key):
            names = forests[forest]
            t = tier_of(forest, tiers, vips)[0]
            mark = {"S": "★★", "A": "★ ", "B": "・"}[t]
            lines.append(f"   {mark}{forest} ({len(names)}곳) 예: {', '.join(names[:3])}")
        lines.append("")
    lines.append("※ 요금은 이 목록에 없어요. 마음에 드는 곳은 숲나들e에서 요금을 확인하세요: " + SITE_URL)
    if errors:
        lines.append(f"※ 조회에 실패한 곳이 {len(errors)}건 있어요.")
    text = "\n".join(lines)
    out = BASE / "지금빈자리.txt"
    out.write_text(text, encoding="utf-8")
    print(text)
    if CLOUD:
        notify_long(cfg, text)
        return 0
    if os.name == "nt":
        try:
            os.startfile(str(out))  # type: ignore[attr-defined]
        except Exception:
            pass
    return 0


# ---------------------------------------------------------------- 계속 감시하기
def keep_awake() -> None:
    """감시 중에는 컴퓨터가 저절로 잠들지 않게 합니다 (윈도우만)."""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        except Exception:
            pass


_exit_sent = False
_ctrl_handler_ref: Any = None


def gap_note(last_ts: float | None, now_ts: float, interval_min: int) -> str | None:
    """감시가 오래 멈춰 있었다면 알려줄 글. 괜찮으면 None."""
    if not last_ts:
        return None
    gap = now_ts - last_ts
    if gap < interval_min * 60 * 2.5 + 120:
        return None
    mins = int(gap // 60)
    human = f"{mins // 60}시간 {mins % 60}분" if mins >= 60 else f"{mins}분"
    last = datetime.fromtimestamp(last_ts)
    return (
        f"⚠️ 감시가 약 {human} 동안 멈춰 있었어요 (마지막 확인 {last:%m/%d %H:%M}).\n"
        "컴퓨터가 꺼졌거나 잠들었던 것 같아요. 그 사이 생긴 빈자리는 못 봤을 수 있어요.\n"
        "지금부터 다시 확인합니다."
    )


def send_exit_alert(cfg: dict[str, Any]) -> None:
    """창이 닫히거나 컴퓨터가 꺼질 때, 마지막으로 폰에 알려요 (한 번만)."""
    global _exit_sent
    if _exit_sent:
        return
    _exit_sent = True
    send_discord(
        cfg["webhook"],
        "🛑 캠핑 빈자리 감시가 멈췄어요.\n창이 닫혔거나 컴퓨터가 꺼지는 중이에요.\n"
        "다시 켜려면 컴퓨터에서 '2_시작하기'를 눌러 주세요.",
        timeout=4,
    )


def install_exit_alert(cfg: dict[str, Any]) -> None:
    import atexit
    import signal

    atexit.register(send_exit_alert, cfg)
    try:
        signal.signal(signal.SIGTERM, lambda *_: (send_exit_alert(cfg), sys.exit(0)))
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes

            global _ctrl_handler_ref
            routine = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

            def handler(event: int) -> int:
                if event in (0, 1, 2, 5, 6):  # Ctrl+C, Ctrl+Break, 창 닫기, 로그오프, 종료
                    send_exit_alert(cfg)
                return 0  # 원래 하던 대로 끝내도록 둠

            _ctrl_handler_ref = routine(handler)  # 사라지지 않게 붙잡아 둠
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_ctrl_handler_ref, True)
        except Exception:
            pass


def ping(cfg: dict[str, Any]) -> None:
    url = cfg.get("ping_url")
    if not url:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "CampAlert/1.0"}), timeout=10).close()
    except Exception as e:
        log(f"바깥 지킴이에게 인사 실패: {e}")


def run_loop(cfg: dict[str, Any], once: bool = False) -> int:
    keep_awake()
    state = load_state()
    if not once:
        note = gap_note(state.get("last_alive"), time.time(), int(cfg["interval_minutes"]))
        if note:
            notify(cfg, note.replace("컴퓨터가 꺼졌거나 잠들었던 것 같아요.", "컴퓨터가 꺼져 있었거나 감시 창이 닫혀 있었어요."))
        install_exit_alert(cfg)
    last_tick = time.time()
    session: Session | None = None
    login_fails = 0
    login_alerted = False
    started_msg_sent = state.get("started_once", False)

    while True:
        now_ts = time.time()
        if not once:
            woke = gap_note(last_tick, now_ts, int(cfg["interval_minutes"]))
            if woke:  # 컴퓨터가 잠들었다 깨어난 경우
                notify(cfg, woke)
        last_tick = now_ts
        try:
            if session is None or time.time() - session.created > 20 * 60:
                log("숲나들e에 로그인합니다...")
                want_probe = (not once) and state.get("probe_version") != 7
                session = bootstrap_session(cfg["forest_id"], cfg["forest_pw"], probe=want_probe)
                log(f"로그인 성공. 휴양림 {len(session.forests)}곳 확인.")
                if want_probe:
                    state["probe_version"] = 7
                    pr = session.probe or {}
                    if pr.get("book_ok"):
                        state["deeplink"] = {"date_key": pr.get("date_key", ""), "tpl": pr.get("tpl", ""), "pay_tpl": pr.get("pay_tpl", "")}
                        msg = ("🔗 예약 링크 점검 성공: 알림의 '예약하기'를 누르면 그 휴양림의 '일반예약' 화면이 열려요. "
                               + ("날짜까지 미리 골라져요!" if pr.get("date_key") else "(날짜는 화면에서 직접 고르세요)"))
                    else:
                        state["deeplink"] = {"pay_tpl": pr.get("pay_tpl", "")}
                        msg = ("🔗 예약 링크 점검: '일반예약' 화면 주소를 아직 못 찾았어요. 알림의 '🛒'는 숲나들e 예약 현황 화면으로 열려요. "
                               "'🔎 자세히 보기'로 보시고 알림에 적힌 휴양림과 날짜를 고르세요.\n(열린 곳: " + str(pr.get("landed") or pr.get("click_landed") or pr.get("start") or "?") + ")")
                    log(msg + f" ({pr})")
                    extra = ("\n(점검 메모: " + " / ".join(pr.get("links", [])[:4]) + ")") if pr.get("links") else ""
                    notify(cfg, msg + extra)
                login_fails = 0
                if login_alerted:
                    notify(cfg, "✅ 로그인이 다시 잘 되고 있어요. 감시를 계속합니다.")
                    login_alerted = False

            result = run_cycle(cfg, session, state)
            log(
                f"확인 끝: 휴양림 {result['scanned']}곳, 지금 빈자리 {result['open_now']}건, "
                f"새로 생김 {len(result['new'])}건, 실패 {len(result['errors'])}건"
            )
            if result["total_jobs"] and len(result["errors"]) >= result["total_jobs"]:
                log("모든 조회가 실패해서 다음번에 다시 로그인합니다: " + "; ".join(result["errors"][:3]))
                session = None
            first_run = not state.get("baseline_done")
            if first_run or once:
                log(f"지금 있는 빈자리 {result['open_now']}건은 조용히 기록만 합니다. 앞으로 새로 생기는 자리만 알려드려요.")
            else:
                if cfg.get("picky", True):
                    for it in result["new"]:
                        log(f"새 자리(기록만): {it['forest']} {it['use_dt']} {it['name']}")
                    ctx = {"cfg": cfg, "state": state, "fc": (state.get("forecast") or {}).get("d", {}),
                           "tiers": load_tiers(), "infos": load_info(), "now": datetime.now(),
                           "found": result["found"], "session": session}
                    msgs = format_special(result["special"], holiday_set(cfg), cfg.get("strong_mention", ""), ctx)
                else:
                    msgs = format_messages(result["new"], holidays=holiday_set(cfg))
                for msg in msgs:
                    notify(cfg, msg)
                    time.sleep(1)
            if result["ok_forests"] > 0:
                state["baseline_done"] = True

            if not started_msg_sent and not once:
                notify(
                    cfg,
                    "✅ 캠핑 빈자리 감시를 시작했어요!\n"
                    f"지역 휴양림 {result['scanned']}곳, 앞으로 {cfg['weeks']}주, "
                    f"{'・'.join(DAY_NAMES[d] for d in cfg['weekdays'])}요일 밤{'과 공휴일 전날 밤' if cfg.get('holiday_eve') else ''}을 {cfg['interval_minutes']}분마다 확인합니다.\n"
                    f"지금 빈자리는 {result['open_now']}건이에요.",
                )
                state["started_once"] = True
                started_msg_sent = True
            digest_date = digest_due(cfg, state) if not once and result["ok_forests"] > 0 else None
            if digest_date:
                for msg in format_digest(result["found"], digest_date, holiday_set(cfg)):
                    notify(cfg, msg)
                    time.sleep(1)
                state["last_digest"] = datetime.now().strftime("%Y%m%d")
                state["last_heartbeat"] = state["last_digest"]
            if not once and cfg.get("rain_mode", True):
                maybe_rain(cfg, state, found=result["found"])
            if not once and heartbeat_due(cfg, state):
                notify(cfg, f"☀️ 잘 돌아가고 있어요 · 지금 빈자리 {result['open_now']}건 · 휴양림 {result['scanned']}곳 확인")
                state["last_heartbeat"] = datetime.now().strftime("%Y%m%d")
            save_state(state)
            if once:
                return 0 if result["ok_forests"] > 0 else 1

        except LoginError as e:
            session = None
            login_fails += 1
            log(f"로그인 문제({login_fails}번째): {e}")
            if once:
                print("\n" + str(e))
                return 2
            if login_fails >= 2 and not login_alerted:
                notify(cfg, f"⚠️ 숲나들e 로그인이 계속 안 돼요.\n{e}\n(아이디/비밀번호를 바꾸셨다면 '1_설치하기'를 다시 실행해 주세요)")
                login_alerted = True
        except Exception:
            session = None
            log("예상 못한 오류:\n" + traceback.format_exc())
            if once:
                return 3

        state["last_alive"] = time.time()
        save_state(state)
        ping(cfg)
        wait = int(cfg["interval_minutes"]) * 60 + random.randint(0, 60)
        time.sleep(wait)
        last_tick = state["last_alive"]


# ---------------------------------------------------------------- 클라우드에서 한 번 확인하기
def run_cloud(cfg: dict[str, Any]) -> int:
    """GitHub(클라우드)가 15분마다 이 함수를 한 번씩 불러요. 컴퓨터를 켜 둘 필요가 없어요.
    한 번 확인하고 끝나지만, 알림·일요일 모아보기·비모드·아침 인사는 집 컴퓨터 때와 똑같이 해요."""
    state = load_state()
    now_ts = time.time()
    last = state.get("last_alive")
    if last and now_ts - last > 100 * 60 and not state.get("cloud_first", True):
        mins = int((now_ts - last) // 60)
        human = f"{mins // 60}시간 {mins % 60}분" if mins >= 60 else f"{mins}분"
        notify(cfg, f"⚠️ 감시가 약 {human} 동안 쉬었어요 (마지막 확인 {datetime.fromtimestamp(last):%m/%d %H:%M}).\n"
                    "GitHub이 붐비면 가끔 늦어져요. 지금 다시 확인했어요. (따로 하실 일은 없어요)")

    try:
        want_probe = state.get("probe_version") != 7
        log("숲나들e에 로그인합니다...")
        session = bootstrap_session(cfg["forest_id"], cfg["forest_pw"], probe=want_probe)
        log(f"로그인 성공. 휴양림 {len(session.forests)}곳 확인.")
        if want_probe:
            state["probe_version"] = 7
            pr = session.probe or {}
            if pr.get("book_ok"):
                state["deeplink"] = {"date_key": pr.get("date_key", ""), "tpl": pr.get("tpl", ""), "pay_tpl": pr.get("pay_tpl", "")}
            else:
                state["deeplink"] = {"pay_tpl": pr.get("pay_tpl", "")}
        if state.get("login_alerted"):
            notify(cfg, "✅ 숲나들e 로그인이 다시 잘 되고 있어요. 감시를 계속합니다.")
        state["login_fails"] = 0
        state["login_alerted"] = False
    except LoginError as e:
        state["login_fails"] = int(state.get("login_fails", 0)) + 1
        log(f"로그인 문제({state['login_fails']}번째): {e}")
        if state["login_fails"] >= 3 and not state.get("login_alerted"):
            notify(cfg, f"⚠️ 숲나들e 로그인이 계속 안 돼요.\n{e}\n"
                        "(숲나들e 비밀번호를 바꾸셨다면 Claude에게 '캠핑알림 비밀번호 바꿨어'라고 말해 주세요)")
            state["login_alerted"] = True
        save_state(state)
        return 0
    except Exception:
        state["errors_in_row"] = int(state.get("errors_in_row", 0)) + 1
        log("예상 못한 오류(로그인 중):\n" + traceback.format_exc())
        if state["errors_in_row"] == 4:
            notify(cfg, "⚠️ 숲나들e에 1시간째 들어가지지 않아요. 사이트 점검 중일 수 있어요. 저절로 다시 시도해요.")
        save_state(state)
        return 0

    try:
        result = run_cycle(cfg, session, state)
        log(f"확인 끝: 휴양림 {result['scanned']}곳, 지금 빈자리 {result['open_now']}건, "
            f"새로 생김 {len(result['new'])}건, 실패 {len(result['errors'])}건")
        if state.get("errors_in_row", 0) >= 4 and result["ok_forests"] > 0:
            notify(cfg, "✅ 숲나들e가 다시 열렸어요. 감시를 계속합니다.")
        state["errors_in_row"] = 0 if result["ok_forests"] > 0 else int(state.get("errors_in_row", 0)) + 1

        if not state.get("baseline_done"):
            log(f"지금 있는 빈자리 {result['open_now']}건은 조용히 기록만 합니다.")
        elif cfg.get("picky", True):
            for it in result["new"]:
                log(f"새 자리(기록만): {it['forest']} {it['use_dt']} {it['name']}")
            ctx = {"cfg": cfg, "state": state, "fc": (state.get("forecast") or {}).get("d", {}),
                   "tiers": load_tiers(), "infos": load_info(), "now": datetime.now(),
                   "found": result["found"], "session": session}
            for msg in format_special(result["special"], holiday_set(cfg), cfg.get("strong_mention", ""), ctx):
                notify(cfg, msg)
                time.sleep(1)
        else:
            for msg in format_messages(result["new"], holidays=holiday_set(cfg)):
                notify(cfg, msg)
                time.sleep(1)
        if result["ok_forests"] > 0:
            state["baseline_done"] = True

        if state.get("cloud_first", True):
            notify(cfg,
                   "☁️ 이제 캠핑 빈자리 감시가 인터넷(클라우드)에서 돌아가요!\n"
                   "집 컴퓨터는 꺼도 돼요. 15분쯤마다 확인하고, 알림은 지금처럼 이 방으로 와요.\n"
                   f"지역 휴양림 {result['scanned']}곳 확인, 지금 빈자리 {result['open_now']}건.")
            state["cloud_first"] = False

        digest_date = digest_due(cfg, state) if result["ok_forests"] > 0 else None
        if digest_date:
            for msg in format_digest(result["found"], digest_date, holiday_set(cfg)):
                notify(cfg, msg)
                time.sleep(1)
            state["last_digest"] = datetime.now().strftime("%Y%m%d")
            state["last_heartbeat"] = state["last_digest"]
        if cfg.get("rain_mode", True):
            maybe_rain(cfg, state, found=result["found"])
        if heartbeat_due(cfg, state):
            notify(cfg, f"☀️ 잘 돌아가고 있어요 · 지금 빈자리 {result['open_now']}건 · 휴양림 {result['scanned']}곳 확인")
            state["last_heartbeat"] = datetime.now().strftime("%Y%m%d")
    except Exception:
        log("예상 못한 오류:\n" + traceback.format_exc())
    state["last_alive"] = time.time()
    save_state(state)
    return 0


def test_connect(cfg: dict[str, Any]) -> int:
    """처음 한 번: 디스코드와 숲나들e가 클라우드에서 되는지 시험해요."""
    ok, why = send_discord(cfg["webhook"], "🧪 클라우드 연결 시험 중이에요... (1~2분 걸려요)")
    if not ok:
        print("디스코드 실패:", why)
        return 1
    try:
        session = bootstrap_session(cfg["forest_id"], cfg["forest_pw"])
    except Exception as e:
        notify(cfg, "❌ 클라우드에서 숲나들e에 못 들어갔어요: " + str(e)[:300])
        log("시험 실패:\n" + traceback.format_exc())
        return 1
    notify(cfg, f"✅ 클라우드 연결 성공! 숲나들e 로그인 OK, 휴양림 {len(session.forests)}곳 확인했어요.")
    return 0


# ---------------------------------------------------------------- 처음 설정
def ask(prompt: str, default: str | None = None) -> str:
    s = input(prompt).strip()
    return s if s else (default or "")


def write_autostart() -> str:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return "윈도우가 아니라서 자동 시작 등록은 건너뜁니다."
    startup = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True, exist_ok=True)
    bat = startup / "캠핑빈자리알림.bat"
    content = (
        "@echo off\r\n"
        "set PYTHONUTF8=1\r\n"
        f'cd /d "{BASE}"\r\n'
        f'start "CampAlert" /min "{sys.executable}" "{Path(__file__).resolve()}" run\r\n'
    )
    # 한국어 윈도우의 검은 창은 기본 글자표(mbcs)로 읽어야 폴더 이름의 한글이 안 깨져요
    bat.write_bytes(content.encode("mbcs" if os.name == "nt" else "utf-8", errors="replace"))
    return f"컴퓨터를 켜면 자동으로 시작되게 등록했어요. ({bat})"


def relocate() -> int:
    """이 폴더를 바탕화면 정리에도 안 사라지는 곳(C:\\캠핑알림)으로 옮기고, 자동 시작도 거기로 다시 맞춰요."""
    import shutil
    import subprocess
    if os.name != "nt":
        print("윈도우에서만 쓰는 기능이에요.")
        return 1
    target = Path(os.environ.get("SystemDrive", "C:") + "\\") / "캠핑알림"
    if BASE.resolve() == target.resolve():
        print(f"이미 안전한 곳({target})에 있어요. 옮길 필요가 없어요.")
        print(write_autostart())
        return 0
    print(f"폴더를 {target} 로 옮깁니다...")
    try:
        shutil.copytree(BASE, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "지금빈자리.txt"))
    except Exception as e:
        print(f"옮기지 못했어요: {e}")
        print("까만 감시 창이 켜져 있으면 먼저 닫고 다시 해 주세요.")
        return 1
    res = subprocess.run([sys.executable, str(target / "camp_alert.py"), "autostart"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONUTF8="1"))
    print(res.stdout.strip())
    print()
    print("끝났어요! 이제 새 곳에서 감시가 시작돼요. (까만 창이 하나 열려요)")
    print(f"바탕화면의 '{BASE.name}' 폴더는 지워도 되고, 정리해도 돼요.")
    try:
        os.startfile(str(target / "2_시작하기.bat"))  # type: ignore[attr-defined]
    except Exception:
        print(f"{target} 안의 2_시작하기 를 직접 눌러 주세요.")
    return 0


def setup() -> int:
    cfg = load_config()
    print("\n=== 캠핑 빈자리 알림 처음 설정 ===")
    print("질문 3개만 답하면 끝나요. 모르겠으면 그냥 엔터(Enter)를 누르세요.\n")

    # 1) 디스코드
    print("[1/3] 디스코드 웹훅 주소를 붙여넣어 주세요.")
    print("      (디스코드 채널 설정 > 연동 > 웹후크 > 'URL 복사'로 얻는 주소예요)")
    while True:
        keep = "(전에 넣은 걸 그대로 쓰려면 그냥 엔터) " if cfg.get("webhook") else ""
        url = ask(f"      주소 {keep}: ", cfg.get("webhook"))
        if not WEBHOOK_RE.match(url):
            print("      ※ 주소 모양이 달라요. https://discord.com/api/webhooks/ 로 시작하는 주소를 다시 붙여넣어 주세요.\n")
            continue
        ok, why = send_discord(url, "✅ 연결 테스트예요. 이 글이 보이면 디스코드 연결 성공!")
        if ok:
            print("      → 디스코드에 테스트 글을 보냈어요. 디스코드에서 확인해 보세요.\n")
            cfg["webhook"] = url
            break
        print(f"      ※ 디스코드로 글을 못 보냈어요({why}). 주소를 다시 확인해 주세요.\n")

    # 2) 숲나들e 계정
    print("[2/3] 숲나들e(foresttrip.go.kr) 아이디와 비밀번호를 입력해 주세요.")
    print("      이 컴퓨터 안에만 저장되고 다른 곳으로는 보내지 않아요.")
    print("      (숲나들e는 로그인해야 빈자리를 볼 수 있어요)")
    keep = "(전에 넣은 걸 그대로 쓰려면 그냥 엔터) " if cfg.get("forest_id") else ""
    cfg["forest_id"] = ask(f"      아이디 {keep}: ", cfg.get("forest_id"))
    cfg["forest_pw"] = ask(f"      비밀번호 {keep}: ", cfg.get("forest_pw"))
    print()

    # 3) 요일
    print("[3/3] 어느 날 밤 자리를 찾을까요? (쉬는 날 앞날 밤은 항상 같이 찾아요)")
    print("      1) 금·토·일요일 밤 (기본, 일요일이 1순위)")
    print("      2) 일요일 밤만")
    print("      3) 토요일·일요일 밤")
    print("      4) 매일")
    choice = ask("      번호 (그냥 엔터 = 1): ", "1")
    cfg["weekdays"] = {"1": [4, 5, 6], "2": [6], "3": [5, 6], "4": [0, 1, 2, 3, 4, 5, 6]}.get(choice, [4, 5, 6])
    cfg["holiday_eve"] = True
    save_config(cfg)

    # 실제 확인 한 번
    print("\n숲나들e에 실제로 로그인해서 한 번 확인해 볼게요. 1~3분 걸려요...\n")
    code = run_loop(cfg, once=True)
    if code != 0:
        print("\n※ 확인 중에 문제가 있었어요. 위 설명을 읽고, 아이디/비밀번호를 확인한 뒤 '1_설치하기'를 다시 실행해 주세요.")
        print("   그래도 안 되면 log.txt 파일 내용을 Claude에게 붙여넣어 주세요.")
        return code

    print("\n" + write_autostart())
    print("\n🎉 설정이 끝났어요! 이제 '2_시작하기'를 더블클릭하면 감시가 시작돼요.")
    print("   (컴퓨터를 껐다 켜도 자동으로 다시 시작돼요)")
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="캠핑 빈자리 알림")
    ap.add_argument("command", choices=["setup", "run", "once", "discord", "autostart", "now", "move", "rain", "cloud", "test"])
    args = ap.parse_args()

    if args.command == "setup":
        return setup()
    if args.command == "move":
        return relocate()
    cfg = load_config()
    if args.command == "autostart":
        print(write_autostart())
        return 0
    if not cfg.get("webhook") or not cfg.get("forest_id"):
        print("아직 설정이 안 됐어요. (클라우드: Secrets에 CAMP_WEBHOOK·FOREST_ID·FOREST_PW 가 있어야 해요)")
        return 1
    if args.command == "discord":
        ok, why = send_discord(cfg["webhook"], "✅ 테스트 메시지예요.")
        print("성공!" if ok else f"실패: {why}")
        return 0 if ok else 1
    if args.command == "once":
        return run_loop(cfg, once=True)
    if args.command == "cloud":
        return run_cloud(cfg)
    if args.command == "test":
        return test_connect(cfg)
    if args.command == "now":
        return show_now(cfg)
    if args.command == "rain":
        return rain_now(cfg)
    print("감시를 시작합니다. 이 창을 닫으면 멈춰요. (작게 줄여두세요)")
    return run_loop(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
