"""
Fantasy Football Data Fetcher
Pulls PPR rankings, ADP, player headshots, and news from multiple sources.
Outputs structured JSON files to /data for the static site to consume.
"""

import json
import time
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

session = requests.Session()
session.headers.update(HEADERS)


# ---------------------------------------------------------------------------
# Sleeper — player database + headshots
# ---------------------------------------------------------------------------

def fetch_sleeper_players() -> dict:
    """Returns a dict keyed by player_id with name, team, position, photo URL."""
    print("  Fetching Sleeper player database...")
    url = "https://api.sleeper.app/v1/players/nfl"
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    raw = resp.json()

    players = {}
    for pid, p in raw.items():
        pos = p.get("position", "")
        if pos not in POSITIONS:
            continue
        players[pid] = {
            "sleeper_id": pid,
            "name": p.get("full_name") or f"{p.get('first_name','')} {p.get('last_name','')}".strip(),
            "first_name": p.get("first_name", ""),
            "last_name": p.get("last_name", ""),
            "position": pos,
            "team": p.get("team") or "FA",
            "number": p.get("number"),
            "status": p.get("status", ""),
            "injury_status": p.get("injury_status") or "",
            "headshot_url": f"https://sleepercdn.com/content/nfl/players/thumb/{pid}.jpg",
            "espn_id": p.get("espn_id"),
            "yahoo_id": p.get("yahoo_id"),
        }
    print(f"    Loaded {len(players)} players from Sleeper.")
    return players


# ---------------------------------------------------------------------------
# FantasyPros — PPR rankings + ADP
# ---------------------------------------------------------------------------

FP_POSITION_SLUGS = {
    "QB":  "qb",
    "RB":  "ppr-rb",
    "WR":  "ppr-wr",
    "TE":  "ppr-te",
    "K":   "k",
    "DST": "dst",
}

FP_ADP_URL = "https://www.fantasypros.com/nfl/adp/ppr-overall.php"


def _parse_fp_ecr_page(pos: str) -> list[dict]:
    """Scrapes FantasyPros ECR page for a position and returns ranked player list."""
    slug = FP_POSITION_SLUGS[pos]
    url = f"https://www.fantasypros.com/nfl/rankings/{slug}.php"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # FP embeds ranking data as a JS variable: var ecrData = {...}
    script_text = ""
    for script in soup.find_all("script"):
        if script.string and "ecrData" in script.string:
            script_text = script.string
            break

    if not script_text:
        print(f"    WARNING: Could not find ecrData for {pos}")
        return []

    match = re.search(r"var ecrData\s*=\s*(\{.*?\});", script_text, re.DOTALL)
    if not match:
        print(f"    WARNING: Could not parse ecrData JSON for {pos}")
        return []

    try:
        ecr = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        print(f"    WARNING: JSON parse error for {pos}: {e}")
        return []

    ranked = []
    for i, p in enumerate(ecr.get("players", []), start=1):
        ranked.append({
            "rank": i,
            "fp_id": p.get("player_id"),
            "name": p.get("player_name", ""),
            "team": p.get("player_team_id", ""),
            "position": pos,
            "position_rank": p.get("pos_rank", i),
            "avg_rank": p.get("avg_rank"),
            "best_rank": p.get("best_rank"),
            "worst_rank": p.get("worst_rank"),
            "std_dev": p.get("std_dev"),
            "bye": p.get("player_bye_week"),
            "ecr_vs_adp": p.get("ecr_vs_adp"),
        })
    return ranked


def fetch_fp_rankings() -> dict[str, list]:
    """Returns dict of position -> ranked player list."""
    rankings = {}
    for pos in POSITIONS:
        print(f"  Fetching FantasyPros ECR for {pos}...")
        try:
            rankings[pos] = _parse_fp_ecr_page(pos)
            print(f"    Got {len(rankings[pos])} {pos} rankings.")
        except Exception as e:
            print(f"    ERROR fetching {pos} rankings: {e}")
            rankings[pos] = []
        time.sleep(1.5)  # be polite
    return rankings


