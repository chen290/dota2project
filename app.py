# app.py
from flask import Flask, render_template, request, jsonify
import dota_analysis
import threading
import webbrowser
import time
import os
import pandas as pd
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global variable to track cancellation state
cancellation_event = threading.Event()

# Global variable to track progress
progress_data = {"current": 0, "total": 0, "percentage": 0}

@app.route("/")
def index():
    return render_template("index.html")

def check_cancellation():
    """Check if the current request should be cancelled"""
    return cancellation_event.is_set()

def update_progress(current, total):
    """Update progress data"""
    global progress_data
    progress_data["current"] = current
    progress_data["total"] = total
    progress_data["percentage"] = int((current / total * 100) if total > 0 else 0)

def process_hero_mode(player_id, hero_name, seconds_ago, duration):
    """Process hero mode request"""
    if not player_id:
        return jsonify({"html": "<b>Please provide a valid player ID.</b>"})
    
    try:
        # Get matches first to know the total count
        if hero_name == "All Hero":
            hero_filter = None
        else:
            try:
                selected_hero_id = next(k for k, v in dota_analysis.g_id_name_map.items() if v == hero_name)
            except StopIteration:
                return jsonify({"html": "<b>Invalid hero name selected.</b>"})
            hero_filter = selected_hero_id
        
        # Create matches object with progress callback
        matches = dota_analysis.Matches(int(player_id), check_cancellation, update_progress)
        
        # Get filtered matches to know total count
        filtered_matches = matches.get_matches(hero_filter, seconds_ago)
        
        # Now set progress with actual total
        update_progress(0, len(filtered_matches))
        
        if hero_name == "All Hero":
            # Get stats for all heroes
            result_df: pd.DataFrame = matches.get_stats_per_enemy_hero(None, seconds_ago)
        else:
            # Get stats for specific hero
            result_df: pd.DataFrame = matches.get_stats_per_enemy_hero(selected_hero_id, seconds_ago)
        
        if result_df.empty:
            html = f"<b>No data available for hero {hero_name} in the selected time period.</b>"
        else:
            html_table = result_df.to_html(classes='display', index=False, border=0)
            total_matches = matches.get_matches(hero_filter, seconds_ago)
            total_wins = sum(1 for match in total_matches if match.get_winner_team() == match.get_team())
            html = f"""
            <h3>Total Matches: {len(total_matches)} | Total Wins: {total_wins} | Win Rate: {total_wins / len(total_matches) * 100:.2f}%</h3>
            {html_table}
            """
        return jsonify({"html": html})
    except Exception as e:
        if check_cancellation():
            return jsonify({"html": "<b>Request cancelled.</b>"})
        else:
            return jsonify({"html": f"<b>Error processing request: {str(e)}</b>"})
    
    # Fallback return (should never be reached)
    return jsonify({"html": "<b>Unexpected error occurred.</b>"})

def process_player_mode(player_id, other_player_id, seconds_ago, duration):
    """Process player mode request"""
    try:
        matches = dota_analysis.Matches(int(player_id), check_cancellation)
        df = matches.get_stats_per_player(int(other_player_id), seconds_ago)
        if df.empty:
            html = f"<b>No data available for player {other_player_id} in the selected time period.</b>"
        else:
            html_table = df.to_html(classes='display', index=False, border=0, escape=False)
            # Get player names for display
            player_name = dota_analysis.get_player_name(int(player_id))
            other_player_name = dota_analysis.get_player_name(int(other_player_id))
            html = f"""
            <h3>Match History: {player_name} vs {other_player_name}</h3>
            {html_table}
            """
        return jsonify({"html": html})
    except Exception as e:
        if check_cancellation():
            return jsonify({"html": "<b>Request cancelled.</b>"})
        else:
            return jsonify({"html": f"<b>Error processing request: {str(e)}</b>"})
    
    # Fallback return (should never be reached)
    return jsonify({"html": "<b>Unexpected error occurred.</b>"})

@app.route("/call_function", methods=["POST"])
def call_function():
    # Reset cancellation event for new request
    cancellation_event.clear()
    
    mode = request.form.get("mode")
    player_id = request.form.get("player_id", "302004172")  # Default to 302004172 if not provided
    hero_name = request.form.get("hero_name", None)
    duration = request.form.get("duration", "6_months")
    other_player_id = request.form.get("other_player_id")

    # Convert duration string to seconds
    duration_map = {
        "1_month": dota_analysis.SECONDS_PER_MONTH,
        "3_months": dota_analysis.SECONDS_PER_3_MONTHS,
        "6_months": dota_analysis.SECONDS_PER_6_MONTHS,
        "1_year": dota_analysis.SECONDS_PER_YEAR,
        "all_time": float("inf")
    }
    seconds_ago = duration_map.get(duration, dota_analysis.SECONDS_PER_YEAR)

    if mode == "Hero":
        return process_hero_mode(player_id, hero_name, seconds_ago, duration)
    else:
        logger.debug(f"Player mode request: {player_id}, {other_player_id}, {seconds_ago}, {duration}")
        return process_player_mode(player_id, other_player_id, seconds_ago, duration)


@app.route("/cancel_request", methods=["POST"])
def cancel_request():
    """Endpoint to cancel the current request"""
    cancellation_event.set()
    return jsonify({"status": "cancelled"})

@app.route("/check_cancellation_status", methods=["GET"])
def check_cancellation_status():
    """Debug endpoint to check cancellation event status"""
    return jsonify({"cancelled": cancellation_event.is_set()})

@app.route("/reset_cancellation", methods=["POST"])
def reset_cancellation():
    """Debug endpoint to manually reset cancellation event"""
    cancellation_event.clear()
    return jsonify({"status": "reset"})

@app.route("/get_heroes", methods=["GET"])
def get_heroes():
    """Get all hero names for the dropdown"""
    heroes = [{"name": name} for name in sorted(dota_analysis.g_id_name_map.values())]
    return jsonify({"heroes": heroes})

@app.route("/get_players", methods=["GET"])
def get_players():
    """Get all player names for the dropdown"""
    players = []
    for account_id in dota_analysis.ACCOUNT_IDS:
        try:
            name = dota_analysis.get_player_name(account_id)
            players.append({"id": account_id, "name": name})
        except Exception as e:
            logger.warning(f"Error getting name for player {account_id}: {e}")
            players.append({"id": account_id, "name": f"Unknown Player ({account_id})"})
    return jsonify({"players": players})

@app.route("/get_player_name/<int:account_id>", methods=["GET"])
def get_player_name(account_id):
    """Get a single player's name"""
    try:
        name = dota_analysis.get_player_name(account_id)
        return jsonify({"name": name})
    except Exception as e:
        logger.warning(f"Error getting name for player {account_id}: {e}")
        return jsonify({"name": f"Unknown Player ({account_id})"})

@app.route("/get_progress", methods=["GET"])
def get_progress():
    """Get current progress data"""
    global progress_data
    return jsonify(progress_data)

def open_browser():
    time.sleep(1)  # wait briefly to ensure server starts
    webbrowser.open("http://localhost:5000")

if __name__ == "__main__":
    app.run(debug=True)
