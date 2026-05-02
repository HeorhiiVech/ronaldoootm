# lol_app_LTA_2/app.py
# lol_app_LTA/app.py
from dotenv import load_dotenv
import os
import sys

_basedir = os.path.abspath(os.path.dirname(__file__))
dotenv_path = os.path.join(_basedir, '.env')

try:
    from scrims_logic import log_message
except ImportError:
    import logging
    log_message = logging.info
    logging.basicConfig(level=logging.INFO)

if os.path.exists(dotenv_path):
    log_message(f"Loading .env file from: {dotenv_path}")
    load_dotenv(dotenv_path=dotenv_path)
else:
    log_message(f"WARNING: .env file not found at expected path: {dotenv_path}. API keys might not be loaded.")

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from datetime import datetime, date
from database import get_db_connection, init_db
import json
import sqlite3
from search_draft import get_filter_options, get_filtered_drafts
from scrims_logic import (
    get_champion_icon_html, 
    get_champion_data, 
    get_latest_patch_version,
    aggregate_scrim_data,
    fetch_and_store_scrims,
    get_game_replay_data
)
from tournament_logic import (
    fetch_and_store_tournament_data,
    TARGET_TOURNAMENT_NAME_FOR_DB,
    aggregate_tournament_data,
    TEAM_TAG_TO_FULL_NAME,
    ICON_SIZE_DRAFTS,
    get_all_wards_data,
    get_proximity_data
)
from soloq_logic import (
    TEAM_ROSTERS,
    aggregate_soloq_data_from_db,
    fetch_and_store_soloq_data,
    get_soloq_activity_data
)
from start_positions_logic import get_start_positions_data
from jng_clear_logic import get_jng_clear_data
from objects_logic import get_objects_data
# <<< НОВЫЙ ИМПОРТ ДЛЯ SWAP
from swap_logic import get_swap_data


app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "a_default_secret_key_change_me")
app.jinja_env.globals.update(min=min, max=max)

with app.app_context(): init_db()

@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}

@app.context_processor
def inject_utility_processor():
    champ_data = get_champion_data()
    team_tag_map = TEAM_TAG_TO_FULL_NAME
    return dict(
        get_champion_icon_html=get_champion_icon_html,
        champion_data=champ_data,
        ICON_SIZE_DRAFTS=ICON_SIZE_DRAFTS,
        team_tag_map=team_tag_map,
        date=date,
        request=request,
        get_latest_patch_version=get_latest_patch_version
    )

@app.route('/')
def index():
    return redirect(url_for('tournament'))
# --- Новый маршрут для получения данных плеера (Таймлайн + События) ---

@app.route('/get_match_replay/<game_id>')
def get_match_replay(game_id):
    """API эндпоинт для получения данных движения."""
    try:
        # Импорт внутри функции, чтобы избежать циклической зависимости
        from scrims_logic import get_game_replay_data
        
        data = get_game_replay_data(game_id)
        
        # Если данных нет вообще
        if not data:
            return jsonify([]) 
            
        return jsonify(data)
    except Exception as e:
        # Используем обычный print, если log_message не импортирован в app.py
        print(f"!!! Error in /get_match_replay/{game_id}: {e}")
        import traceback
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

# НОВЫЙ ROUTE ДЛЯ SCRIMS
@app.route('/scrims')
def scrims():
    selected_time_filter = request.args.get('time_filter', 'All Time')
    selected_side_filter = request.args.get('side_filter', 'all')
    time_filters = ["All Time", "3 Days", "1 Week", "2 Weeks", "4 Weeks", "2 Months"]
    side_filters = ["all", "blue", "red"]

    if selected_side_filter not in side_filters:
        selected_side_filter = 'all'

    try:
        # Теперь функция возвращает статистику оппонентов и список команд
        overall_stats, history_list, player_stats, opponent_stats, opponent_teams, _ = aggregate_scrim_data(
            time_filter=selected_time_filter,
            side_filter=selected_side_filter
        )
    except Exception as e:
        log_message(f"Error in /scrims data aggregation: {e}")
        flash(f"Error loading scrim data: {e}", "error")
        overall_stats, history_list, player_stats, opponent_stats, opponent_teams = {}, [], {}, {}, []

    return render_template(
        'scrims.html',
        overall_stats=overall_stats,
        history=history_list,
        player_stats=player_stats,
        opponent_stats=opponent_stats, # Передаем новые данные
        opponent_teams=opponent_teams, # Передаем новые данные
        time_filters=time_filters,
        selected_time_filter=selected_time_filter,
        selected_side_filter=selected_side_filter
    )

