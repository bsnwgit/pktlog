#!/usr/bin/env python3
"""
Check a running pkt* install against the resonance data contract.

The contract is a checklist, and every item on it is the kind of thing that
looks fine in a diff and fails in a conversation instead: a parameter nobody
described, a fixed vocabulary published without its enum, a list operation with
no ceiling. This runs the checklist against a live server so the failure lands
here rather than in front of a person asking the assistant a question.

Reads only the two public documents — the grant file and the spec it names —
so it needs no credential and touches no data. Point it at any pkt* app.

Usage:
    python3 scripts/resonance_contract_check.py http://127.0.0.1:8768
    python3 scripts/resonance_contract_check.py https://logs.example.com --ca /path/to/ca.pem

Exit status is 0 when every granted operation satisfies the checklist.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import urljoin

import httpx

GRANT_PATH = "/.well-known/resonance.json"

MIN_DESCRIPTION = 80


def _resolve(schema: dict | None, spec: dict, seen: set | None = None) -> dict:
    """Follow a local $ref one or more hops. External refs are not followed."""
    seen = seen or set()
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        ref = schema["$ref"]
        if not ref.startswith("#/components/schemas/") or ref in seen:
            return {}
        seen.add(ref)
        schema = ((spec.get("components") or {}).get("schemas") or {}).get(ref.rsplit("/", 1)[-1])
    return schema if isinstance(schema, dict) else {}


def returns_rows(schema: dict | None, spec: dict) -> bool:
    """Whether a 200 body can carry a growing list.

    `limit` is required of anything that can return many rows — not of an
    operation whose answer is one object, which is what a write reports back.
    Deciding that from the declared schema rather than from a hand-kept list
    means a read that grows an array later starts being checked automatically.
    """
    schema = _resolve(schema, spec)
    if schema.get("type") == "array" or "items" in schema:
        return True
    for prop in (schema.get("properties") or {}).values():
        resolved = _resolve(prop, spec)
        for branch in resolved.get("anyOf", [resolved]):
            branch = _resolve(branch, spec)
            if branch.get("type") == "array" or "items" in branch:
                return True
    return False


def fetch(client: httpx.Client, base: str, path: str) -> tuple[httpx.Response, dict | None]:
    url = urljoin(base, path)
    response = client.get(url)
    try:
        return response, response.json()
    except ValueError:
        return response, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url", help="Origin of the running app, e.g. https://logs.example.com")
    parser.add_argument("--ca", help="CA bundle for an internally issued certificate")
    parser.add_argument("--insecure", action="store_true",
                        help="Skip certificate verification. For a scratch install only.")
    args = parser.parse_args()

    base = args.base_url if args.base_url.endswith("/") else args.base_url + "/"
    verify: object = args.ca or (not args.insecure)

    problems: list[str] = []
    notes: list[str] = []

    with httpx.Client(verify=verify, timeout=20, follow_redirects=False) as client:
        response, grant = fetch(client, base, GRANT_PATH)
        if response.status_code != 200 or grant is None:
            print(f"FAIL  {GRANT_PATH} -> HTTP {response.status_code}, not JSON"
                  if grant is None else f"FAIL  {GRANT_PATH} -> HTTP {response.status_code}")
            print("      Without a grant file the assistant gets read operations only, and on an")
            print("      app that publishes none of its spec that means nothing at all.")
            return 1

        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("application/json"):
            problems.append(f"grant file served as {content_type or 'no content-type'}, not application/json")
        if grant.get("resonance") != 1:
            problems.append(f"grant file 'resonance' is {grant.get('resonance')!r}, expected 1")

        allow = grant.get("allow") or []
        granted = {entry["op"]: bool(entry.get("writes")) for entry in allow if "op" in entry}
        if not granted:
            problems.append("grant file allows nothing — no operation is reachable")

        spec_path = grant.get("spec")
        if not spec_path:
            print("FAIL  grant file names no spec")
            return 1

        response, spec = fetch(client, base, spec_path)
        if response.status_code != 200 or spec is None:
            print(f"FAIL  {spec_path} -> HTTP {response.status_code}"
                  f"{'' if spec is not None else ', not JSON'}")
            return 1

        version = str(spec.get("openapi", ""))
        if not version.startswith(("3.0", "3.1")):
            problems.append(f"spec declares OpenAPI {version or 'nothing'}; 3.0 or 3.1 required")

    operations: dict[str, tuple[str, str, dict]] = {}
    for path, item in (spec.get("paths") or {}).items():
        for method, operation in item.items():
            if not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId")
            if not op_id:
                problems.append(f"{method.upper()} {path} has no operationId")
                continue
            if op_id in operations:
                problems.append(f"operationId {op_id!r} is used more than once")
            operations[op_id] = (path, method, operation)

    missing = sorted(set(granted) - set(operations))
    if missing:
        problems.append(f"granted but absent from the spec: {', '.join(missing)}")
    extra = sorted(set(operations) - set(granted))
    if extra:
        notes.append(f"in the spec but not granted (unreachable, not an error): {', '.join(extra)}")

    print(f"grant     {len(granted)} operation(s), {sum(granted.values())} of them writing")
    print(f"spec      OpenAPI {spec.get('openapi')} at {spec_path}")
    print()

    for op_id in sorted(granted):
        if op_id not in operations:
            continue
        path, method, operation = operations[op_id]
        faults: list[str] = []

        if not operation.get("summary"):
            faults.append("no summary")
        description = operation.get("description") or ""
        if len(description) < MIN_DESCRIPTION:
            faults.append("description too thin — a model chooses on these sentences alone")

        parameters = operation.get("parameters") or []
        undescribed = [p.get("name") for p in parameters if not p.get("description")]
        if undescribed:
            faults.append(f"undescribed parameter(s): {', '.join(str(n) for n in undescribed)}")

        enums = {}
        for parameter in parameters:
            schema = parameter.get("schema") or {}
            for branch in schema.get("anyOf", [schema]):
                if isinstance(branch, dict) and "enum" in branch:
                    enums[parameter.get("name")] = len(branch["enum"])

        ok = (operation.get("responses") or {}).get("200") or {}
        ok_schema = (ok.get("content") or {}).get("application/json", {}).get("schema")

        limit = next((p for p in parameters if str(p.get("name", "")).endswith("limit")), None)
        if limit is None and returns_rows(ok_schema, spec):
            faults.append("returns a list but takes no limit parameter")
        elif limit is not None:
            schema = limit.get("schema") or {}
            if schema.get("default") is None:
                faults.append("limit has no default")
            if schema.get("maximum") is None:
                faults.append("limit has no hard maximum")

        if not ok_schema:
            faults.append("no declared 200 response schema")

        if granted[op_id] and method.lower() in ("get", "head"):
            notes.append(f"{op_id} is marked writes:true on a GET — correct if it changes state, "
                         f"but worth confirming it is not a mislabel")

        marker = "ok  " if not faults else "FAIL"
        summary_bits = f"params={len(parameters)}"
        if enums:
            summary_bits += f" enums={','.join(f'{k}[{v}]' for k, v in sorted(enums.items()))}"
        if limit is not None:
            schema = limit.get("schema") or {}
            summary_bits += f" {limit['name']}={schema.get('default')}/{schema.get('maximum')}"
        print(f"[{marker}] {op_id:26} {method.upper():4} {path}"
              f"{'   [writes]' if granted[op_id] else ''}")
        print(f"         {summary_bits}")
        for fault in faults:
            print(f"         -> {fault}")
            problems.append(f"{op_id}: {fault}")

    encoded = json.dumps(spec)
    dangling = ({ref.rsplit("/", 1)[-1]
                 for ref in re.findall(r'"#/components/schemas/([^"]+)"', encoded)}
                - set((spec.get("components") or {}).get("schemas") or {}))
    if dangling:
        problems.append(f"spec references schemas it does not define: {', '.join(sorted(dangling))}")

    # Only a local $ref into the same document is resolved at the far end.
    external = sorted({ref for ref in re.findall(r'"\$ref"\s*:\s*"([^"]+)"', encoded)
                       if not ref.startswith("#/")})
    if external:
        problems.append(f"spec uses $ref to another document, which is not followed: "
                        f"{', '.join(external)}")

    print()
    for note in notes:
        print(f"note   {note}")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nevery granted operation satisfies the contract")
    return 0


if __name__ == "__main__":
    sys.exit(main())
