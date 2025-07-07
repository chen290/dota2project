import requests
import time
import json
import os
import tempfile
import logging
import numpy as np
from collections import defaultdict
from datetime import datetime, timezone
import statistics
from enum import Enum
from typing import Optional, Any, Dict, List, Tuple
import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('dota_analysis.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

ACCOUNT_IDS: Tuple[int, ...] = (302004172, 129213402, 138951493, 285975878)
SECONDS_PER_YEAR = 31_536_000
SECONDS_PER_6_MONTHS = SECONDS_PER_YEAR // 2
SECONDS_PER_3_MONTHS = SECONDS_PER_6_MONTHS // 2
SECONDS_PER_MONTH = SECONDS_PER_YEAR // 12


class Team(Enum):
    RADIANT = 0
    DIRE = 1


class Dota2Cache:
    def __init__(
        self, cache_file: str = "C:/Users/Hanyu/dota2project/dota2/cache.json"
    ) -> None:
        self.cache_file: str = cache_file
        self.unsaved_count: int = 0
        self.cache_timestamps: Dict[str, float] = {}
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        self.cache: Dict[str, Any] = self._load_cache()

    def _load_cache(self) -> Dict[str, Any]:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    data = json.load(f)
                    # Load both cache data and timestamps
                    if isinstance(data, dict) and "cache" in data and "timestamps" in data:
                        self.cache_timestamps = data["timestamps"]
                        return data["cache"]
                    else:
                        # Legacy format - just cache data
                        return data
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load cache ({e}), creating new cache.")
                return {}
        logger.info("Cache not found, creating new cache")
        return {}

    def get(self, url: str, use_cache: bool = True) -> Any:
        current_time = time.time()
        cache_expiry = 3600  # 1 hour in seconds
        
        # Check if we should use cache (either use_cache=True or data is fresh)
        if url in self.cache:
            if use_cache or (url in self.cache_timestamps and 
                           current_time - self.cache_timestamps[url] < cache_expiry):
                return self.cache[url]
        
        logger.debug(f"Fetching {url}")
        while True:
            response = requests.get(url)
            if response.status_code in (429, 500):
                time.sleep(10)
                continue
            response.raise_for_status()
            break

        data = response.json()
        self.cache[url] = data
        self.cache_timestamps[url] = current_time
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
                # Save both cache data and timestamps
                json.dump({
                    "cache": self.cache,
                    "timestamps": self.cache_timestamps
                }, f)
            os.replace(temp_path, self.cache_file)
            logger.debug("Cache saved successfully.")
        except Exception as e:
            logger.error(f"Error saving cache: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
        self.unsaved_count = 0

logger.info("Loading cache")
g_cache = Dota2Cache()


def fetch_hero_id_name_mapping() -> Dict[int, str]:
    data = g_cache.get("https://api.opendota.com/api/heroStats")
    g_cache.flush()
    return {hero["id"]: hero["localized_name"] for hero in data}


def fetch_player_name(account_id: int) -> str:
    """Fetch player name from OpenDota API"""
    try:
        data = g_cache.get(f"https://api.opendota.com/api/players/{account_id}")
        g_cache.flush()
        return data.get("profile", {}).get("personaname", f"Unknown Player ({account_id})")
    except Exception as e:
        logger.error(f"Error fetching player name for {account_id}: {e}")
        return f"Unknown Player ({account_id})"


g_id_name_map: Dict[int, str] = fetch_hero_id_name_mapping()
g_player_name_cache: Dict[int, str] = {}


def get_player_name(account_id: int) -> str:
    """Get player name with caching"""
    if account_id not in g_player_name_cache:
        g_player_name_cache[account_id] = fetch_player_name(account_id)
    return g_player_name_cache[account_id]


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

    def get_name(self) -> str:
        """Get player name from account ID"""
        account_id = self.try_get_id()
        if account_id:
            return get_player_name(account_id)
        return "Anonymous Player"


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
            if self.full_match_json and "players" in self.full_match_json:
                self.players = [Player(pj) for pj in self.full_match_json["players"]]
            else:
                self.players = []

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
    def __init__(self, account_id: int, cancellation_check: Optional[Any] = None, progress_callback=None) -> None:
        self.account_id: int = account_id
        self.cancellation_check: Optional[Any] = cancellation_check
        self.progress_callback = progress_callback
        self.matches: List[Match] = [
            Match(match_json)
            for match_json in g_cache.get(
                f"https://api.opendota.com/api/players/{account_id}/matches",
                False
            )
        ]
        g_cache.flush()

    def _check_cancellation(self) -> bool:
        if self.cancellation_check:
            return self.cancellation_check()
        return False

    def _raise_if_cancelled(self) -> None:
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
        self, hero_id: Optional[int], seconds_ago: int = SECONDS_PER_YEAR
    ) -> pd.DataFrame:
        matches = self.get_matches(hero_id, seconds_ago)
        if not matches:
            return pd.DataFrame(columns=['Hero Name', 'GPM', 'Matches', 'Wins', 'Win Rate'])
        
        # Pre-allocate data structures for better performance
        all_enemy_hero_ids = []
        all_gpms = []
        all_wins = []
        
        for i, match in enumerate(matches):
            # Update progress every match
            if self.progress_callback:
                self.progress_callback(i + 1, len(matches))
                
            if i % 10 == 0:
                self._raise_if_cancelled()
                
            players = match.get_players()
            # Find self player info in one pass
            self_team = None
            self_gpm = None
            for player in players:
                if player.try_get_id() == self.account_id:
                    self_team = player.get_team()
                    self_gpm = player.get_gpm()
                    break
            
            if self_team is None or self_gpm is None:
                continue
                
            # Process enemy players
            for player in players:
                if player.get_team() != self_team:
                    enemy_hero_id = player.get_hero_id()
                    is_win = match.get_winner_team() == self_team
                    
                    all_enemy_hero_ids.append(enemy_hero_id)
                    all_gpms.append(self_gpm)
                    all_wins.append(1 if is_win else 0)
            
            logger.debug(f"Processing match {match.get_id()}")

        # Final progress update
        if self.progress_callback:
            self.progress_callback(len(matches), len(matches))

        if not all_enemy_hero_ids:
            return pd.DataFrame(columns=['Hero Name', 'GPM', 'Matches', 'Wins', 'Win Rate'])
        
        # Convert to NumPy arrays for efficient computation
        hero_ids_array = np.array(all_enemy_hero_ids)
        gpms_array = np.array(all_gpms)
        wins_array = np.array(all_wins)
        
        # Get unique hero IDs
        unique_hero_ids = np.unique(hero_ids_array)
        
        records = []
        for hero_id in unique_hero_ids:
            # Skip None values
            if hero_id is None:
                continue
                
            # Use NumPy boolean indexing for efficient filtering
            mask = hero_ids_array == hero_id
            hero_gpms = gpms_array[mask]
            hero_wins = wins_array[mask]
            
            count = len(hero_gpms)
            wins = int(np.sum(hero_wins))
            gpm_avg = float(np.mean(hero_gpms))
            win_rate = (wins / count * 100) if count > 0 else 0
            
            hero_name = g_id_name_map.get(int(hero_id), f"Unknown Hero ({int(hero_id)})")
            
            records.append({
                'Hero Name': hero_name,
                'Wins': wins,
                'Matches': count,
                'Win Rate': round(win_rate, 2),
                'GPM': round(gpm_avg, 2),
            })
        
        return pd.DataFrame(records)

    def get_play_with_matches(self, player_id: int, same_team: bool) -> List[Tuple[Match, Match]]:
        others_matches = Matches(player_id).get_matches()
        result = []
        
        # Create a dictionary for O(1) lookup instead of O(n) search
        other_matches_dict = {}
        for other_match in others_matches:
            other_matches_dict[other_match.get_id()] = other_match
        
        for match in self.get_matches():
            match_id = match.get_id()
            if match_id in other_matches_dict:
                other_match = other_matches_dict[match_id]
                if (match.get_team() == other_match.get_team()) == same_team:
                    result.append((match, other_match))
        
        return result

    def get_stats_per_player(self, player_id: int, seconds_ago: int = SECONDS_PER_YEAR) -> pd.DataFrame:
        """Get statistics for playing with/against a specific player"""
        # Get player name
        player_name = get_player_name(player_id)
        
        # Get all matches for both players
        others_matches = Matches(player_id).get_matches()
        my_matches = self.get_matches()
        
        # Create lookup dictionary for other player's matches
        other_matches_dict = {match.get_id(): match for match in others_matches}
        
        teammate_wins = 0
        teammate_count = 0
        opponent_wins = 0
        opponent_count = 0
        teammate_match_ids = []
        opponent_match_ids = []
        
        for match in my_matches:
            match_id = match.get_id()
            if match_id in other_matches_dict:
                other_match = other_matches_dict[match_id]
                is_teammate = match.get_team() == other_match.get_team()
                is_win = match.get_winner_team() == match.get_team()
                
                if is_teammate:
                    teammate_count += 1
                    teammate_match_ids.append(match_id)
                    if is_win:
                        teammate_wins += 1
                else:
                    opponent_count += 1
                    opponent_match_ids.append(match_id)
                    if is_win:
                        opponent_wins += 1
        
        teammate_winrate = (teammate_wins / teammate_count * 100) if teammate_count > 0 else 0
        opponent_winrate = (opponent_wins / opponent_count * 100) if opponent_count > 0 else 0
        
        records = [
            {
                'Player': player_name,
                'Role': 'Teammate',
                'Wins': teammate_wins,
                'Matches': teammate_count,
                'Win Rate': round(teammate_winrate, 2),
                'Match IDs': '<br>'.join(map(str, teammate_match_ids)) if teammate_match_ids else 'None'
            },
            {
                'Player': player_name,
                'Role': 'Opponent',
                'Wins': opponent_wins,
                'Matches': opponent_count,
                'Win Rate': round(opponent_winrate, 2),
                'Match IDs': '<br>'.join(map(str, opponent_match_ids)) if opponent_match_ids else 'None'
            }
        ]
        
        return pd.DataFrame(records)


if __name__ == "__main__":
    import requests

    match_id = 8359187117
    response = requests.post(f"https://api.opendota.com/api/request/{match_id}")

    if response.status_code == 200:
        logger.info("Parse requested successfully.")
    else:
        logger.error(f"Failed to request parse: {response.status_code}, {response.text}")
