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
        self, cache_file: str = "C:/Users/Hanyu/dota2project/dota2/cache.json"
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
        print("cache not found, creating new cache")
        return {}

    def get(self, url: str, use_cache: bool = True) -> Any:
        if url in self.cache and use_cache:
            return self.cache[url]
        print("fetching", url)
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

print("loading cache")
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
        self.players: List[Player] = []

    def __repr__(self) -> str:
        return str(self.simple_match_json["match_id"])

    def _set_full_match_json(self) -> None:
        if not self.full_match_json:
            match_id = self.get_id()
            self.full_match_json = g_cache.get(
                f"https://api.opendota.com/api/matches/{match_id}"
            )
            self.players = [Player(pj) for pj in self.full_match_json["players"]]

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

    def get_players(self) -> List[Player]:
        self._set_full_match_json()
        return self.players

    def get_player(self, player_id: int) -> Optional[Player]:
        self._set_full_match_json()
        return next((p for p in self.players if p.try_get_id() == player_id), None)


class Matches:
    def __init__(self, account_id: int, cancellation_check: Optional[Any] = None) -> None:
        self.account_id: int = account_id
        self.cancellation_check: Optional[Any] = cancellation_check
        self.matches: List[Match] = [
            Match(match_json)
            for match_json in g_cache.get(
                f"https://api.opendota.com/api/players/{account_id}/matches",
                False
            )
        ]
        g_cache.flush()

    def _check_cancellation(self) -> bool:
        print("checking cancellation 2")
        if self.cancellation_check:
            print("checking cancellation 3")
            print(self.cancellation_check())
            return self.cancellation_check()
        return False

    def _raise_if_cancelled(self) -> None:
        print("checking cancellation")
        if self._check_cancellation():
            raise InterruptedError("Request cancelled by user")

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

    def get_stats_per_enemy_hero(
        self, hero_id: int, seconds_ago: int = SECONDS_PER_YEAR
    ) -> pd.DataFrame:
        matches = self.get_matches(hero_id, seconds_ago)
        hero_to_gpm_list: Dict[int, List[int]] = defaultdict(list)
        hero_to_count_list: Dict[int, int] = defaultdict(int)
        hero_to_win_count_list: Dict[int, int] = defaultdict(int)
        
        for i, match in enumerate(matches):
            if i % 10 == 0:
                self._raise_if_cancelled()
                
            players = match.get_players()
            self_team = next(
                (p.get_team() for p in players if p.try_get_id() == self.account_id),
                None,
            )
            self_gpm = next(
                (p.get_gpm() for p in players if p.try_get_id() == self.account_id),
                None,
            )

            for player in players:
                if player.get_team() != self_team and self_gpm:
                    hero_to_gpm_list[player.get_hero_id()].append(self_gpm)
                    hero_to_count_list[player.get_hero_id()] += 1
                    hero_to_win_count_list[player.get_hero_id()] += 1 if match.get_winner_team() == self_team else 0

        if not hero_to_gpm_list:
            return pd.DataFrame(columns=['Hero Name', 'GPM', 'Matches', 'Wins', 'Win Rate'])
        
        records = []
        for hero_id, gpms in hero_to_gpm_list.items():
            hero_name = g_id_name_map.get(hero_id, f"Unknown Hero ({hero_id})")
            win_rate = (hero_to_win_count_list[hero_id] / hero_to_count_list[hero_id] * 100) if hero_to_count_list[hero_id] > 0 else 0
            
            records.append({
                'Hero Name': hero_name,
                'Wins': hero_to_win_count_list[hero_id],
                'Matches': hero_to_count_list[hero_id],
                'Win Rate': round(win_rate, 2),
                'GPM': round(statistics.mean(gpms), 2),
            })
        
        df = pd.DataFrame(records)
        if not df.empty:
            df = df.sort_values('Matches', ascending=False).reset_index(drop=True)
        
        return df

    def get_play_with_matches(self, player_id: int, same_team: bool) -> List[Tuple[Match, Match]]:
        others_matches = Matches(player_id).get_matches()
        result = []
        
        # Since match IDs are sorted in descending order, we can use binary search
        # or early termination for better performance
        other_match_ids = {match.get_id() for match in others_matches}
        
        for match in self.get_matches():
            if match.get_id() in other_match_ids:
                for other_match in others_matches:
                    if match.get_id() == other_match.get_id():
                        if (match.get_team() == other_match.get_team()) == same_team:
                            result.append((match, other_match))
                        break  # Found the match, no need to continue searching
                    elif match.get_id() > other_match.get_id():
                        # Since IDs are sorted in descending order, we can stop searching
                        break
        print(result)
        return result

    def get_stats_per_player(self, player_id: int, seconds_ago: int = SECONDS_PER_YEAR) -> pd.DataFrame:
        """Get statistics for playing with/against a specific player"""
        teammate_matches = self.get_play_with_matches(player_id, True)
        opponent_matches = self.get_play_with_matches(player_id, False)
        
        teammate_count = len(teammate_matches)
        teammate_wins = sum(1 for match, _ in teammate_matches if match.get_winner_team() == match.get_team())
        teammate_winrate = (teammate_wins / teammate_count * 100) if teammate_count > 0 else 0
        
        opponent_count = len(opponent_matches)
        opponent_wins = sum(1 for match, _ in opponent_matches if match.get_winner_team() == match.get_team())
        opponent_winrate = (opponent_wins / opponent_count * 100) if opponent_count > 0 else 0
        
        records = [
            {
                'Role': 'Teammate',
                'Wins': teammate_wins,
                'Matches': teammate_count,
                'Win Rate': round(teammate_winrate, 2)
            },
            {
                'Role': 'Opponent',
                'Wins': opponent_wins,
                'Matches': opponent_count,
                'Win Rate': round(opponent_winrate, 2)
            }
        ]
        
        df = pd.DataFrame(records)
        return df



if __name__ == "__main__":
    # Example usage of generate_table function
    # selected_player = ACCOUNT_IDS[0]
    # matches = Matches(selected_player)
    # stats = matches.get_stats_per_enemy_hero(1)  # Example hero_id
    # df = generate_table(stats)
    # print(df)
    
    # g_cache.flush()
    import requests

    match_id = 8359187117
    response = requests.post(f"https://api.opendota.com/api/request/{match_id}")

    if response.status_code == 200:
        print("Parse requested successfully.")
    else:
        print(f"Failed to request parse: {response.status_code}, {response.text}")
