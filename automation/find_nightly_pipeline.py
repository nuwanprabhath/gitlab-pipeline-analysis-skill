#!/usr/bin/env python3
"""Resolve the most recent "nightly" pipeline id for a project via `glab api`.

Prints the pipeline id to stdout, or exits 1 with a message on stderr.
Supports three detection modes (see automation/config.example.env).
"""

import argparse
import datetime
import json
import subprocess
import sys
import urllib.parse


def glab_api(path):
    proc = subprocess.run(
        ["glab", "api", path], capture_output=True, text=True
    )
    if proc.returncode != 0:
        sys.exit("glab api failed for {}: {}".format(path, proc.stderr.strip()))
    return json.loads(proc.stdout)


def in_window(created_at, start_hour, end_hour):
    ts = datetime.datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone()
    if start_hour <= end_hour:
        return start_hour <= ts.hour < end_hour
    # Window wraps midnight, e.g. 23 -> 2
    return ts.hour >= start_hour or ts.hour < end_hour


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--project", required=True, help="group/project")
    ap.add_argument(
        "-m",
        "--mode",
        default="trigger_window",
        choices=["trigger_window", "ref", "schedule_id"],
    )
    ap.add_argument("--trigger-user", default="")
    ap.add_argument("--window-start-hour", type=int, default=23)
    ap.add_argument("--window-end-hour", type=int, default=2)
    ap.add_argument("--ref", default="", help="exact ref, or prefix ending in *")
    ap.add_argument("--schedule-id", default="")
    ap.add_argument(
        "--scan",
        type=int,
        default=300,
        help="how many recent pipelines to scan (paged 100 at a time); modes that "
        "filter client-side (ref prefix, or an overnight window with no trigger "
        "user) need a large budget because MR pipelines crowd the recent list",
    )
    ap.add_argument(
        "--max-age-hours",
        type=float,
        default=24.0,
        help="reject a match older than this (0 disables the check)",
    )
    args = ap.parse_args()

    enc = urllib.parse.quote(args.project, safe="")

    base_query = {"order_by": "id", "sort": "desc"}
    if args.mode == "schedule_id":
        if not args.schedule_id:
            sys.exit("mode=schedule_id requires --schedule-id")
        base_path = "projects/{}/pipeline_schedules/{}/pipelines".format(
            enc, args.schedule_id
        )
        base_query = {}
    else:
        base_path = "projects/{}/pipelines".format(enc)
        if args.mode == "trigger_window" and args.trigger_user:
            base_query["username"] = args.trigger_user
        if args.mode == "ref":
            if not args.ref:
                sys.exit("mode=ref requires --ref")
            if not args.ref.endswith("*"):
                base_query["ref"] = args.ref

    def matches(p):
        if args.mode == "trigger_window":
            return in_window(p["created_at"], args.window_start_hour, args.window_end_hour)
        if args.mode == "ref" and args.ref.endswith("*"):
            return p.get("ref", "").startswith(args.ref[:-1])
        return True

    match = None
    scanned = 0
    page = 1
    while scanned < args.scan and match is None:
        per_page = min(100, args.scan - scanned)
        query = dict(base_query, per_page=str(per_page), page=str(page))
        batch = glab_api("{}?{}".format(base_path, urllib.parse.urlencode(query)))
        if not batch:
            break
        scanned += len(batch)
        page += 1
        match = next((p for p in batch if matches(p)), None)

    if match is None:
        sys.exit("no pipeline matched (mode={}, scanned {})".format(args.mode, scanned))

    if args.max_age_hours > 0:
        created = datetime.datetime.fromisoformat(
            match["created_at"].replace("Z", "+00:00")
        )
        age_h = (
            datetime.datetime.now(datetime.timezone.utc) - created
        ).total_seconds() / 3600
        if age_h > args.max_age_hours:
            sys.exit(
                "newest match #{} is {:.1f}h old (> --max-age-hours {}); "
                "the nightly run probably did not happen".format(
                    match["id"], age_h, args.max_age_hours
                )
            )

    print(match["id"])


if __name__ == "__main__":
    main()
