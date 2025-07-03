import requests
import time
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
import statistics
from enum import Enum
from typing import Optional, Any, Dict, List, Tuple
import pandas as pd

ACCOUNT_IDS: Tuple[int, ...] = (302004172, 129213402, 138951493, 285975878)
SECONDS_PER_YEAR = 31_536_000
SECONDS_PER_6_MONTHS = SECONDS_PER_YEAR // 2
SECONDS_PER_3_MONTHS = SECONDS_PER_6_MONTHS // 2
SECONDS_PER_MONTH = SECONDS_PER_6_MONTHS // 3


class Team(Enum):
    RADIANT = 0
    DIRE = 1


class Dota2Cache:
    def __init__(
        self, cache_file: str = "./dota2/cache.json"
    ) -> None:
        self.cache_file: str = cache_file
        self.unsaved_count: int = 0
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        self.cache: Dict[str, Any] = self._load_cache()

    def _load_cache(self) -> Dict[str, Any]:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: Failed to load cache ({e}), exiting.")
                exit()
        return {}

    def get(self, url: str) -> Any:
        if url in self.cache:
            return self.cache[url]
        print("fetch")
        while True:
            response = requests.get(url)
            if response.status_code in (429, 500):
                time.sleep(10)
                continue
            response.raise_for_status()
            break

        data = response.json()
        self.cache[url] = data
        self.unsaved_count += 1
        if self.unsaved_count % 50 == 0:
            self.flush()
        return data

    def flush(self) -> None:
        if not self.unsaved_count:
            return

        temp_fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(self.cache_file))
        try:
            with os.fdopen(temp_fd, "w") as f:
                json.dump(self.cache, f)
            os.replace(temp_path, self.cache_file)
            print("Cache saved successfully.")
        except Exception as e:
            print(f"Error saving cache: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
        self.unsaved_count = 0


g_cache = Dota2Cache()


def fetch_hero_id_name_mapping() -> Dict[int, str]:
    data = g_cache.get("https://api.opendota.com/api/heroStats")
    g_cache.flush()
    return {hero["id"]: hero["localized_name"] for hero in data}


g_id_name_map: Dict[int, str] = fetch_hero_id_name_mapping()


class Player:
    def __init__(self, player_json: Dict[str, Any]) -> None:
        self.player_json: Dict[str, Any] = player_json

    def try_get_id(self) -> Optional[int]:
        return self.player_json.get("account_id")

    def get_team(self) -> Team:
        return Team.RADIANT if self.player_json["team_number"] == 0 else Team.DIRE

    def get_hero_id(self) -> int:
        return self.player_json["hero_id"]

    def get_gpm(self) -> int:
        return self.player_json["gold_per_min"]


class Match:
    def __init__(self, match_json: Dict[str, Any]) -> None:
        self.simple_match_json: Dict[str, Any] = match_json
        self.full_match_json: Optional[Dict[str, Any]] = None
        self.players: Tuple[Player] = ()

    def __repr__(self) -> str:
        return str(self.simple_match_json["match_id"])

    def _set_full_match_json(self) -> None:
        if not self.full_match_json:
            match_id = self.get_id()
            self.full_match_json = g_cache.get(
                f"https://api.opendota.com/api/matches/{match_id}"
            )
            self.players = (Player(pj) for pj in self.full_match_json["players"])

    def get_player_hero_id(self) -> int:
        return self.simple_match_json["hero_id"]

    def get_start_time(self) -> int:
        return self.simple_match_json["start_time"]

    def get_id(self) -> int:
        return self.simple_match_json["match_id"]

    def get_team(self) -> Team:
        return (
            Team.RADIANT if self.simple_match_json["player_slot"] < 128 else Team.DIRE
        )

    def get_winner_team(self) -> Team:
        return Team.RADIANT if self.simple_match_json["radiant_win"] else Team.DIRE

    def get_players(self) -> Tuple[Player, ...]:
        self._set_full_match_json()
        return tuple(self.players)

    def get_player(self, player_id: int) -> Optional[Player]:
        self._set_full_match_json()
        return next((p for p in self.players if p.try_get_id() == player_id), None)


class Matches:
    def __init__(self, account_id: int) -> None:
        self.account_id: int = account_id
        self.matches: List[Match] = [
            Match(match_json)
            for match_json in g_cache.get(
                f"https://api.opendota.com/api/players/{account_id}/matches"
            )
        ]
        g_cache.flush()

    def get_matches(
        self, hero_id: Optional[int] = None, seconds_ago: float = float("inf")
    ) -> List[Match]:
        now = int(datetime.now(timezone.utc).timestamp())
        return [
            m
            for m in self.matches
            if (hero_id is None or m.get_player_hero_id() == hero_id)
            and now - m.get_start_time() <= seconds_ago
        ]

    def get_average_gpm_per_enemy_hero(
        self, hero_id: int, seconds_ago: int = SECONDS_PER_YEAR
    ) -> Dict[int, float]:
        matches = self.get_matches(hero_id, seconds_ago)
        hero_to_gpm_list: Dict[int, List[int]] = defaultdict(list)

        for match in matches:
            players = match.get_players()
            self_team = next(
                (p.get_team() for p in players if p.try_get_id() == self.account_id),
                None,
            )

            for player in players:
                if player.get_team() != self_team:
                    hero_to_gpm_list[player.get_hero_id()].append(player.get_gpm())

        return {
            hero_id: statistics.mean(gpms) for hero_id, gpms in hero_to_gpm_list.items()
        }

    def get_play_with_matches(self, player_id: int) -> List[Match]:
        self_match_ids = {m.get_id() for m in self.get_matches()}
        other_matches = Matches(player_id).get_matches()
        return [m for m in other_matches if m.get_id() in self_match_ids]

def generate_table(d):
    if not d:
        return pd.DataFrame()

    # Ensure all values are lists of the same length
    keys = list(d.keys())
    values = list(d.values())

    row_count = len(values[0])

    data = []
    for i in range(row_count):
        row = {key: d[key][i] for key in keys}
        data.append(row)

    df = pd.DataFrame(data)

    if not df.empty:
        first_key = next(iter(d))
        df = df.sort_values(by=first_key, ascending=False).reset_index(drop=True)

    return df


if __name__ == "__main__":
    selected_player = ACCOUNT_IDS[0]
    matches = Matches(selected_player)
    print(matches.get_play_with_matches(112772595))
    g_cache.flush()