# НОВЫЙ ROUTE ДЛЯ ОБНОВЛЕНИЯ SCRIMS
@app.route('/update_scrims', methods=['POST'])
def update_scrims_route():
    log_message("Updating scrims data...")
    try:
        added_games = fetch_and_store_scrims()
        if added_games > 0:
            flash(f"Successfully added {added_games} new scrim game(s)!", "success")
        elif added_games == 0:
            flash("No new scrim games found.", "info")
        else:
            flash("An error occurred while updating scrims. Check logs.", "error")
    except Exception as e:
        log_message(f"Error during scrims update: {e}")
        flash(f"An unexpected error occurred: {e}", "error")

    # Возвращаемся на страницу scrims с сохранением фильтров
    time_filter = request.form.get('time_filter', 'All Time')
    side_filter = request.form.get('side_filter', 'all')
    return redirect(url_for('scrims', time_filter=time_filter, side_filter=side_filter))


@app.route('/search_draft')
def search_draft():
    options = get_filter_options()
    
    # Собираем все фильтры, используя getlist для массивов и get для одиночных значений
    filters = {
        'leagues': request.args.getlist('league'),
        'leagues_exclude': request.args.getlist('league_exclude'),
        'teams': request.args.getlist('team'),
        'teams_exclude': request.args.getlist('team_exclude'),
        'champions': request.args.getlist('champion'),
        'champions_exclude': request.args.getlist('champion_exclude'),
        'patches': request.args.getlist('patch'),
        'game_number': request.args.getlist('game_number'),
        'result': request.args.get('result', ''),
        'pick_position': request.args.get('pick_position', ''),
        'side': request.args.get('side', '')
    }

    def is_active(val):
        if isinstance(val, list):
            for v in val:
                if v:
                    if str(v).strip() != "":
                        return True
            return False
        else:
            if val:
                if str(val).strip() != "":
                    return True
            return False

    has_filters = False
    
    if is_active(filters['leagues']):
        has_filters = True
    if is_active(filters['leagues_exclude']):
        has_filters = True
    if is_active(filters['teams']):
        has_filters = True
    if is_active(filters['teams_exclude']):
        has_filters = True
    if is_active(filters['champions']):
        has_filters = True
    if is_active(filters['champions_exclude']):
        has_filters = True
    if is_active(filters['patches']):
        has_filters = True
    if is_active(filters['game_number']):
        has_filters = True
    if is_active(filters['result']):
        has_filters = True
    if is_active(filters['pick_position']):
        has_filters = True
    if is_active(filters['side']):
        has_filters = True

    drafts = []
    if has_filters == True:
        drafts = get_filtered_drafts(filters)

    # Получаем данные о чемпионах для отображения иконок
    champion_data = get_champion_data()

    return render_template('search_draft.html', 
                           options=options, 
                           filters=filters, 
                           drafts=drafts, 
                           has_filters=has_filters,
                           champion_data=champion_data)

