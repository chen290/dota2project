# app.py
from flask import Flask, render_template, request, jsonify
import dota_analysis
import threading
import webbrowser
import time
import os
import pandas as pd

app = Flask(__name__)

# Global variable to track cancellation state
cancellation_event = threading.Event()

@app.route("/")
def index():
    return render_template("index.html")

def check_cancellation():
    """Check if the current request should be cancelled"""
    return cancellation_event.is_set()

def process_hero_mode(player_id, hero_name, seconds_ago, duration):
    """Process hero mode request"""
    try:
        selected_hero = next(k for k, v in dota_analysis.g_id_name_map.items() if v == hero_name)
    except StopIteration:
        return jsonify({"html": "<b>Invalid hero name selected.</b>"})
    
    if not player_id:
        return jsonify({"html": "<b>Please provide a valid player ID.</b>"})
    
    try:
        matches = dota_analysis.Matches(int(player_id), check_cancellation)
        df: pd.DataFrame = matches.get_stats_per_enemy_hero(selected_hero, seconds_ago)
        
        if df.empty:
            html = f"<b>No data available for hero {hero_name} in the selected time period.</b>"
        else:
            html_table = df.to_html(classes='display', index=False, border=0)
            total_matches = df['Matches'].sum()
            total_wins = df['Wins'].sum()
            html = f"""
            <h3>Total Matches: {total_matches} | Total Wins: {total_wins}</h3>
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
            html_table = df.to_html(classes='display', index=False, border=0)
            html = f"""
            <h3>Match History with {other_player_id}</h3>
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
        print(player_id, other_player_id, seconds_ago, duration)
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

def open_browser():
    time.sleep(1)  # wait briefly to ensure server starts
    webbrowser.open("http://localhost:5000")

if __name__ == "__main__":
    app.run(debug=True)
