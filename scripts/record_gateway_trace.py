#!/usr/bin/env python3
"""OpenWebNet Gateway Real-World Trace Capture Utility.

Connects to a physical OpenWebNet gateway (F454, MH200, MH202, F455, etc.),
executes an active diagnostic status sweep across all subsystems (lighting, covers,
thermoregulation, clock, diagnostics), captures on-wire bus traffic, anonymizes
credentials, and generates a ready-to-commit CI replay fixture in tests/fixtures/plants/.
"""

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

# Ensure repo root and custom_components are in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from OWNd.connection import OWNCommandSession, OWNEventSession, OWNGateway
    from OWNd.message import OWNMessage
except ImportError:
    print(
        "Error: 'OWNd' library not found. Please install dependencies:\n"
        "  pip install -r requirements_test.txt",
        file=sys.stderr,
    )
    sys.exit(1)

from custom_components.myhome.bus_monitor import BusMonitor  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture real-world bus traces from an OpenWebNet gateway for CI replay."
    )
    parser.add_argument(
        "--host",
        required=True,
        help="IP address or hostname of the OpenWebNet gateway (e.g. 192.168.1.35).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=20000,
        help="OpenWebNet TCP port (default: 20000).",
    )
    parser.add_argument(
        "--password",
        default="12345",
        help="OpenWebNet numeric password (default: 12345).",
    )
    parser.add_argument(
        "--model",
        default="Generic",
        help="Gateway model identifier (e.g. MH200, MH202, F454, F455, F461).",
    )
    parser.add_argument(
        "--name",
        default="",
        help="Custom plant name suffix (default: auto-generated from model).",
    )
    parser.add_argument(
        "--listen",
        type=float,
        default=5.0,
        help="Seconds to listen for ambient bus traffic or manual switch presses (default: 5.0s).",
    )
    parser.add_argument(
        "--yaml",
        default="",
        help="Optional path to existing myhome.yaml device configuration to include in fixture.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Custom output directory. Defaults to tests/fixtures/plants/<plant_id>/.",
    )
    return parser.parse_args()


