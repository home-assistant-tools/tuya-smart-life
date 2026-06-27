import json
import os
from urllib.parse import parse_qs, urlparse


OUTPUT_PATH = os.environ.get("TUYA_MITM_EXPORT")
TARGET_APIS = {
    item.strip()
    for item in os.environ.get("TUYA_TARGET_APIS", "").split(",")
    if item.strip()
}


def load(_loader):
    if not OUTPUT_PATH:
        raise RuntimeError("Set TUYA_MITM_EXPORT to the JSONL output path")
    open(OUTPUT_PATH, "w", encoding="utf-8").close()


def response(flow):
    if flow.request.pretty_host not in {"a1.tuyaus.com", "a1-sg.iotbing.com"}:
        return
    if not flow.request.path.startswith("/api.json"):
        return

    body_text = flow.request.get_text(strict=False)
    body = parse_qs(body_text, keep_blank_values=True)
    query = parse_qs(urlparse(flow.request.url).query, keep_blank_values=True)
    api_name = (body.get("a") or query.get("a") or [""])[0]
    if TARGET_APIS and api_name not in TARGET_APIS:
        return

    record = {
        "api": api_name,
        "version": (body.get("v") or query.get("v") or [""])[0],
        "method": flow.request.method,
        "url": flow.request.url,
        "headers": dict(flow.request.headers.items(multi=True)),
        "body": body_text,
        "response_status": flow.response.status_code if flow.response else None,
        "response_body": flow.response.get_text(strict=False) if flow.response else "",
    }
    with open(OUTPUT_PATH, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
