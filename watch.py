import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Europe/Stockholm")
URL = "https://platform.sweetspot.io/api/tee-times"
STATE_FILE = Path("state.json")
CONFIG_FILE = Path("config.json")

COURSES = {
    "Bodaholm": "ac165789-77f6-43c5-ae15-907ae3c4b814",
    "Brollsta 18": "292e2543-f661-403f-b1d6-a5086d251061",
    "International": "2ee26a65-028d-46e0-809d-635bf3c06a5a",
    "Kungs Kings": "c006a958-4c27-4a58-9442-627a7aebc843",
    "Kungs Queen": "583bf680-75c3-4281-b78d-be5ebbcdac1f",
    "Kyssinge 18": "f10b066a-205b-4690-af58-83c43cff55c1",
    "Lindö Dal": "e02768fc-caff-49b6-a86d-237179cf5ca8",
    "Lindö Äng": "6ec41746-b529-46bc-ac81-514095105d54",
    "Lövsättra": "ae776456-e51a-4be7-8b55-f869c62d03b3",
    "Riksten": "19ceb886-66ed-4489-9ddd-6e86642a22be",
    "Viksberg": "96445d62-468b-43ae-b589-fe9da3de4a38",
    "Waxholm 18": "410fdd67-a108-4b3f-8058-1ff66fc061c2",
}

BLOCKED_WORDS = ("tävling", "tournament", "competition")


def fetch_course(uuid, start_local, end_local, players):
    """Returnerar lista med (tid-sträng, lediga platser) för en bana."""
    params = {
        "course.uuid": uuid,
        "from[after]": start_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "from[before]": end_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "limit": 9999,
        "order[from]": "asc",
        "page": 1,
        "partial": "true",
    }
    r = requests.get(URL, params=params, timeout=15)
    r.raise_for_status()

    found = []
    for t in r.json():
        cat = t.get("category") or {}
        text = ((cat.get("name") or "") + " " + (cat.get("description") or "")).lower()

        if t.get("available_slots", 0) < players:
            continue
        if cat.get("display") == "none":
            continue
        if not cat.get("tee_time_bookable", False):
            continue
        if any(w in text for w in BLOCKED_WORDS):
            continue

        tee = datetime.fromisoformat(t["from"]).astimezone(TZ)
        if start_local <= tee <= end_local:
            found.append((tee.strftime("%Y-%m-%d %H:%M"), t["available_slots"]))
    return found


def notify(message):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC saknas, skriver bara ut:\n" + message)
        return
    requests.post(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": "Nya golftider", "Priority": "high", "Tags": "golf"},
        timeout=15,
    ).raise_for_status()


def main():
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    previous = set(json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else set()
    now = datetime.now(TZ)

    current = set()
    failed_courses = set()
    details = {}  # key -> text att visa

    for s in config["searches"]:
        start_local = datetime.fromisoformat(f"{s['date']} {s['from']}").replace(tzinfo=TZ)
        end_local = datetime.fromisoformat(f"{s['date']} {s['to']}").replace(tzinfo=TZ)
        if end_local < now:
            continue  # sökningen har passerat

        players = s.get("players", 4)
        wanted = s.get("courses", "all")
        names = list(COURSES) if wanted == "all" else wanted

        for name in names:
            if name not in COURSES:
                print(f"Okänd bana i config: {name}")
                continue
            try:
                for tid, slots in fetch_course(COURSES[name], start_local, end_local, players):
                    key = f"{name}|{tid}"
                    current.add(key)
                    details[key] = f"{name} {tid[5:]} ({slots} platser)"
            except Exception as e:
                print(f"Fel vid {name}: {e}")
                failed_courses.add(name)

    # Behåll tidigare kända tider för banor som felade, så vi inte får falska "nya" nästa gång
    for key in previous:
        if key.split("|")[0] in failed_courses:
            current.add(key)

    new = sorted(k for k in current - previous if k in details)
    print(f"Nu: {len(current)} tider, nya: {len(new)}")

    if new:
        msg = "\n".join(details[k] for k in new)
        notify(msg[:3500])

    STATE_FILE.write_text(json.dumps(sorted(current), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