@app.route('/tournament')
def tournament():
    selected_team_full_name = request.args.get('team')
    selected_side_filter = request.args.get('side_filter', 'all')
    side_filters = ["all", "blue", "red"]
    if selected_side_filter not in side_filters:
        selected_side_filter = 'all'

    all_teams_display, team_or_overall_stats, grouped_matches, all_game_details = [], {}, {}, []
    try:
        all_teams_display, team_or_overall_stats, grouped_matches, all_game_details_list = aggregate_tournament_data(
            selected_team_full_name=selected_team_full_name if selected_team_full_name else None,
            side_filter=selected_side_filter
        )
        all_game_details = all_game_details_list
    except Exception as e:
        log_message(f"Error in /tournament data aggregation: {e}")
        import traceback
        log_message(traceback.format_exc())
        flash(f"Error loading tournament data: {e}", "error")
        all_teams_display, team_or_overall_stats, grouped_matches, all_game_details = [], {"error": "Failed to load tournament data."}, {}, []

    return render_template('tournament.html', all_teams=all_teams_display, selected_team=selected_team_full_name, stats=team_or_overall_stats, side_filters=side_filters, selected_side_filter=selected_side_filter, matches=grouped_matches, all_game_details=all_game_details)

@app.route('/update_hll', methods=['POST'])
def update_hll_route():
    log_message("Updating HLL tournament data...")
    tournament_name_for_flash = TARGET_TOURNAMENT_NAME_FOR_DB
    try:
        added_games = fetch_and_store_tournament_data()
    except Exception as e:
        log_message(f"Error during HLL tournament update: {e}")
        flash(f"Error updating {tournament_name_for_flash}: {e}", "error")
        added_games = -1

    if added_games > 0: flash(f"Added/Updated {added_games} game(s) for {tournament_name_for_flash}!", "success")
    elif added_games == 0: flash(f"No new games found or updated for {tournament_name_for_flash}.", "info")
    return redirect(request.referrer or url_for('tournament'))

@app.route('/jng_clear')
def jng_clear():
    selected_team = request.args.get('team')
    selected_champion = request.args.get('champion', 'All')

    all_teams, stats, available_champions = [], {}, []
    try:
        all_teams, stats, available_champions = get_jng_clear_data(
            selected_team_full_name=selected_team,
            selected_champion=selected_champion
        )
    except Exception as e:
        log_message(f"Error in /jng_clear data aggregation: {e}")
        import traceback
        log_message(traceback.format_exc())
        flash(f"Error loading jungle clear data: {e}", "error")
        stats = {"error": "Failed to load jungle clear data."}

    return render_template(
        'jng_clear.html',
        all_teams=all_teams,
        selected_team=selected_team,
        available_champions=available_champions,
        selected_champion=selected_champion,
        stats=stats
    )

@app.route('/objects')
def objects():
    selected_team = request.args.get('team')
    all_teams, stats = [], {}
    try:
        all_teams, stats = get_objects_data(selected_team_full_name=selected_team)
    except Exception as e:
        log_message(f"Error in /objects data aggregation: {e}")
        import traceback
        log_message(traceback.format_exc())
        flash(f"Error loading object data: {e}", "error")
        stats = {"error": "Failed to load object data."}
    
    return render_template(
        'objects.html',
        all_teams=all_teams,
        stats=stats
    )

@app.route('/wards')
def wards():
    selected_team = request.args.get('team')
    selected_role = request.args.get('role', 'All')
    games_filter = request.args.get('games_filter', '20')
    selected_champion = request.args.get('champion', 'All')

    roles = ["All", "TOP", "JGL", "MID", "BOT", "SUP"]
    games_filters = ["5", "10", "20", "30", "50", "All"]
    
    all_teams, wards_by_interval, stats_or_error, available_champions = [], {}, {}, []
    try:
        all_teams, wards_by_interval, stats_or_error, available_champions = get_all_wards_data(
            selected_team_full_name=selected_team,
            selected_role=selected_role,
            games_filter=games_filter,
            selected_champion=selected_champion
        )
    except Exception as e:
        log_message(f"Error in /wards data aggregation: {e}")
        import traceback
        log_message(traceback.format_exc())
        flash(f"Error loading ward data: {e}", "error")
        stats_or_error = {"error": "Failed to load ward data."}

    return render_template(
        'wards.html',
        all_teams=all_teams,
        selected_team=selected_team,
        roles=roles,
        selected_role=selected_role,
        games_filters=games_filters,
        selected_games_filter=games_filter,
        wards_by_interval=wards_by_interval,
        stats=stats_or_error,
        available_champions=available_champions,
        selected_champion=selected_champion
    )

