"""Probe KorService2 parameter acceptance. Prints only result codes and field names; never the key."""
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, quote, quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT))
from app.festival_data.settings import Settings  # noqa: E402

s = Settings.load(ROOT)
key = s.tourapi_key
secrets = [key, quote(key, safe=""), quote_plus(key)]
BASE = "https://apis.data.go.kr/B551011/KorService2/"


def scrub(text):
    for sec in secrets:
        if sec:
            text = text.replace(sec, "[REDACTED]")
    return text


def call(op, extra):
    params = {"serviceKey": key, "_type": "json", "MobileOS": "ETC", "MobileApp": s.mobile_app, **extra}
    url = BASE + op + "?" + urlencode(params)
    try:
        with urlopen(Request(url, headers={"User-Agent": "probe"}), timeout=30) as r:
            raw = r.read().decode("utf-8-sig", "replace")
            status = r.status
    except HTTPError as e:
        raw = e.read().decode("utf-8-sig", "replace")
        status = e.code
    out = {"op": op, "params": {k: v for k, v in extra.items()}, "http": status}
    try:
        payload = json.loads(raw)
        resp = payload.get("response", payload)
        header = resp.get("header", {})
        out["resultCode"] = header.get("resultCode") or payload.get("resultCode")
        out["resultMsg"] = scrub(str(header.get("resultMsg") or payload.get("resultMsg")))
        items = (resp.get("body", {}) or {}).get("items") or {}
        items = items.get("item", []) if isinstance(items, dict) else []
        if isinstance(items, dict):
            items = [items]
        out["count"] = len(items)
        out["fields"] = sorted({k for it in items for k in it})
        out["sample"] = [{k: scrub(str(it.get(k))) for k in ("imgname", "cpyrhtDivCd", "serialnum", "originimgurl", "firstimage", "title") if k in it} for it in items[:8]]
    except json.JSONDecodeError:
        try:
            root = ET.fromstring(raw)
            out["xml"] = {el.tag: scrub((el.text or "").strip()) for el in root.iter() if el.tag in ("resultCode", "resultMsg", "returnReasonCode", "returnAuthMsg", "errMsg")}
        except ET.ParseError:
            out["raw_head"] = scrub(raw[:300])
    print(json.dumps(out, ensure_ascii=False))


cid = sys.argv[2] if len(sys.argv) > 2 else "737479"
call("detailImage2", {"contentId": cid, "imageYN": "Y", "numOfRows": 20, "pageNo": 1})
call("detailImage2", {"contentId": cid, "numOfRows": 20, "pageNo": 1})
call("detailImage2", {"contentId": cid, "imageYN": "Y", "subImageYN": "Y", "numOfRows": 20, "pageNo": 1})
call("detailCommon2", {"contentId": cid})
call("detailCommon2", {"contentId": cid, "firstImageYN": "Y"})
