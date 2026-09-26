import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / 'tests' / 'fixtures'
README_MD = REPO_ROOT / 'README.md'
DOCS_MD = REPO_ROOT / 'docs' / 'trace-availability.md'

START_MARKER = '<!-- TRACE_MATRIX_START -->'
END_MARKER = '<!-- TRACE_MATRIX_END -->'

def get_who(frame):
    if not frame.startswith('*'):
        return None
    m = re.match(r'^\*#?(\d+)\*', frame)
    if m:
        return int(m.group(1))
    return None

def build_matrix():
    # Gather data
    gateways_data = {}

    for p in FIXTURES_DIR.rglob('*'):
        if not p.is_file():
            continue

        if p.suffix == '.json':
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    j = json.load(f)

                whos = set()
                gateway = 'Unknown'

                if 'data' in j and 'gateway' in j['data']:
                    gateway = j['data']['gateway'].get('model_name', 'Unknown')
                    if 'bus_monitor' in j['data'] and 'recent_frames' in j['data']['bus_monitor']:
                        for frame_obj in j['data']['bus_monitor']['recent_frames']:
                            w = get_who(frame_obj['raw'])
                            if w is not None:
                                whos.add(w)
                elif 'frames' in j:
                    for frame_obj in j['frames']:
                        w = get_who(frame_obj['raw'])
                        if w is not None:
                            whos.add(w)
                    if 'gateway' in j and isinstance(j['gateway'], dict) and j['gateway'].get('model'):
                        gateway = j['gateway']['model']
                    else:
                        m = re.search(r'_(?:trace|sweep)_([A-Za-z0-9]+)_', p.name)
                        if m:
                            gateway = m.group(1)

                if whos:
                    if gateway not in gateways_data:
                        gateways_data[gateway] = set()
                    gateways_data[gateway].update(whos)
            except Exception:
                pass

        elif p.suffix == '.txt':
            try:
                with open(p, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                whos = set()
                gateway = 'F454' # from issue_429 readme
                for line in lines:
                    line_clean = re.sub(r'^\[.*?\]\s*\[.*?\]\s*', '', line.strip())
                    w = get_who(line_clean)
                    if w is not None:
                        whos.add(w)
                if whos:
                    if gateway not in gateways_data:
                        gateways_data[gateway] = set()
                    gateways_data[gateway].update(whos)
            except Exception:
                pass

    # Build markdown table
    all_whos_found = set()
    for whos in gateways_data.values():
        all_whos_found.update(whos)

    whos_list = sorted(list(all_whos_found))

    who_names = {
        0: 'Scenario', 1: 'Lights', 2: 'Autom.', 4: 'Climate', 5: 'Alarm', 9: 'Power',
        13: 'Gateway', 14: 'Lock', 15: 'CEN', 16: 'Audio', 17: 'Scenario', 18: 'Energy',
        22: 'Audio Diff.', 25: 'Diag', 1013: 'Diag', 1022: 'Diag'
    }

    GATEWAY_DISPLAY_NAMES = {
        "H4890": "H4890 / AM4890",
    }

    header = "| Gateway Model | " + " | ".join([f"WHO {w}<br>{who_names.get(w, '')}" for w in whos_list]) + " |"
    separator = "| :--- | " + " | ".join([" :---: " for _ in whos_list]) + " |"

    rows = []
    # Sort gateways
    sorted_gateways = sorted(gateways_data.keys(), key=lambda g: GATEWAY_DISPLAY_NAMES.get(g, g))
    for gw in sorted_gateways:
        disp_name = GATEWAY_DISPLAY_NAMES.get(gw, gw)
        row = f"| **{disp_name}** | "
        cols = []
        for w in whos_list:
            if w in gateways_data[gw]:
                cols.append("✅")
            else:
                cols.append("")
        row += " | ".join(cols) + " |"
        rows.append(row)

    md = header + "\n" + separator + "\n" + "\n".join(rows)
    return md

def update_file(filepath, new_content):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    pattern = re.compile(f"{START_MARKER}.*?{END_MARKER}", re.DOTALL)
    replacement = f"{START_MARKER}\n{new_content}\n{END_MARKER}"

    if pattern.search(content):
        updated = pattern.sub(replacement, content)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(updated)
        print(f"Updated {filepath.name}")
    else:
        print(f"Markers not found in {filepath.name}")

def sync_github_issue(issue_number=466, new_table=None):
    """Sync the updated trace matrix table to the community tracking issue description."""
    import os
    import urllib.request

    token = os.environ.get("GITHUB_PAT") or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("No GitHub token (GITHUB_PAT/GITHUB_TOKEN) found; skipping GitHub issue sync.")
        return

    # In CI, only sync on push to main/master/v2 branches, not on pull_request runs
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    if event_name and event_name != "push":
        print(f"Skipping GitHub issue sync for event '{event_name}' (only syncs on push).")
        return

    repo = os.environ.get("GITHUB_REPOSITORY", "OpenWebNet-HA/MyHOME")
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "MyHOME-TraceMatrixSync",
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Failed to fetch issue #{issue_number}: {e}")
        return

    body = data.get("body", "")
    pattern = re.compile(f"{START_MARKER}.*?{END_MARKER}", re.DOTALL)
    replacement = f"{START_MARKER}\n{new_table}\n{END_MARKER}"

    if pattern.search(body):
        new_body = pattern.sub(replacement, body)
    else:
        # If markers are not yet in the issue body, match the existing markdown table
        table_pattern = re.compile(r"(\| Gateway Model \|.*?\n\| :---.*?\n(?:\|.*?\n)+)", re.MULTILINE)
        if table_pattern.search(body):
            new_body = table_pattern.sub(f"{START_MARKER}\n{new_table}\n{END_MARKER}\n", body, count=1)
        else:
            print(f"Could not locate trace matrix table or markers in issue #{issue_number}")
            return

    if new_body == body:
        print(f"Issue #{issue_number} description is already up to date.")
        return

    patch_data = json.dumps({"body": new_body}).encode("utf-8")
    patch_req = urllib.request.Request(url, data=patch_data, headers=headers, method="PATCH")
    try:
        with urllib.request.urlopen(patch_req) as resp:
            if resp.status == 200:
                print(f"Successfully updated issue #{issue_number} description on GitHub.")
            else:
                print(f"Updating issue #{issue_number} returned status {resp.status}")
    except Exception as e:
        print(f"Failed to update issue #{issue_number}: {e}")

if __name__ == '__main__':
    matrix_md = build_matrix()
    print("Generated Matrix:")
    print("Done")
    update_file(README_MD, matrix_md)
    update_file(DOCS_MD, matrix_md)
    sync_github_issue(issue_number=466, new_table=matrix_md)