async def capture_trace(args: argparse.Namespace) -> None:
    clean_model = args.model.lower().replace(" ", "_").replace("/", "_")
    plant_slug = args.name.lower().replace(" ", "_") if args.name else f"{clean_model}_plant"

    if args.output_dir:
        out_dir = Path(args.output_dir).resolve()
    else:
        out_dir = REPO_ROOT / "tests" / "fixtures" / "plants" / plant_slug

    out_dir.mkdir(parents=True, exist_ok=True)
    fixture_mac = "00:03:50:20:00:01"

    print("=" * 70)
    print(f"OpenWebNet Gateway Trace Recorder — Model: {args.model}")
    print(f"Connecting to: {args.host}:{args.port}")
    print("=" * 70)

    # Copy or generate myhome.yaml
    target_yaml = out_dir / "myhome.yaml"
    if args.yaml and Path(args.yaml).is_file():
        shutil.copyfile(args.yaml, target_yaml)
        print(f"[OK] Included device config: {target_yaml.name}")
    elif not target_yaml.is_file():
        with open(target_yaml, "w", encoding="utf-8") as f:
            f.write(
                f"# Real-world plant configuration fixture for {args.model}\n"
                f"{clean_model}:\n"
                f"  mac: \"{fixture_mac}\"\n"
            )
        print(f"[OK] Created stub plant config: {target_yaml.name}")

    bm = BusMonitor(maxlen=1000)

    # Initialize gateway handler
    gw = OWNGateway(
        {
            "address": args.host,
            "port": args.port,
            "password": args.password,
            "serialNumber": fixture_mac,
            "modelName": args.model,
            "friendlyName": f"{args.model} Gateway",
            "manufacturer": "BTicino / Legrand",
        }
    )

    # 1. Active Diagnostic Status Sweep
    print("\n[1/3] Connecting command session & running diagnostic status sweep...")
    session = OWNCommandSession(gateway=gw)
    try:
        conn_res = await session.connect()
        if not conn_res.get("Success"):
            print(f"[ERROR] Failed to connect: {conn_res}", file=sys.stderr)
            sys.exit(1)
        print("      [Connected] Command session established.")

        sweep_queries = [
            "*#13**0##",   # Gateway real-time clock
            "*#13**15##",  # Gateway model & firmware status
            "*#1*0##",     # All lighting & switch actuators
            "*#2*0##",     # All cover actuators
            "*#4*0##",     # Thermoregulation master status
        ]

        for query in sweep_queries:
            bm.record_frame("tx", query, OWNMessage.parse(query))
            res = await session.send(query, is_status_request=True)
            if isinstance(res, list):
                for frame in res:
                    raw_str = str(frame)
                    bm.record_frame("rx", raw_str, OWNMessage.parse(raw_str))
            elif res is True:
                bm.record_frame("rx", "*#*1##", None)
            await asyncio.sleep(0.1)

        print(f"      [Completed] Active sweep dispatched {len(sweep_queries)} queries.")
    finally:
        await session.close()
        # Brief pause for legacy gateway socket recycling
        await asyncio.sleep(0.5)

    # 2. Ambient Event Stream Capture
    if args.listen > 0:
        print(f"\n[2/3] Listening for ambient bus events for {args.listen:.1f}s...")
        print("      (Tip: Press physical buttons, trigger sensors, or toggle lights now!)")
        event_session = OWNEventSession(gateway=gw)
        try:
            ev_conn = await event_session.connect()
            if ev_conn.get("Success"):
                print("      [Connected] Event monitoring session active.")
                try:
                    async with asyncio.timeout(args.listen):
                        while True:
                            msg = await event_session.get_next()
                            if msg:
                                raw_str = str(msg)
                                bm.record_frame("rx", raw_str, OWNMessage.parse(raw_str))
                                print(f"      -> {raw_str}")
                except TimeoutError:
                    pass
            else:
                print("      [Notice] Event session connection skipped or unavailable on this model.")
        finally:
            await event_session.close()

    # 3. Anonymize and Export Diagnostic Summary
    print("\n[3/3] Anonymizing credentials and compiling diagnostic summary...")
    raw_frames = [f.to_dict() for f in bm._frames]

    summary = {
        "home_assistant": {
            "installation_type": "Home Assistant OS",
            "version": "2026.9.1",
            "dev": False,
            "os_name": "Linux",
            "timezone": "Europe/Brussels",
        },
        "data": {
            "integration_version": "2.0.0b11",
            "ownd_version": "2.0.0b5",
            "config_entry": {
                "entry_id": f"{clean_model}_plant_fixture",
                "version": 1,
                "domain": "myhome",
                "title": f"{args.model} Gateway",
                "data": {
                    "host": "192.168.1.50",
                    "port": args.port,
                    "password": "pass",
                    "mac": fixture_mac,
                    "model_name": args.model,
                    "friendly_name": f"{args.model} Gateway",
                    "manufacturer": "BTicino",
                    "firmware": "2.0.0",
                },
                "options": {
                    "file_path": str(target_yaml),
                },
            },
            "gateway": {
                "model_name": args.model,
                "manufacturer": "BTicino",
                "firmware": "2.0.0",
                "is_connected": True,
                "send_workers": 1,
            },
            "profile": {
                "name": args.model,
                "command_queue_delay": 0.05,
                "max_queue_size": 150,
                "keepalive_interval": 60.0,
            },
            "queue": {
                "queue_depth": 0,
                "max_size": 150,
            },
            "platforms": {
                "light": 0,
                "switch": 0,
                "cover": 0,
            },
            "bus_monitor": {
                "stats": {
                    "capacity": len(raw_frames),
                    "captured": len(raw_frames),
                    "total_rx": bm.total_rx,
                    "total_tx": bm.total_tx,
                    "subscribers": 0,
                },
                "recent_frames": raw_frames,
            },
        },
    }

    target_diag = out_dir / "diagnostic_summary.json"
    with open(target_diag, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\n" + "=" * 70)
    print(f"SUCCESS: Captured {len(raw_frames)} authentic frames!")
    print(f"Fixture directory: {out_dir}")
    print("Files created:")
    print(f"  - {target_diag.name} ({len(raw_frames)} frames)")
    print(f"  - {target_yaml.name}")
    print("\nNext steps:")
    print("  1. Verify the replay test: pytest tests/test_trace_replay.py")
    print("  2. Commit and open a Pull Request!")
    print("=" * 70)


def main() -> None:
    args = parse_args()
    asyncio.run(capture_trace(args))


if __name__ == "__main__":
    main()