@app.route('/proximity')
def proximity():
    selected_team = request.args.get('team')
    selected_role = request.args.get('role', 'JUNGLE') 
    games_filter = request.args.get('games_filter', '20')

    proximity_roles = ["JUNGLE", "SUPPORT"]
    games_filters = ["5", "10", "20", "30", "50", "All"]

    all_teams, proximity_stats, players_in_role = [], {}, []
    try:
        all_teams, proximity_stats, players_in_role = get_proximity_data(
            selected_team_full_name=selected_team,
            selected_role=selected_role,
            games_filter=games_filter
        )
    except Exception as e:
        log_message(f"Error in /proximity data aggregation: {e}")
        import traceback
        log_message(traceback.format_exc())
        flash(f"Error loading proximity data: {e}", "error")
        proximity_stats = {"error": "Failed to load proximity data."}

    return render_template(
        'proximity.html',
        all_teams=all_teams,
        selected_team=selected_team,
        proximity_roles=proximity_roles,
        selected_role=selected_role,
        games_filters=games_filters,
        selected_games_filter=games_filter,
        stats=proximity_stats,
        players_in_role=players_in_role
    )

@app.route('/start_positions')
def start_positions():
    selected_team = request.args.get('team')
    selected_champion = request.args.get('champion', 'All')
    games_filter = request.args.get('games_filter', '10')

    games_filters = ["5", "10", "15", "20", "All"]

    all_teams, stats, available_champions = [], {}, []
    try:
        all_teams, stats, available_champions = get_start_positions_data(
            selected_team_full_name=selected_team,
            selected_champion=selected_champion,
            games_filter=games_filter
        )
    except Exception as e:
        log_message(f"Error in /start_positions data aggregation: {e}")
        import traceback
        log_message(traceback.format_exc())
        flash(f"Error loading start position data: {e}", "error")
        stats = {"error": "Failed to load start position data."}

    return render_template(
        'start_positions.html',
        all_teams=all_teams,
        selected_team=selected_team,
        games_filters=games_filters,
        selected_games_filter=games_filter,
        available_champions=available_champions,
        selected_champion=selected_champion,
        stats=stats
    )

@app.route('/soloq')
def soloq():
    selected_time_filter = request.args.get('time_filter', 'All Time')
    date_from_str = request.args.get('date_from')
    date_to_str = request.args.get('date_to')

    current_filter_label = selected_time_filter
    if date_from_str and date_to_str: current_filter_label = f"{date_from_str} to {date_to_str}"
    elif date_from_str: current_filter_label = f"From {date_from_str}"
    elif date_to_str: current_filter_label = f"Until {date_to_str}"

    time_filters_soloq = ["All Time", "1 week", "2 weeks", "3 weeks", "4 weeks"]
    player_stats_all = {}
    players = []
    target_team_roster_key = 'Gamespace'

    if target_team_roster_key not in TEAM_ROSTERS:
        flash(f"Team '{target_team_roster_key}' not found in SoloQ rosters configuration.", "error")
    else:
        players = list(TEAM_ROSTERS[target_team_roster_key].keys())
        for player in players:
            try:
                player_stats_all[player] = aggregate_soloq_data_from_db(
                    player, selected_time_filter, date_from_str, date_to_str
                )
            except Exception as e:
                log_message(f"Error aggregating SoloQ data for {player}: {e}")
                flash(f"Could not load SoloQ stats for {player}: {e}", "warning")
                player_stats_all[player] = []

    selected_player_viz = request.args.get('viz_player', players[0] if players else None)
    selected_agg_type = request.args.get('agg_type', 'Day')
    activity_data = {}
    if selected_player_viz:
        try:
            activity_data = get_soloq_activity_data(selected_player_viz, selected_agg_type)
        except Exception as e:
            log_message(f"Error getting SoloQ activity for {selected_player_viz}: {e}")
            flash(f"Could not load activity data for {selected_player_viz}: {e}", "warning")

    return render_template(
        'soloq.html',
        players=players,
        player_stats_all=player_stats_all,
        time_filters=time_filters_soloq,
        selected_time_filter=selected_time_filter,
        current_filter_label=current_filter_label,
        selected_player_viz=selected_player_viz,
        selected_agg_type=selected_agg_type,
        activity_data_json=json.dumps(activity_data)
    )