def fetch_fp_adp() -> list[dict]:
    """Scrapes FantasyPros PPR overall ADP table."""
    print("  Fetching FantasyPros PPR ADP...")
    resp = session.get(FP_ADP_URL, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", id="data")
    if not table:
        print("    WARNING: ADP table not found.")
        return []

    # Detect column count from header to handle layout changes
    header_cols = table.find("thead").find_all("th") if table.find("thead") else []
    col_names = [th.text.strip().lower() for th in header_cols]

    tbody = table.find("tbody")
    data_rows = tbody.find_all("tr") if tbody else table.find_all("tr")[1:]

    rows = []
    for tr in data_rows:
        cols = tr.find_all("td")
        if not cols:
            continue

        # Column 1 contains "Name TEAM (Bye)" — parse it out
        cell1 = cols[1].text.strip() if len(cols) > 1 else ""
        # Extract player name (before team abbreviation)
        name_tag = cols[1].find("a") if len(cols) > 1 else None
        name = name_tag.text.strip() if name_tag else re.sub(r"\s+[A-Z]{2,3}\s*\(\d+\).*", "", cell1).strip()

        # Position_rank is in col 2, overall ADP in col 3
        pos_rank = cols[2].text.strip() if len(cols) > 2 else ""
        adp_val = cols[3].text.strip() if len(cols) > 3 else ""

        # Extract position from pos_rank (e.g. "RB1" -> "RB")
        pos_match = re.match(r"([A-Z]+)\d*", pos_rank)
        position = pos_match.group(1) if pos_match else ""

        rows.append({
            "adp_rank": len(rows) + 1,
            "name": name,
            "position": position,
            "adp": adp_val,
            # Per-site ADP columns are present only in expanded views
            "espn_adp": cols[4].text.strip() if len(cols) > 4 else None,
            "yahoo_adp": cols[5].text.strip() if len(cols) > 5 else None,
            "nfl_adp": cols[6].text.strip() if len(cols) > 6 else None,
            "cbs_adp": cols[7].text.strip() if len(cols) > 7 else None,
            "sleeper_adp": cols[8].text.strip() if len(cols) > 8 else None,
        })
    print(f"    Got {len(rows)} ADP entries.")
    return rows


# ---------------------------------------------------------------------------
# The Fantasy Footballers — free draft rankings (Playwright)
# ---------------------------------------------------------------------------

TFF_POSITION_URLS = {
    "QB": "https://www.thefantasyfootballers.com/2026-quarterback-rankings-draft/",
    "RB": "https://www.thefantasyfootballers.com/2026-running-back-rankings-draft/",
    "WR": "https://www.thefantasyfootballers.com/2026-wide-receiver-rankings-draft/",
    "TE": "https://www.thefantasyfootballers.com/2026-tight-end-rankings-draft/",
}


def fetch_tff_rankings() -> dict[str, list]:
    """Scrapes TFF free draft rankings via Playwright (JS-rendered table)."""
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        for pos, url in TFF_POSITION_URLS.items():
            print(f"  Fetching TFF rankings for {pos}...")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(4000)

                rows = page.query_selector_all("table tbody tr")
                ranked = []
                for row in rows:
                    name_el  = row.query_selector(".player-name a")
                    rank_el  = row.query_selector("td.rank--cons")
                    meta_el  = row.query_selector(".player-right-line-two")
                    if not name_el or not rank_el:
                        continue

                    name = name_el.inner_text().strip()
                    try:
                        rank = int(rank_el.inner_text().strip())
                    except ValueError:
                        continue

                    team_bye = meta_el.inner_text().strip() if meta_el else ""
                    m = re.match(r"([A-Z]+)\s*\((\d+)\)", team_bye)
                    team = m.group(1) if m else ""
                    bye  = m.group(2) if m else ""

                    ranked.append({"rank": rank, "name": name, "team": team, "bye": bye})

                results[pos] = ranked
                print(f"    Got {len(ranked)} {pos} rankings from TFF.")
            except Exception as e:
                print(f"    ERROR fetching TFF {pos}: {e}")
                results[pos] = []

        browser.close()
    return results


# ---------------------------------------------------------------------------
# ESPN NFL News API
# ---------------------------------------------------------------------------

ESPN_NEWS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news"


def fetch_espn_news() -> list[dict]:
    """Fetches latest NFL news from ESPN's public API (no auth required)."""
    print("  Fetching ESPN NFL news...")
    params = {"limit": 100}
    resp = session.get(ESPN_NEWS_URL, params=params, timeout=30)
    resp.raise_for_status()

    data = resp.json()
    articles = []
    for item in data.get("articles", []):
        # Extract athlete names from categories
        athlete_names = [
            c.get("description", "")
            for c in item.get("categories", [])
            if c.get("type") == "athlete" and c.get("description")
        ]
        # Extract teams
        team_names = [
            c.get("description", "")
            for c in item.get("categories", [])
            if c.get("type") == "team" and c.get("description")
        ]

        articles.append({
            "source": "espn",
            "headline": item.get("headline", ""),
            "description": item.get("description", ""),
            "story": item.get("story", "")[:500] if item.get("story") else "",
            "url": item.get("links", {}).get("web", {}).get("href", ""),
            "timestamp": item.get("published", ""),
            "player_names": athlete_names,
            "player_name": athlete_names[0] if athlete_names else "",
            "teams": team_names,
            "type": item.get("type", ""),
        })

    print(f"    Got {len(articles)} ESPN news items.")
    return articles


# ---------------------------------------------------------------------------
# Merge & build output JSON
# ---------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """Lowercase, strip suffixes, collapse whitespace for fuzzy matching."""
    name = name.lower().strip()
    for suffix in [" jr.", " sr.", " ii", " iii", " iv"]:
        name = name.replace(suffix, "")
    return re.sub(r"\s+", " ", name)


def build_player_index(sleeper_players: dict) -> dict:
    """Build a name -> sleeper_id lookup for merging."""
    index = {}
    for pid, p in sleeper_players.items():
        key = normalize_name(p["name"])
        index[key] = pid
    return index


def merge_news(espn_news: list, name_index: dict) -> dict:
    """Returns player_id -> list of news items, matched by athlete name."""
    news_by_player = {}

    for item in espn_news:
        # An article may mention multiple players — attribute to each
        for raw_name in (item.get("player_names") or [item.get("player_name", "")]):
            if not raw_name:
                continue
            key = normalize_name(raw_name)
            pid = name_index.get(key)
            if not pid:
                continue
            news_by_player.setdefault(pid, []).append(item)

    return news_by_player


def load_prev_ranks(pos: str) -> dict:
    """Load name -> rank from existing position JSON, for computing rank_change."""
    path = DATA_DIR / f"{pos.lower()}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return {normalize_name(p["name"]): p["rank"] for p in data if "name" in p and "rank" in p}
    except Exception:
        return {}


def build_rankings_output(
    rankings: dict[str, list],
    adp_list: list,
    sleeper_players: dict,
    news_by_player: dict,
    name_index: dict,
) -> dict[str, list]:
    """Merges all data sources into position-keyed ranked player lists."""

    # Build ADP lookup by normalized name
    adp_lookup = {}
    for entry in adp_list:
        key = normalize_name(entry["name"])
        adp_lookup[key] = entry

    output = {}
    for pos, ranked in rankings.items():
        pos_list = []
        for player in ranked:
            fp_name_key = normalize_name(player["name"])
            sleeper_id = name_index.get(fp_name_key)
            sleeper = sleeper_players.get(sleeper_id, {}) if sleeper_id else {}
            adp = adp_lookup.get(fp_name_key, {})
            news = news_by_player.get(sleeper_id, [])[:5] if sleeper_id else []

            pos_list.append({
                "rank": player["rank"],
                "name": player["name"],
                "team": sleeper.get("team") or player.get("team", ""),
                "position": pos,
                "position_rank": player.get("position_rank"),
                "bye": player.get("bye"),
                "headshot_url": sleeper.get("headshot_url", ""),
                "injury_status": sleeper.get("injury_status", ""),
                "player_status": sleeper.get("status", ""),
                "ecr": {
                    "avg": player.get("avg_rank"),
                    "best": player.get("best_rank"),
                    "worst": player.get("worst_rank"),
                    "std_dev": player.get("std_dev"),
                    "ecr_vs_adp": player.get("ecr_vs_adp"),
                },
                "adp": {
                    "overall": adp.get("adp"),
                    "espn": adp.get("espn_adp"),
                    "yahoo": adp.get("yahoo_adp"),
                    "nfl": adp.get("nfl_adp"),
                    "cbs": adp.get("cbs_adp"),
                    "sleeper": adp.get("sleeper_adp"),
                },
                "news": news,
                "sleeper_id": sleeper_id,
                "fp_id": player.get("fp_id"),
            })
        output[pos] = pos_list

    return output


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"\n=== Fantasy Football Data Fetch — {datetime.now(timezone.utc).isoformat()} ===\n")

    # 1. Sleeper player database
    sleeper_players = fetch_sleeper_players()
    name_index = build_player_index(sleeper_players)

    # 2. FantasyPros rankings
    rankings = fetch_fp_rankings()

    # 3. FantasyPros ADP
    adp_list = fetch_fp_adp()

    # 4. News
    espn_news = fetch_espn_news()
    news_by_player = merge_news(espn_news, name_index)

    # 5. TFF rankings
    tff_rankings = fetch_tff_rankings()
    tff_lookup = {
        pos: {normalize_name(p["name"]): p["rank"] for p in players}
        for pos, players in tff_rankings.items()
    }

    # 6. Merge everything
    print("\n  Merging data sources...")
    merged = build_rankings_output(rankings, adp_list, sleeper_players, news_by_player, name_index)

    # Add TFF rank to each player
    for pos, players in merged.items():
        pos_tff = tff_lookup.get(pos, {})
        for player in players:
            player["tff_rank"] = pos_tff.get(normalize_name(player["name"]))

    # 5b. Compute rank_change vs previous data
    for pos, players in merged.items():
        prev_ranks = load_prev_ranks(pos)
        for player in players:
            prev = prev_ranks.get(normalize_name(player["name"]))
            player["rank_change"] = (prev - player["rank"]) if prev is not None else None

    # 7. Write output files
    meta = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "positions": POSITIONS,
        "player_counts": {pos: len(players) for pos, players in merged.items()},
    }

    (DATA_DIR / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"  Wrote data/meta.json")

    for pos, players in merged.items():
        out_path = DATA_DIR / f"{pos.lower()}.json"
        out_path.write_text(json.dumps(players, indent=2))
        print(f"  Wrote data/{pos.lower()}.json  ({len(players)} players)")

    # Also write full news feed
    (DATA_DIR / "news.json").write_text(json.dumps(espn_news[:200], indent=2))
    print(f"  Wrote data/news.json  ({min(len(espn_news), 200)} items)")

    print("\n=== Done ===\n")


if __name__ == "__main__":
    main()
