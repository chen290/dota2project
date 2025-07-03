# app.py
from flask import Flask, render_template, request, jsonify
import dota_analysis
import threading
import webbrowser
import time
import os

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/call_function", methods=["POST"])
def call_function():
    mode = request.form.get("mode")
    player_id = request.form.get("player_id")
    hero_name = request.form.get("hero_name", None)

    if mode == "Hero":
        try:
            selected_hero = next(k for k, v in dota_analysis.g_id_name_map.items() if v == hero_name)
        except StopIteration:
            return jsonify({"html": "<b>Invalid hero name selected.</b>"})
        selected_player = player_id
    else:
        selected_player = player_id
        selected_hero = 0  # adjust as needed

    if selected_player:
        # Call your updated generate_df returning only df
        df = dota_analysis.generate_table({"key1": ["value1", "value2"], "key2": ["value1", "value2"]})

        if df.empty:
            html = f"<b>No data available for hero {hero_name}.</b>"
        else:
            # Convert df to html table with DataTables classes
            html_table = df.to_html(classes='display', index=False, border=0)

            html = f"""
            <h3>{hero_name}</h3>
            {html_table}
            """
        return jsonify({"html": html})
    else:
        return jsonify({"html": "<b>Please provide a valid player ID.</b>"})

def open_browser():
    time.sleep(1)  # wait briefly to ensure server starts
    webbrowser.open("http://localhost:5000")

if __name__ == "__main__":
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        threading.Thread(target=open_browser).start()
    app.run(debug=True)
