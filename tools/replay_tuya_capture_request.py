#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


DROP_HEADERS = {
    "accept-encoding",
    "connection",
    "content-length",
    "host",
    "http2-settings",
    "proxy-connection",
    "te",
    "transfer-encoding",
}

SENSITIVE_MARKERS = ("password", "passwd", "sid", "token", "cookie", "authorization")


def redact_key_value(key, value):
    if any(marker in key.lower() for marker in SENSITIVE_MARKERS):
        return "***"
    if value is None:
        return value
    if len(value) > 220:
        return value[:220] + "..."
    return value


def load_records(capture_path, api_names):
    with tempfile.NamedTemporaryFile(prefix="tuya-mitm-", suffix=".jsonl", delete=False) as tmp:
        export_path = tmp.name

    env = os.environ.copy()
    env["TUYA_MITM_EXPORT"] = export_path
    env["TUYA_TARGET_APIS"] = ",".join(api_names)
    addon = os.path.join(os.path.dirname(__file__), "export_tuya_mitm_requests.py")
    try:
        subprocess.run(
            ["mitmdump", "-q", "-nr", capture_path, "-s", addon],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
        )
        with open(export_path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    finally:
        try:
            os.unlink(export_path)
        except FileNotFoundError:
            pass


def choose_record(records, api_name, occurrence):
    matches = [record for record in records if record["api"] == api_name]
    if not matches:
        raise SystemExit(f"No captured request found for API: {api_name}")
    if occurrence < 0:
        return matches[occurrence]
    if occurrence >= len(matches):
        raise SystemExit(f"Only {len(matches)} captured request(s) found for API: {api_name}")
    return matches[occurrence]


def replay(record, timeout):
    headers = {
        key: value
        for key, value in record["headers"].items()
        if key.lower() not in DROP_HEADERS
    }
    data = record["body"].encode("utf-8")
    request = urllib.request.Request(
        record["url"],
        data=data,
        headers=headers,
        method=record["method"],
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, dict(response.headers.items()), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, dict(exc.headers.items()), body


def print_record_summary(record):
    print(f"captured_api={record['api']}")
    print(f"captured_version={record['version']}")
    print(f"captured_status={record['response_status']}")
    for field in ("url", "body"):
        print(f"captured_{field}={redact_key_value(field, record[field])}")


def main():
    parser = argparse.ArgumentParser(
        description="Replay one Tuya mobile API request from a mitmproxy capture."
    )
    parser.add_argument("capture", help="Path to .mitm capture")
    parser.add_argument("--api", default="m.life.home.space.list", help="API name to replay")
    parser.add_argument(
        "--occurrence",
        type=int,
        default=-1,
        help="0-based occurrence to replay; negative values count from the end",
    )
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument(
        "--list",
        action="store_true",
        help="List captured API counts instead of replaying a request",
    )
    args = parser.parse_args()

    if args.list:
        records = load_records(args.capture, [])
        counts = {}
        for record in records:
            counts[record["api"]] = counts.get(record["api"], 0) + 1
        for api, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            print(f"{count}\t{api}")
        return 0

    records = load_records(args.capture, [args.api])
    record = choose_record(records, args.api, args.occurrence)
    print_record_summary(record)
    status, headers, body = replay(record, args.timeout)
    print(f"replay_status={status}")
    print(f"replay_content_type={headers.get('Content-Type', '')}")
    print(f"replay_body={redact_key_value('body', body)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