@app.route('/update_soloq', methods=['POST'])
def update_soloq_route():
    log_message("Получен запрос на обновление данных SoloQ...")
    api_key = os.getenv("RIOT_API_KEY")
    if not api_key:
        log_message("Update SoloQ failed: RIOT_API_KEY is not set in environment.")
        flash("Error: Riot API Key is not configured.", "error")
        return redirect(url_for('soloq'))

    target_team_roster_key = 'Gamespace'
    if target_team_roster_key not in TEAM_ROSTERS:
        flash(f"Team '{target_team_roster_key}' not found in SoloQ rosters.", "error")
        return redirect(url_for('soloq'))

    players = list(TEAM_ROSTERS[target_team_roster_key].keys())
    total_added_count = 0
    update_errors = 0
    for player in players:
        try:
            added_count = fetch_and_store_soloq_data(player)
            if added_count == -1: update_errors += 1
            elif added_count > 0: total_added_count += added_count
        except Exception as e:
            update_errors += 1
            log_message(f"Error during SoloQ update for player {player}: {e}")
            import traceback
            log_message(traceback.format_exc())
            flash(f"Failed to update SoloQ data for {player}: {e}", "error")

    if update_errors == 0:
        if total_added_count > 0: flash(f"Successfully added {total_added_count} new SoloQ game(s)!", "success")
        else: flash("No new SoloQ games found for any player.", "info")
    else:
        flash(f"SoloQ update completed with {update_errors} error(s). Check logs for details.", "warning")

    return redirect(request.referrer or url_for('soloq'))

# <<< НОВЫЙ МАРШРУТ ДЛЯ SWAP ---
@app.route('/swap')
def swap():
    selected_team = request.args.get('team')
    selected_champion = request.args.get('champion', 'All')
    games_filter = request.args.get('games_filter', '10')
    games_filters = ["5", "10", "20", "All"]

    all_teams, stats, available_champions = [], {}, []
    try:
        all_teams, stats, available_champions = get_swap_data(
            selected_team_full_name=selected_team,
            selected_champion=selected_champion,
            games_filter=games_filter
        )
    except Exception as e:
        log_message(f"Error in /swap data aggregation: {e}")
        import traceback
        log_message(traceback.format_exc())
        flash(f"Error loading swap data: {e}", "error")
        stats = {"error": "Failed to load swap data."}

    return render_template(
        'swap.html',
        all_teams=all_teams,
        selected_team=selected_team,
        available_champions=available_champions,
        selected_champion=selected_champion,
        games_filters=games_filters,
        selected_games_filter=games_filter,
        stats=stats
    )
# --- КОНЕЦ НОВОГО МАРШРУТА ---
@app.route('/fearless')
def fearless():
    # Загружаем список чемпионов из JSON файла
    champions_data = {}
    json_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'champions_roles.json')
    
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            champions_data = json.load(f)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    # Получаем историю сохраненных серий
    cursor.execute("SELECT id, series_name, roster, last_updated FROM fearless_drafts ORDER BY last_updated DESC")
    saved_drafts = cursor.fetchall()
    conn.close()
    
    # Передаем и чемпионов, и историю в шаблон
    return render_template('fearless.html', 
                           saved_drafts=saved_drafts, 
                           champions_by_role=champions_data)

@app.route('/api/fearless/save', methods=['POST'])
def save_fearless():
    data = request.get_json()
    series_name = data.get('series_name', 'New Series')
    roster = data.get('roster', 'Main')
    draft_data = json.dumps(data.get('draft_data', {}))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO fearless_drafts (series_name, roster, draft_data, last_updated)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    """, (series_name, roster, draft_data))
    
    conn.commit()
    conn.close()
    
    return jsonify({"status": "success", "message": "Draft saved successfully."})
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)