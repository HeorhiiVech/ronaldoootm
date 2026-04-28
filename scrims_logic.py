# scrims_logic.py (Обновленная HLL версия)

import requests
import json
import os
from datetime import datetime, timedelta, timezone
import time
from collections import defaultdict
import sqlite3
# Убедитесь, что database.py находится там, где его можно импортировать
# Возможно, потребуется from .database import ... если структура проекта изменилась
from database import get_db_connection, SCRIMS_HEADER
import math # Для округления

# --- КОНСТАНТЫ (HLL) ---
GRID_API_KEY = os.getenv("GRID_API_KEY")
GRID_BASE_URL = "https://api.grid.gg/"
TEAM_NAME = "paiN Gaming" # HLL Team Name
PLAYER_IDS = {
    #"23038": "PAIN CarioK", 
    "20749": "Zoen",
    "29529": " Levizin",
    "29528": "Namiru",
    "29543": "Marvin",
    "25848": "Pipo"
}
ROSTER_RIOT_NAME_TO_GRID_ID = {
    #"PAIN CarioK": "23038", 
    "Zoen": "20749",
    "Levizin": "29529",
    "Namiru": "29528",
    "Marvin": "29543",
    "Pipo": "25848"
}
PLAYER_ROLES_BY_ID = {
   # "23038": "JUNGLE", 
    "20749": "TOP",
    "29529": "JUNGLE",
    "29528": "MIDDLE",
    "29543": "BOTTOM",
    "25848": "UTILITY"
}
API_REQUEST_DELAY = 0.5 # HLL Delay
ROLE_ORDER_FOR_SHEET = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
PLAYER_NAME_MAP = {
    "PAIN CarioK": "PAIN CarioK",
    "CarioK": "PAIN CarioK",
}
PLAYER_DISPLAY_ORDER = ["Zoen", "Levizin", "Namiru", "Marvin", "Pipo", "Gatovisck"]



# --- Логирование ---
def log_message(message):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"{timestamp} :: {message}")

# --- Функции для работы с GRID API (Без изменений от HLL версии) ---
def post_graphql_request(query_string, variables, endpoint, retries=3, initial_delay=1):
    """ Отправляет GraphQL POST запрос с обработкой ошибок и повторами """
    if not GRID_API_KEY:
        log_message("API Key Error: GRID_API_KEY not set.")
        return None
    headers = {"x-api-key": GRID_API_KEY, "Content-Type": "application/json"}
    payload = json.dumps({"query": query_string, "variables": variables})
    url = f"{GRID_BASE_URL}{endpoint}"
    last_exception = None

    for attempt in range(retries):
        try:
            response = requests.post(url, headers=headers, data=payload, timeout=20)
            response.raise_for_status()
            response_data = response.json()
            if "errors" in response_data and response_data["errors"]:
                error_msg = response_data["errors"][0].get("message", "Unknown GraphQL error")
                log_message(f"GraphQL Error in response: {json.dumps(response_data['errors'])}")
                if "UNAUTHENTICATED" in error_msg or "UNAUTHORIZED" in error_msg or "forbidden" in error_msg.lower():
                     log_message(f"GraphQL Auth/Permission Error: {error_msg}. Check API Key/Permissions.")
                     return None
                last_exception = Exception(f"GraphQL Error: {error_msg}")
                time.sleep(initial_delay * (2 ** attempt))
                continue
            return response_data.get("data")
        except requests.exceptions.HTTPError as http_err:
            log_message(f"HTTP error on attempt {attempt + 1}: {http_err}")
            last_exception = http_err
            if response is not None:
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", initial_delay * (2 ** attempt)))
                    log_message(f"Rate limited (429). Retrying after {retry_after} seconds.")
                    time.sleep(retry_after)
                    continue
                elif response.status_code in [401, 403]:
                    log_message(f"Authorization error ({response.status_code}). Check API Key/Permissions.")
                    return None
                elif response.status_code == 400:
                     try: error_details = response.json(); log_message(f"Bad Request (400) details: {json.dumps(error_details)}")
                     except json.JSONDecodeError: log_message(f"Bad Request (400), could not decode JSON: {response.text[:500]}")
                     break # Не повторяем 400 Bad Request
            if response is None or 500 <= response.status_code < 600: # Повторяем серверные ошибки
                 time.sleep(initial_delay * (2 ** attempt))
            else: break # Не повторяем другие клиентские ошибки
        except requests.exceptions.RequestException as req_err: log_message(f"Request exception on attempt {attempt + 1}: {req_err}"); last_exception = req_err; time.sleep(initial_delay * (2 ** attempt))
        except json.JSONDecodeError as json_err: log_message(f"JSON decode error attempt {attempt+1}: {json_err}. Response: {response.text[:200] if response else 'N/A'}"); last_exception = json_err; time.sleep(initial_delay * (2 ** attempt))
        except Exception as e: import traceback; log_message(f"Unexpected error in post_graphql attempt {attempt + 1}: {e}\n{traceback.format_exc()}"); last_exception = e; time.sleep(initial_delay * (2 ** attempt))

    log_message(f"GraphQL request failed after {retries} attempts. Last error: {last_exception}")
    return None

def get_rest_request(endpoint, retries=5, initial_delay=2, expected_type='json'):
    """ Отправляет REST GET запрос с обработкой ошибок и повторами """
    if not GRID_API_KEY:
        log_message("API Key Error: GRID_API_KEY not set.")
        return None
    headers = {"x-api-key": GRID_API_KEY}
    if expected_type == 'json': headers['Accept'] = 'application/json'

    url = f"{GRID_BASE_URL}{endpoint}"
    last_exception = None

    for attempt in range(retries):
        try:
            response = requests.get(url, headers=headers, timeout=15) # Таймаут 15 секунд
            if response.status_code == 200:
                if expected_type == 'json':
                    try: return response.json()
                    except json.JSONDecodeError as json_err: log_message(f"JSON decode error (200 OK): {json_err}. Response: {response.text[:200]}"); last_exception = json_err; break # Не повторяем ошибку декодирования
                else: return response.content # Возвращаем байты для .jsonl и др.
            elif response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", initial_delay * (2 ** attempt)))
                log_message(f"Rate limited (429). Retrying after {retry_after} seconds.")
                time.sleep(retry_after); last_exception = requests.exceptions.HTTPError(f"429 Too Many Requests"); continue
            elif response.status_code == 404: log_message(f"Resource not found (404) at {endpoint}"); last_exception = requests.exceptions.HTTPError(f"404 Not Found"); return None # Не найдено - не повторяем
            elif response.status_code in [401, 403]: error_msg = f"Auth error ({response.status_code}) for {endpoint}. Check API Key."; log_message(error_msg); last_exception = requests.exceptions.HTTPError(f"{response.status_code} Unauthorized/Forbidden"); return None # Ошибка доступа - не повторяем
            else: response.raise_for_status() # Вызовет HTTPError для других кодов 4xx/5xx
        except requests.exceptions.HTTPError as http_err: log_message(f"HTTP error attempt {attempt + 1}: {http_err}"); last_exception = http_err; time.sleep(initial_delay * (2 ** attempt)) # Повторяем серверные ошибки
        except requests.exceptions.RequestException as req_err: log_message(f"Request exception attempt {attempt + 1}: {req_err}"); last_exception = req_err; time.sleep(initial_delay * (2 ** attempt)) # Повторяем ошибки сети
        except Exception as e: log_message(f"Unexpected error attempt {attempt + 1}: {e}"); last_exception = e; time.sleep(initial_delay * (2 ** attempt)) # Повторяем другие ошибки

    log_message(f"REST GET failed after {retries} attempts for {endpoint}. Last error: {last_exception}")
    return None

def get_all_series(days_ago=4):
    """ Получает список ID и дат начала LoL скримов за последние N дней """
    query_string = """
        query ($filter: SeriesFilter, $first: Int, $after: Cursor, $orderBy: SeriesOrderBy, $orderDirection: OrderDirection) {
          allSeries( filter: $filter, first: $first, after: $after, orderBy: $orderBy, orderDirection: $orderDirection ) {
            totalCount, pageInfo { hasNextPage, endCursor }, edges { node { id, startTimeScheduled } } } }
    """
    start_thresh = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    variables_template = { "filter": {"titleId": 3, "types": ["SCRIM"], "startTimeScheduled": {"gte": start_thresh}}, "first": 50, "orderBy": "StartTimeScheduled", "orderDirection": "DESC" }
    all_nodes = []; cursor = None; page_num = 1; max_pages = 20

    log_message(f"Fetching series from the last {days_ago} days...")
    while page_num <= max_pages:
        current_variables = variables_template.copy()
        if cursor: current_variables["after"] = cursor
        else: current_variables.pop("after", None)

        response_data = post_graphql_request(query_string=query_string, variables=current_variables, endpoint="central-data/graphql")
        if not response_data: log_message(f"Failed to fetch series page {page_num}. Stopping."); break

        series_data = response_data.get("allSeries", {}); edges = series_data.get("edges", [])
        nodes = [edge["node"] for edge in edges if "node" in edge]; all_nodes.extend(nodes)
        page_info = series_data.get("pageInfo", {}); has_next_page = page_info.get("hasNextPage", False); cursor = page_info.get("endCursor")
        if not has_next_page or not cursor: break
        page_num += 1; time.sleep(API_REQUEST_DELAY)

    log_message(f"Finished fetching series. Total series found: {len(all_nodes)}")
    return all_nodes

def get_series_state(series_id):
    """ Получает список игр (id, sequenceNumber) для заданной серии """
    query_template = """ query GetSeriesGames($seriesId: ID!) { seriesState ( id: $seriesId ) { id, games { id, sequenceNumber } } } """
    variables = {"seriesId": series_id}
    response_data = post_graphql_request(query_string=query_template, variables=variables, endpoint="live-data-feed/series-state/graphql")

    if response_data and response_data.get("seriesState") and "games" in response_data["seriesState"]:
        games = response_data["seriesState"]["games"]
        if games is None: log_message(f"Series {series_id} found, but games list is null."); return []
        return games
    elif response_data and not response_data.get("seriesState"): log_message(f"No seriesState found for series {series_id}."); return []
    else: log_message(f"Failed to get games for series {series_id}."); return []

def download_riot_summary_data(series_id, sequence_number):
    """ Скачивает Riot Summary JSON для конкретной игры """
    endpoint = f"file-download/end-state/riot/series/{series_id}/games/{sequence_number}/summary"
    summary_data = get_rest_request(endpoint, expected_type='json')
    return summary_data

# --- НОВОЕ: Скачивание LiveStats (из UOL) ---
def download_riot_livestats_data(series_id, sequence_number):
    """ Скачивает Riot LiveStats (.jsonl) для конкретной игры LoL """
    endpoint = f"file-download/events/riot/series/{series_id}/games/{sequence_number}"
    log_message(f"Attempting to download LiveStats for s:{series_id} g:{sequence_number} from {endpoint}")

    # Ожидаем сырой контент (байты)
    livestats_content_bytes = get_rest_request(endpoint, expected_type='content', retries=2, initial_delay=5)

    if livestats_content_bytes:
        log_message(f"Successfully downloaded LiveStats content for s:{series_id} g:{sequence_number} ({len(livestats_content_bytes)} bytes)")
        try:
            # Пытаемся декодировать как UTF-8
            return livestats_content_bytes.decode('utf-8')
        except UnicodeDecodeError:
            log_message(f"Warning: Could not decode LiveStats as UTF-8 for s:{series_id} g:{sequence_number}. Trying latin-1.")
            try:
                # Попытка с другой кодировкой
                return livestats_content_bytes.decode('latin-1')
            except Exception as e_dec:
                 log_message(f"Error decoding livestats content with latin-1 for s:{series_id} g:{sequence_number}: {e_dec}. Returning None.")
                 return None
        except Exception as e:
            log_message(f"Error decoding livestats content for s:{series_id} g:{sequence_number}: {e}")
            return None
    else:
        log_message(f"Failed to download LiveStats for s:{series_id} g:{sequence_number}")
        return None

# --- Вспомогательные функции парсинга ---
def normalize_player_name(riot_id_game_name):
    """ Удаляет известные командные префиксы из игрового имени Riot ID """
    if isinstance(riot_id_game_name, str):
        known_prefixes = ["GSMC ","PAIN ", "PAIN ","PNGA"] # Используем префикс HLL
        for prefix in known_prefixes:
            if riot_id_game_name.startswith(prefix):
                return riot_id_game_name[len(prefix):].strip()
    return riot_id_game_name

def extract_team_tag(riot_id_game_name):
    """Пытается извлечь потенциальный тег команды."""
    if isinstance(riot_id_game_name, str) and ' ' in riot_id_game_name:
        parts = riot_id_game_name.split(' ', 1); tag = parts[0]
        if 2 <= len(tag) <= 5 and tag.isupper() and tag.isalnum():
             common_roles = {"MID", "TOP", "BOT", "JGL", "JUG", "JG", "JUN", "ADC", "SUP", "SPT"}
             if tag.upper() not in common_roles: return tag
    return None

# --- Функция обновления и сохранения данных скримов в SQLite (Без изменений от HLL) ---
def fetch_and_store_scrims():
    log_message("Starting scrims update process...")
    series_list = get_all_series(days_ago=4)
    if not series_list: 
        log_message("No recent series found.")
        return 0

    conn = get_db_connection()
    if not conn: 
        log_message("DB Connection failed for scrim update.")
        return -1 
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT Game_ID FROM scrims")
        existing_game_ids = {row['Game_ID'] for row in cursor.fetchall()}
        log_message(f"Found {len(existing_game_ids)} existing game IDs in DB.")
    except sqlite3.Error as e:
        log_message(f"Error reading existing game IDs: {e}. Proceeding without duplicate check.")
        existing_game_ids = set()

    added_count = 0
    processed_series_count = 0
    total_series = len(series_list)

    sql_column_names = [hdr.replace(" ", "_").replace(".", "").replace("-", "_") for hdr in SCRIMS_HEADER]
    quoted_column_names = [f'"{col}"' for col in sql_column_names]
    columns_string = ', '.join(quoted_column_names)
    sql_placeholders = ", ".join(["?"] * len(sql_column_names))
    insert_sql = f"INSERT OR IGNORE INTO scrims ({columns_string}) VALUES ({sql_placeholders})"

    for series_summary in series_list:
        processed_series_count += 1
        if processed_series_count % 10 == 0:
            log_message(f"Processing series {processed_series_count}/{total_series}...")

        series_id = series_summary.get("id")
        if not series_id: continue

        games_in_series = get_series_state(series_id)
        if not games_in_series: 
            time.sleep(API_REQUEST_DELAY / 2)
            continue
            
        # НОВОЕ: Скачиваем данные о драфте (grid end-state) для серии
        series_end_state_data = get_rest_request(f"file-download/end-state/grid/series/{series_id}", expected_type='json')
        time.sleep(API_REQUEST_DELAY / 2)

        for game_info in games_in_series:
            game_id = game_info.get("id")
            sequence_number = game_info.get("sequenceNumber")
            if not game_id or sequence_number is None: continue
            if game_id in existing_game_ids: continue

            summary_data = download_riot_summary_data(series_id, sequence_number)
            if not summary_data: 
                time.sleep(API_REQUEST_DELAY)
                continue
                
            # НОВОЕ: Получаем действия драфта для конкретной игры
            current_game_draft_actions = []
            if series_end_state_data and series_end_state_data.get("seriesState", {}).get("games"):
                for game_state in series_end_state_data["seriesState"]["games"]:
                    if game_state and game_state.get("sequenceNumber") == sequence_number:
                        current_game_draft_actions = game_state.get("draftActions", [])
                        break

            try:
                participants = summary_data.get("participants", [])
                teams_data = summary_data.get("teams", [])
                if not participants or len(participants) != 10 or not teams_data or len(teams_data) != 2: 
                    continue

                our_side = None
                our_team_id = None
                for idx, p in enumerate(participants):
                    normalized_name = normalize_player_name(p.get("riotIdGameName"))
                    if normalized_name in ROSTER_RIOT_NAME_TO_GRID_ID:
                        current_side = 'blue' if idx < 5 else 'red'
                        current_team_id = 100 if idx < 5 else 200
                        if our_side is None: 
                            our_side = current_side
                            our_team_id = current_team_id
                        elif our_side != current_side: 
                            log_message(f"Warn: Players on both sides! G:{game_id}")
                            break
                if our_side is None: continue

                opponent_team_name = "Opponent"
                opponent_tags = defaultdict(int)
                opponent_indices = range(5, 10) if our_side == 'blue' else range(0, 5)
                for idx in opponent_indices:
                    if idx < len(participants): 
                        tag = extract_team_tag(participants[idx].get("riotIdGameName"))
                        if tag: opponent_tags[tag] += 1
                
                if opponent_tags: 
                    sorted_tags = sorted(opponent_tags.items(), key=lambda item: item[1], reverse=True)
                    opponent_team_name = sorted_tags[0][0] if sorted_tags[0][1] >= 3 else "Opponent"
                
                blue_team_name = TEAM_NAME if our_side == 'blue' else opponent_team_name
                red_team_name = TEAM_NAME if our_side == 'red' else opponent_team_name

                result = "Unknown"
                for team_summary in teams_data:
                    if team_summary.get("teamId") == our_team_id: 
                        win_status = team_summary.get("win")
                        result = "Win" if win_status is True else "Loss" if win_status is False else "Unknown"
                        break

                blue_bans = ["N/A"] * 5
                red_bans = ["N/A"] * 5
                for team in teams_data:
                    target_bans = blue_bans if team.get("teamId") == 100 else red_bans
                    bans_list = sorted(team.get("bans", []), key=lambda x: x.get('pickTurn', 99))
                    for i, ban in enumerate(bans_list[:5]): 
                        target_bans[i] = str(c_id) if (c_id := ban.get("championId", -1)) != -1 else "N/A"

                game_creation_timestamp = summary_data.get("gameCreation")
                date_str = "N/A"
                if game_creation_timestamp:
                    try: 
                        dt_obj = datetime.fromtimestamp(game_creation_timestamp/1000, timezone.utc)
                        date_str = dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception: pass

                game_duration_sec = summary_data.get("gameDuration", 0)
                duration_str = "N/A"
                if game_duration_sec > 0:
                    minutes, seconds = divmod(int(game_duration_sec), 60)
                    duration_str = f"{minutes}:{seconds:02d}"

                game_version = summary_data.get("gameVersion", "N/A")
                patch_str = "N/A"
                if game_version != "N/A": 
                    parts = game_version.split('.')
                    patch_str = f"{parts[0]}.{parts[1]}" if len(parts) >= 2 else game_version

                row_dict = {sql_col: "N/A" for sql_col in sql_column_names}
                row_dict["Date"] = date_str
                row_dict["Patch"] = patch_str
                row_dict["Blue_Team_Name"] = blue_team_name
                row_dict["Red_Team_Name"] = red_team_name
                row_dict["Duration"] = duration_str
                row_dict["Result"] = result
                row_dict["Game_ID"] = game_id
                for i in range(5): 
                    row_dict[f"Blue_Ban_{i+1}_ID"] = blue_bans[i]
                    row_dict[f"Red_Ban_{i+1}_ID"] = red_bans[i]

                role_to_abbr = {"TOP": "TOP", "JUNGLE": "JGL", "MIDDLE": "MID", "BOTTOM": "BOT", "UTILITY": "SUP"}
                for idx, p in enumerate(participants):
                    role_name = ROLE_ORDER_FOR_SHEET[idx % 5]
                    side_prefix = "Blue" if idx < 5 else "Red"
                    role_abbr = role_to_abbr.get(role_name)
                    player_col_prefix = f"{side_prefix}_{role_abbr}"
                    if not role_abbr: continue

                    row_dict[f"{player_col_prefix}_Player"] = normalize_player_name(p.get("riotIdGameName")) or "Unknown"
                    row_dict[f"{player_col_prefix}_Champ"] = p.get("championName", "N/A")
                    row_dict[f"{player_col_prefix}_K"] = p.get('kills', 0)
                    row_dict[f"{player_col_prefix}_D"] = p.get('deaths', 0)
                    row_dict[f"{player_col_prefix}_A"] = p.get('assists', 0)
                    row_dict[f"{player_col_prefix}_Dmg"] = p.get('totalDamageDealtToChampions', 0)
                    row_dict[f"{player_col_prefix}_CS"] = p.get('totalMinionsKilled', 0) + p.get('neutralMinionsKilled', 0)
                    
                    items = [str(p.get(f"item{i}", 0)) for i in range(7) if p.get(f"item{i}", 0) != 0]
                    row_dict[f"{player_col_prefix}_Items"] = ",".join(items)

                    all_runes = []
                    perks = p.get("perks", {})
                    for style in perks.get("styles", []):
                        for sel in style.get("selections", []):
                            if (pid := sel.get("perk", 0)) != 0: all_runes.append(str(pid))
                    
                    sp = perks.get("statPerks", {})
                    for sk in ['offense', 'flex', 'defense']:
                        if (sid := sp.get(sk, 0)) != 0: all_runes.append(str(sid))
                    
                    row_dict[f"{player_col_prefix}_Runes"] = ",".join(all_runes) if all_runes else "0"
                    row_dict[f"{player_col_prefix}_Gold"] = p.get('goldEarned', 0)

                # НОВОЕ: Обработка Draft Actions
                if current_game_draft_actions and isinstance(current_game_draft_actions, list):
                     actions_by_seq = {int(a["sequenceNumber"]): a for a in current_game_draft_actions if a.get("sequenceNumber") is not None}
                     for i in range(1, 21):
                         action = actions_by_seq.get(i)
                         if action:
                             drafter_info = action.get("drafter", {})
                             draftable_info = action.get("draftable", {})
                             row_dict[f"Draft_Action_{i}_Type"] = str(action.get("type", "N/A")).lower() if action.get("type") else "N/A"
                             row_dict[f"Draft_Action_{i}_TeamID"] = str(drafter_info.get("id", "N/A")) if drafter_info.get("id") else "N/A"
                             row_dict[f"Draft_Action_{i}_ChampName"] = str(draftable_info.get("name", "N/A")) if draftable_info.get("name") else "N/A"
                             row_dict[f"Draft_Action_{i}_ChampID"] = str(draftable_info.get("id", "N/A")) if draftable_info.get("id") else "N/A"
                             row_dict[f"Draft_Action_{i}_ActionID"] = str(action.get("id", "N/A")) if action.get("id") else "N/A"

                data_tuple = tuple(row_dict.get(sql_col, "N/A") for sql_col in sql_column_names)
                
                # Сохраняем основную информацию об игре
                cursor.execute(insert_sql, data_tuple)
                
                if cursor.rowcount > 0:
                    added_count += 1
                    existing_game_ids.add(game_id)
                    
                    conn.commit() 
                    
                    log_message(f"New game {game_id} added. Fetching timeline...")
                    timeline_data = download_riot_livestats_data(series_id, sequence_number)
                    
                    if timeline_data:
                        process_replay_to_db(game_id, timeline_data, summary_data)
                        log_message(f"Replay data stored for {game_id}")
                    else:
                        log_message(f"Warning: Timeline data not available for {game_id}")

            except Exception as e:
                log_message(f"Parse/Process fail G:{game_id}: {e}")
                import traceback
                log_message(traceback.format_exc())
                continue
            finally: 
                time.sleep(API_REQUEST_DELAY / 4)

        conn.commit() 
        time.sleep(API_REQUEST_DELAY / 2)

    conn.close()
    log_message(f"Scrims update finished. Added {added_count} new game(s).")
    return added_count

# --- Функции для работы с Data Dragon ---
_champion_data_cache = {}
_latest_patch_cache = None
_patch_cache_time = None

def get_latest_patch_version(cache_duration=3600):
    """Получает последнюю версию патча LoL, кэширует результат."""
    global _latest_patch_cache, _patch_cache_time
    now = time.time()
    if _latest_patch_cache and _patch_cache_time and (now - _patch_cache_time < cache_duration):
        return _latest_patch_cache
    try:
        response = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=10)
        response.raise_for_status()
        versions = response.json()
        if versions:
            _latest_patch_cache = versions[0]
            _patch_cache_time = now
            return _latest_patch_cache
        else: return "14.7.1" # Fallback
    except Exception as e: log_message(f"Error getting latest patch: {e}"); return "14.7.1"

# ОБНОВЛЕННАЯ normalize_champion_name_for_ddragon (с UOL)
def normalize_champion_name_for_ddragon(champ):
    """Нормализует имя чемпиона для URL Data Dragon."""
    if not champ or champ == "N/A": return None
    # Словарь исключений и специфического регистра
    overrides = {
        "Nunu & Willump": "Nunu", "Wukong": "MonkeyKing", "Renata Glasc": "Renata",
        "K'Sante": "KSante", "LeBlanc": "Leblanc", "Miss Fortune": "MissFortune",
        "Jarvan IV": "JarvanIV", "Twisted Fate": "TwistedFate", "Dr. Mundo": "DrMundo",
        "Xin Zhao": "XinZhao", "Bel'Veth": "Belveth", "Kai'Sa": "Kaisa",
        "Cho'Gath": "Chogath", "Kha'Zix": "Khazix", "Vel'Koz": "Velkoz",
        "Rek'Sai": "RekSai", "Aurelion Sol": "AurelionSol", # Добавлено из UOL
        "Fiddlesticks": "Fiddlesticks" # Добавлено из UOL
    }
    if champ in overrides: return overrides[champ]
    # Общая очистка (убираем пробелы, апострофы, точки)
    name_clean = ''.join(c for c in champ if c.isalnum())
    # Некоторые стандартные случаи ddragon после очистки (в нижнем регистре для сравнения)
    ddragon_exceptions = {
        "monkeyking": "MonkeyKing", "ksante": "KSante", "leblanc": "Leblanc",
        "missfortune": "MissFortune", "jarvaniv": "JarvanIV", "twistedfate": "TwistedFate",
        "drmundo": "DrMundo", "xinzhao": "XinZhao", "belveth": "Belveth", "kaisa": "Kaisa",
        "chogath": "Chogath", "khazix": "Khazix", "velkoz": "Velkoz", "reksai": "RekSai",
         "aurelionsol": "AurelionSol" # Добавлено из UOL
         }
    name_clean_lower = name_clean.lower()
    if name_clean_lower in ddragon_exceptions: return ddragon_exceptions[name_clean_lower]
    # Если не в исключениях, просто возвращаем очищенное имя
    # Data Dragon обычно чувствителен к регистру, но очищенное имя часто работает
    return name_clean

def get_champion_data(cache_duration=86400):
    """Загружает данные чемпионов с Data Dragon, кэширует результат."""
    global _champion_data_cache
    now = time.time()
    cache_key = 'champion_data'
    if cache_key in _champion_data_cache and (now - _champion_data_cache[cache_key]['timestamp'] < cache_duration):
        return _champion_data_cache[cache_key]['data']

    patch_version = get_latest_patch_version()
    url = f"https://ddragon.leagueoflegends.com/cdn/{patch_version}/data/en_US/champion.json"
    log_message(f"Fetching champion data from ddragon (Patch: {patch_version})...")
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()['data']
        champion_id_map = {} # 'ID': 'Name'
        champion_name_map = {} # 'Name': 'DDragonName'
        for champ_ddragon_name, champ_info in data.items():
             champ_id = champ_info['key']
             champ_name = champ_info['name']
             champion_id_map[str(champ_id)] = champ_name
             normalized_ddragon_name = normalize_champion_name_for_ddragon(champ_name)
             # Используем нормализованное имя, если оно не None, иначе исходное из ddragon
             champion_name_map[champ_name] = normalized_ddragon_name if normalized_ddragon_name else champ_ddragon_name

        result_data = {'id_map': champion_id_map, 'name_map': champion_name_map}
        _champion_data_cache[cache_key] = {'data': result_data, 'timestamp': now}
        log_message("Champion data fetched and cached.")
        return result_data
    except Exception as e:
        log_message(f"Failed to fetch or process champion data: {e}")
        return {'id_map': {}, 'name_map': {}}

# ОБНОВЛЕННАЯ get_champion_icon_html (из UOL)
def get_champion_icon_html(champion_name_or_id, champion_data, width=25, height=25):
    """Генерирует HTML img тэг (или fallback span '?') для иконки чемпиона."""
    func_input = champion_name_or_id # Сохраняем исходное значение для логов/title

    if not champion_name_or_id or not champion_data:
        return f'<span title="Icon error: Input missing for {func_input}">?</span>' # Заглушка

    champ_name = None
    ddragon_name = None
    input_is_string = isinstance(champion_name_or_id, str)
    input_as_string = str(champion_name_or_id) # Преобразуем в строку для поиска в словарях

    id_map = champion_data.get('id_map', {})
    name_map = champion_data.get('name_map', {})

    # 1. Определение имени чемпиона (champ_name)
    if input_as_string in id_map: # Если передан ID
        champ_name = id_map[input_as_string]
    elif input_is_string: # Если передана строка (может быть имя)
        champ_name = champion_name_or_id

    # 2. Определение имени для Data Dragon (ddragon_name)
    if champ_name:
        # Сначала ищем точное совпадение имени в name_map
        if champ_name in name_map:
            ddragon_name = name_map[champ_name]
        else:
            # Если точного совпадения нет, пробуем нормализовать имя и поискать снова
            normalized_name_from_champ = normalize_champion_name_for_ddragon(champ_name)
            if normalized_name_from_champ and normalized_name_from_champ in name_map.values(): # Проверяем, есть ли такое значение в name_map
                 ddragon_name = normalized_name_from_champ
            elif normalized_name_from_champ: # Если нет в values, используем само нормализованное имя
                 ddragon_name = normalized_name_from_champ
                 # log_message(f"[Icon Debug] Used normalized name '{ddragon_name}' for '{champ_name}' as direct map failed.")
    # Если имя определить не удалось, но на входе была строка, пробуем нормализовать входную строку
    elif input_is_string:
         normalized_input = normalize_champion_name_for_ddragon(champion_name_or_id)
         if normalized_input:
              ddragon_name = normalized_input
              # log_message(f"[Icon Debug] Used normalized input '{ddragon_name}' for '{func_input}'.")

    # 3. Проверка валидности ddragon_name и генерация HTML
    is_ddragon_name_valid = False
    if ddragon_name:
        ddragon_name_lower = ddragon_name.lower()
        # Проверяем на невалидные значения
        if ddragon_name_lower not in ["n/a", "-1", "unknown", "none", "null", ""]:
            is_ddragon_name_valid = True

    if is_ddragon_name_valid:
        patch = get_latest_patch_version()
        icon_url = f"https://ddragon.leagueoflegends.com/cdn/{patch}/img/champion/{ddragon_name}.png"
        display_name_title = champ_name if champ_name else ddragon_name # Для title используем лучшее доступное имя
        return (f'<img src="{icon_url}" width="{width}" height="{height}" '
                f'alt="{display_name_title}" title="{display_name_title}" '
                f'style="vertical-align: middle; margin: 1px;">')
    else:
        # Если не смогли получить валидное имя для ddragon, возвращаем "?"
        display_name_fallback = champ_name if champ_name else func_input
        # log_message(f"[Icon] Failed to find valid ddragon name for '{display_name_fallback}'. Returning '?'.")
        return f'<span title="Icon error: {display_name_fallback}">?</span>'
    
def get_rune_icon_html(rune_id_input, width=22, height=22):
    """
    Универсальная функция для иконок рун через OP.GG.
    Разбирает строку с ID через запятую и возвращает HTML список иконок.
    """
    if not rune_id_input:
        return ""

    # Если в строке несколько ID через запятую ("8437,8112")
    if isinstance(rune_id_input, str) and ',' in rune_id_input:
        ids = [r.strip() for r in rune_id_input.split(',') if r.strip()]
    elif isinstance(rune_id_input, (list, tuple)):
        ids = [str(r) for r in rune_id_input]
    else:
        # Если пришел один ID
        ids = [str(rune_id_input)]

    html_elements = []
    
    for r_id in ids:
        if not r_id or str(r_id) in ["0", "N/A", "None", "-1"]:
            continue
            
        try:
            # Чистим ID от лишних .0
            clean_id = str(int(float(r_id)))
            
            # Ссылка на OP.GG
            icon_url = f"https://opgg-static.akamaized.net/images/lol/perk/{clean_id}.png"
            
            html_elements.append(
                f'<img src="{icon_url}" width="{width}" height="{height}" '
                f'title="Rune ID: {clean_id}" '
                f'style="border-radius:50%; vertical-align:middle; margin: 1px; background:rgba(0,0,0,0.3);" '
                f'onerror="this.style.display=\'none\';">'
            )
        except (ValueError, TypeError):
            continue

    return "".join(html_elements)
# --- ДОБАВИТЬ ЭТИ ПЕРЕМЕННЫЕ И ФУНКЦИЮ ПЕРЕД aggregate_scrim_data ---
blue_pick_seq_to_phase = { 7: 'B1', 10: 'B2-3', 11: 'B2-3', 18: 'B4-5', 19: 'B4-5' }
red_pick_seq_to_phase = { 8: 'R1-2', 9: 'R1-2', 12: 'R3', 17: 'R4', 20: 'R5' }

def format_priority_picks(priority_dict, champion_data):
    """Форматирует словарь приоритетов для шаблона."""
    # Нужно импортировать внутри, чтобы избежать циклических импортов, если они есть
    from app import get_champion_icon_html
    priority_list = []
    max_picks_in_phase = 0
    
    for champ, phase_counts in priority_dict.items():
        current_max = max(phase_counts.values()) if phase_counts else 0
        if current_max > max_picks_in_phase:
            max_picks_in_phase = current_max

    for champ, phase_counts in priority_dict.items():
        total_picks = sum(phase_counts.values())
        entry = {
            "champion": champ,
            "total_picks": total_picks,
            "icon_html": get_champion_icon_html(champ, champion_data, 35, 35),
            "phases": {phase: count for phase, count in phase_counts.items()}
        }
        priority_list.append(entry)
    
    return sorted(priority_list, key=lambda x: x['total_picks'], reverse=True), max_picks_in_phase if max_picks_in_phase > 0 else 1

def aggregate_scrim_data(time_filter="All Time", side_filter="all"):
    """
    Агрегация данных скримов, включая парсинг Priority Picks оппонентов.
    """
    from database import get_db_connection 
    
    log_message(f"Aggregating scrim data. Time: {time_filter}, Side: {side_filter}")
    conn = get_db_connection()
    if not conn: return {}, [], {}, {}, {}, {} 

    where_clause = ""
    params = []
    if time_filter != "All Time":
        now_utc = datetime.now(timezone.utc)
        delta = {"3 Days": 3, "1 Week": 7, "2 Weeks": 14, "4 Weeks": 28, "2 Months": 60}.get(time_filter)
        if delta:
            cutoff_date = (now_utc - timedelta(days=delta)).strftime("%Y-%m-%d %H:%M:%S")
            where_clause = "WHERE \"Date\" >= ?"
            params.append(cutoff_date)

    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM scrims {where_clause} ORDER BY \"Date\" DESC", params)
        all_scrim_data = cursor.fetchall()
        
        from app import get_champion_data, get_champion_icon_html
        champion_data = get_champion_data()
        
        overall_stats = { "total_games": 0, "blue_wins": 0, "blue_losses": 0, "red_wins": 0, "red_losses": 0 }
        history_list = []
        player_stats_agg = defaultdict(lambda: defaultdict(lambda: {'games': 0, 'wins': 0, 'k': 0, 'd': 0, 'a': 0, 'dmg': 0, 'cs': 0}))
        
        # Словарь для сбора статистики оппонентов
        # Структура: {TeamName: {"players": {PlayerName: {Champ: stats}}, "temp_priority": {'Blue': {...}, 'Red': {...}}}}
        opponent_stats_agg = defaultdict(lambda: {
            "players": defaultdict(lambda: defaultdict(lambda: {'games': 0, 'wins': 0, 'k': 0, 'd': 0, 'a': 0, 'dmg': 0, 'cs': 0})),
            "temp_priority": {
                'Blue': defaultdict(lambda: defaultdict(int)),
                'Red': defaultdict(lambda: defaultdict(int))
            }
        })
        
        role_to_abbr = {"TOP": "TOP", "JUNGLE": "JGL", "MIDDLE": "MID", "BOTTOM": "BOT", "UTILITY": "SUP"}
        roles_ordered = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]

        for row in all_scrim_data:
            game = dict(row)
            duration_str = game.get("Duration", "00:00")
            try:
                parts = duration_str.split(':')
                if len(parts) == 2:
                    minutes, seconds = map(int, parts)
                    total_seconds = minutes * 60 + seconds
                else:
                    total_seconds = int(parts[0])
                if total_seconds < 900:
                    continue
            except (ValueError, TypeError):
                continue
                
            game_id = str(game.get("Game_ID") or game.get("Game ID") or "N/A")
            result = game.get("Result", "Unknown")
            
            blue_team = game.get("Blue_Team_Name")
            red_team = game.get("Red_Team_Name")
            
            is_our_blue = blue_team == "paiN Gaming"
            is_our_red = red_team == "paiN Gaming"
            
            opponent_team_name = red_team if is_our_blue else blue_team if is_our_red else "Unknown"
            
            if is_our_blue:
                if result == "Win": overall_stats["blue_wins"] += 1
                else: overall_stats["blue_losses"] += 1
            elif is_our_red:
                if result == "Win": overall_stats["red_wins"] += 1
                else: overall_stats["red_losses"] += 1
            overall_stats["total_games"] += 1

            details = {
                'blue_players': [], 'red_players': [],
                'blue_total_kills': 0, 'red_total_kills': 0,
                'blue_events': [], 'red_events': []
            }

            if game_id != "N/A":
                ev_cursor = conn.cursor()
                ev_cursor.execute("SELECT * FROM objective_events WHERE game_id = ? ORDER BY timestamp_ms ASC", (game_id,))
                rows = ev_cursor.fetchall()
                for e_row in rows:
                    evt = dict(e_row)
                    ms = evt.get('timestamp_ms', 0)
                    time_str = f"{(ms // 1000) // 60}:{(ms // 1000) % 60:02d}"
                    obj_t = evt.get('objective_type', '')
                    sub = evt.get('objective_subtype', '')
                    lane = evt.get('lane', '')
                    p_name = evt.get('player_name') or evt.get('killer_name') or ""
                    player_part = f" ({p_name})" if p_name else ""
                    
                    if obj_t == 'TOWER': txt = f"Tower: {sub} ({lane}){player_part}"
                    elif obj_t == 'DRAGON': txt = f"Dragon: {sub}{player_part}"
                    else: txt = f"{obj_t}: {sub}{player_part}" if sub else f"{obj_t}{player_part}"

                    details['blue_events' if evt.get('team_id') == 100 else 'red_events'].append({
                        'time': time_str, 'text': txt, 'timestamp': ms, 
                        'teamId': evt.get('team_id'), 'type': obj_t
                    })
                ev_cursor.close()

            bb = [get_champion_icon_html(game.get(f"Blue_Ban_{i}_ID"), champion_data) for i in range(1, 6)]
            rb = [get_champion_icon_html(game.get(f"Red_Ban_{i}_ID"), champion_data) for i in range(1, 6)]
            
            bp, rp = [], []
            for role in roles_ordered:
                r_a = role_to_abbr.get(role)
                bp.append(get_champion_icon_html(game.get(f"Blue_{r_a}_Champ"), champion_data))
                rp.append(get_champion_icon_html(game.get(f"Red_{r_a}_Champ"), champion_data))

            match_max_dmg = 1
            match_max_gold = 1
            for role in roles_ordered:
                r_a = role_to_abbr[role]
                match_max_dmg = max(match_max_dmg, int(game.get(f"Blue_{r_a}_Dmg", 0) or 0), int(game.get(f"Red_{r_a}_Dmg", 0) or 0))
                match_max_gold = max(match_max_gold, int(game.get(f"Blue_{r_a}_Gold", 0) or 0), int(game.get(f"Red_{r_a}_Gold", 0) or 0))

            # Сбор данных игроков
            for role in roles_ordered:
                r_a = role_to_abbr[role]
                for side in ['Blue', 'Red']:
                    p_f = f"{side}_{r_a}"
                    champ = game.get(f"{p_f}_Champ", "N/A")
                    k, d, a = int(game.get(f"{p_f}_K", 0) or 0), int(game.get(f"{p_f}_D", 0) or 0), int(game.get(f"{p_f}_A", 0) or 0)
                    dmg, cs = int(game.get(f"{p_f}_Dmg", 0) or 0), int(game.get(f"{p_f}_CS", 0) or 0)
                    gold = int(game.get(f"{p_f}_Gold", 0) or 0)
                    
                    rune_id = game.get(f"{p_f}_Runes", "0")
                    rune_html = get_rune_icon_html(rune_id)
                    
                    p_entry = {
                        'role': role, 
                        'name': game.get(f"{p_f}_Player", "Unknown"),
                        'champion': champ, 
                        'icon_html': get_champion_icon_html(champ, champion_data, 32, 32),
                        'k': k, 'd': d, 'a': a, 
                        'dmg': dmg, 'gold': gold, 'cs': cs,
                        'max_dmg': match_max_dmg, 'max_gold': match_max_gold, 
                        'items_list': game.get(f"{p_f}_Items", ""),
                        'rune_html': rune_html
                    }
                    
                    details[side.lower() + '_players'].append(p_entry)
                    details[side.lower() + '_total_kills'] += k

                    if (side == 'Blue' and is_our_blue) or (side == 'Red' and is_our_red):
                        st = player_stats_agg[p_entry['name']][champ]
                        st['games'] += 1; st['wins'] += (result == "Win")
                        st['k'] += k; st['d'] += d; st['a'] += a; st['dmg'] += dmg; st['cs'] += cs
                    else:
                        if opponent_team_name != "Unknown" and opponent_team_name != "Opponent":
                            opp_st = opponent_stats_agg[opponent_team_name]["players"][p_entry['name']][champ]
                            opp_st['games'] += 1
                            opp_win = 1 if result == "Loss" else 0
                            opp_st['wins'] += opp_win
                            opp_st['k'] += k; opp_st['d'] += d; opp_st['a'] += a; opp_st['dmg'] += dmg; opp_st['cs'] += cs

            # НОВОЕ: Сбор Priority Picks Оппонента
            if opponent_team_name != "Unknown" and opponent_team_name != "Opponent":
                reconstructed_draft = {}
                for i in range(1, 21):
                    action_type = game.get(f"Draft_Action_{i}_Type")
                    champ_name = game.get(f"Draft_Action_{i}_ChampName")
                    if action_type and action_type != "N/A" and champ_name and champ_name != "N/A":
                        reconstructed_draft[i] = {
                            "Action_Type": str(action_type).lower(),
                            "Champion_Name": champ_name
                        }

                if reconstructed_draft:
                    # Если оппонент играл за синих
                    if not is_our_blue:
                        blue_picks_seq = {s: reconstructed_draft[s] for s in [7, 10, 11, 18, 19] if s in reconstructed_draft}
                        for seq, pick_data in blue_picks_seq.items():
                            champ = pick_data.get("Champion_Name")
                            phase = blue_pick_seq_to_phase.get(seq)
                            if champ and champ != "N/A" and phase:
                                opponent_stats_agg[opponent_team_name]["temp_priority"]['Blue'][champ][phase] += 1
                    # Если оппонент играл за красных
                    else:
                        red_picks_seq = {s: reconstructed_draft[s] for s in [8, 9, 12, 17, 20] if s in reconstructed_draft}
                        for seq, pick_data in red_picks_seq.items():
                            champ = pick_data.get("Champion_Name")
                            phase = red_pick_seq_to_phase.get(seq)
                            if champ and champ != "N/A" and phase:
                                opponent_stats_agg[opponent_team_name]["temp_priority"]['Red'][champ][phase] += 1

            history_list.append({
                "Date": game.get("Date", "N/A"), "Patch": game.get("Patch", "N/A"),
                "Blue_Team_Name": game.get("Blue_Team_Name", "N/A"), "Red_Team_Name": game.get("Red_Team_Name", "N/A"),
                "Result": result, "Duration": game.get("Duration", "N/A"), "Game_ID": game_id, "details": details,
                "B_Bans_HTML": " ".join(filter(None, bb)), "R_Bans_HTML": " ".join(filter(None, rb)),
                "B_Picks_HTML": " ".join(filter(None, bp)), "R_Picks_HTML": " ".join(filter(None, rp))
            })

        final_player_stats = {} 
        
        for display_name in PLAYER_DISPLAY_ORDER:
            if display_name in player_stats_agg:
                champs_stats = player_stats_agg[display_name]
                formatted_champs = {}
                
                for champ, s in champs_stats.items():
                    g = s['games']
                    if g > 0:
                        formatted_champs[champ] = {
                            'games': g,
                            'wins': s['wins'],
                            'k': s['k'], 'd': s['d'], 'a': s['a'], 'dmg': s['dmg'], 'cs': s['cs'],
                            'win_rate': round((s['wins'] / g) * 100, 1),
                            'kda': round((s['k'] + s['a']) / max(1, s['d']), 1),
                            'avg_dmg': s['dmg'] // g,
                            'avg_cs': round(s['cs'] / g, 1),
                            'icon_html': get_champion_icon_html(champ, champion_data, 30, 30)
                        }
                
                if formatted_champs:
                    final_player_stats[display_name] = formatted_champs

        # НОВОЕ: Форматируем стату оппонентов (Игроки + Приоритеты)
        final_opponent_stats = {}
        opponent_teams_list = []
        
        for opp_team, team_data in opponent_stats_agg.items():
            opponent_teams_list.append(opp_team)
            final_opponent_stats[opp_team] = {
                "players": {},
                "priority": {}
            }
            
            # Форматируем игроков
            for player_name, champs_stats in team_data["players"].items():
                formatted_champs = {}
                for champ, s in champs_stats.items():
                    g = s['games']
                    if g > 0:
                        formatted_champs[champ] = {
                            'games': g, 'wins': s['wins'],
                            'k': s['k'], 'd': s['d'], 'a': s['a'], 'dmg': s['dmg'], 'cs': s['cs'],
                            'win_rate': round((s['wins'] / g) * 100, 1),
                            'kda': round((s['k'] + s['a']) / max(1, s['d']), 1),
                            'avg_dmg': s['dmg'] // g,
                            'avg_cs': round(s['cs'] / g, 1),
                            'icon_html': get_champion_icon_html(champ, champion_data, 30, 30)
                        }
                if formatted_champs:
                    final_opponent_stats[opp_team]["players"][player_name] = formatted_champs
            
            # Форматируем приоритеты
            blue_prio_list, blue_max_val = format_priority_picks(team_data["temp_priority"]["Blue"], champion_data)
            red_prio_list, red_max_val = format_priority_picks(team_data["temp_priority"]["Red"], champion_data)
            
            final_opponent_stats[opp_team]["priority"] = {
                "blue": blue_prio_list,
                "red": red_prio_list,
                "blue_max_picks": blue_max_val,
                "red_max_picks": red_max_val,
                "blue_phases": sorted(list(set(blue_pick_seq_to_phase.values())), key=lambda x: int(x.split('-')[0][1:])),
                "red_phases": sorted(list(set(red_pick_seq_to_phase.values())), key=lambda x: int(x.split('-')[0][1:]))
            }
                    
        opponent_teams_list.sort() 

        return overall_stats, history_list, final_player_stats, final_opponent_stats, opponent_teams_list, champion_data

    except Exception as e:
        log_message(f"Aggregation Error: {e}")
        import traceback
        log_message(traceback.format_exc()) 
        return {}, [], {}, {}, [], {}
    finally:
        if conn: conn.close()
# --- Блок для тестирования ---
if __name__ == '__main__':
    print("Testing scrims_logic...")
    from dotenv import load_dotenv
    load_dotenv()
    GRID_API_KEY = os.getenv("GRID_API_KEY")
    if not GRID_API_KEY: print("FATAL: GRID_API_KEY not found.")
    else:
        print("Testing data aggregation...")
        test_overall, test_hist, test_player, test_champ_data = aggregate_scrim_data(time_filter="All Time", side_filter='all')
        print("\n--- Overall Stats ---"); print(test_overall)
        print("\n--- Sample History (First 2) ---"); print(test_hist[:2])
        print("\n--- Sample Player Stats (First Player) ---")
        first_player = PLAYER_DISPLAY_ORDER[0] if PLAYER_DISPLAY_ORDER else None
        if first_player: print(f"Stats for {first_player}: {dict(test_player.get(first_player, {}))}")
        else: print("No players in display order.")
        print("\nTest complete.")

def get_game_replay_data(game_id):
    """Возвращает данные в формате, который ожидает JS плеер."""
    import json
    conn = get_db_connection()
    if not conn:
        return {"timeline": [], "events": []}
    
    try:
        cursor = conn.cursor()
        # 1. Загружаем позиции
        cursor.execute("""
            SELECT timestamp_seconds, positions_json 
            FROM player_positions_snapshots 
            WHERE game_id = ? 
            ORDER BY timestamp_seconds ASC
        """, (str(game_id),))
        
        rows = cursor.fetchall()
        timeline = []
        for row in rows:
            # Превращаем секунды в миллисекунды, так как JS плеер работает с ms
            timeline.append({
                "timestamp": row[0] * 1000, 
                "positions": json.loads(row[1])
            })

        # 2. Загружаем события (пока отдаем пустой список, если таблицы нет)
        events = []
        try:
            cursor.execute("SELECT timestamp_ms, event_type, victim_id FROM game_events WHERE game_id = ?", (str(game_id),))
            e_rows = cursor.fetchall()
            for er in e_rows:
                events.append({
                    "timestamp_ms": er[0],
                    "event_type": er[1],
                    "victim_id": er[2]
                })
        except:
            pass # Если таблицы событий еще нет, просто пропускаем

        return {
            "timeline": timeline,
            "events": events
        }
    except Exception as e:
        print(f"Error fetching replay data: {e}")
        return {"timeline": [], "events": [], "error": str(e)}
    finally:
        conn.close()

def log_message(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"{timestamp} :: {msg}")

def process_replay_to_db(game_id, timeline_data, summary_data):
    """
    Парсит NDJSON, сохраняет позиции И события объектов (башни/монстры).
    """
    conn = None
    for attempt in range(5):
        try:
            conn = get_db_connection()
            conn.execute("PRAGMA busy_timeout = 60000")
            break
        except sqlite3.OperationalError:
            log_message(f"Database busy, retrying connection {attempt+1}/5...")
            time.sleep(1)

    if not conn:
        log_message(f"!!! Could not get DB connection for game {game_id} after retries.")
        return

    cursor = conn.cursor()

    # 1. Маппинг участников для определения команд и чемпионов
    participants = summary_data.get("participants", [])
    pid_to_info = {}
    for p in participants:
        pid = p.get("participantId")
        if pid is not None:
            pid_to_info[pid] = {
                "puuid": p.get("puuid"),
                "champion": p.get("championName", "Unknown"),
                "teamId": p.get("teamId")
            }

    try:
        if isinstance(timeline_data, bytes):
            timeline_data = timeline_data.decode('utf-8')
        
        lines = timeline_data.strip().split('\n')
        log_message(f"--- Processing {len(lines)} lines for scrim {game_id} ---")
        
        timeline_records = []
        snapshot_map = {} 
        objective_events_list = []

        for line in lines:
            if not line.strip(): continue
            try:
                snapshot = json.loads(line)
            except: continue

            schema = snapshot.get("rfc461Schema")
            game_time_ms = snapshot.get("gameTime") or snapshot.get("timestamp")
            
            # --- ПАРСИНГ ПОЗИЦИЙ ---
            if schema == "stats_update" and game_time_ms is not None:
                current_participants = snapshot.get("participants", [])
                t_sec = int(game_time_ms / 1000)
                snapshot_positions = []

                for p_data in current_participants:
                    p_id = p_data.get("participantID")
                    pos = p_data.get("position")
                    if p_id is not None and pos and 'x' in pos and 'z' in pos:
                        info = pid_to_info.get(p_id, {})
                        puuid = p_data.get("puuid") or info.get("puuid")
                        
                        timeline_records.append((
                            str(game_id), int(game_time_ms), int(p_id),
                            str(puuid) if puuid else f"unknown_{p_id}",
                            int(pos['x']), int(pos['z']),
                            datetime.now(timezone.utc).isoformat()
                        ))
                        snapshot_positions.append({
                            "participantID": p_id,
                            "championName": info.get("champion", "Unknown"),
                            "teamId": info.get("teamId", 0),
                            "x": float(pos['x']), "z": float(pos['z'])
                        })
                
                if snapshot_positions and t_sec not in snapshot_map:
                    snapshot_map[t_sec] = snapshot_positions

            # --- ПАРСИНГ СОБЫТИЙ ОБЪЕКТОВ (Драконы, Башни) ---
            elif schema in ["epic_monster_kill", "building_destroyed"] or snapshot.get("eventType") == "ELITE_MONSTER_KILL":
                extracted = extract_single_event(snapshot, game_id, pid_to_info)
                if extracted:
                    objective_events_list.append(extracted)

        # 2. Запись всех данных в одной транзакции
        cursor.execute("BEGIN IMMEDIATE TRANSACTION")
        try:
            # Очистка старых данных
            cursor.execute("DELETE FROM player_positions_timeline WHERE game_id = ?", (str(game_id),))
            cursor.execute("DELETE FROM player_positions_snapshots WHERE game_id = ?", (str(game_id),))
            cursor.execute("DELETE FROM objective_events WHERE game_id = ?", (str(game_id),))

            # Сохранение позиций
            if timeline_records:
                cursor.executemany("""
                    INSERT INTO player_positions_timeline 
                    (game_id, timestamp_ms, participant_id, player_puuid, pos_x, pos_z, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, timeline_records)

            # Сохранение снапшотов
            snapshot_insert_data = [
                (str(game_id), ts, json.dumps(pos_list), datetime.now(timezone.utc).isoformat())
                for ts, pos_list in snapshot_map.items()
            ]
            if snapshot_insert_data:
                cursor.executemany("""
                    INSERT INTO player_positions_snapshots 
                    (game_id, timestamp_seconds, positions_json, last_updated)
                    VALUES (?, ?, ?, ?)
                """, snapshot_insert_data)

            # Сохранение событий объектов (ИСПРАВЛЕНО ДЛЯ event_type)
            if objective_events_list:
                to_insert_obj = []
                for e in objective_events_list:
                    obj_t = e['objective_type']
                    # Определяем event_type для базы
                    if obj_t == 'TOWER': evt_t = 'BUILDING_KILL'
                    else: evt_t = 'ELITE_MONSTER_KILL'

                    to_insert_obj.append((
                        str(e['game_id']), int(e['timestamp_ms']), evt_t, obj_t,
                        e.get('objective_subtype'), e.get('team_id'),
                        e.get('killer_participant_id'), e.get('lane')
                    ))
                
                cursor.executemany("""
                    INSERT INTO objective_events 
                    (game_id, timestamp_ms, event_type, objective_type, objective_subtype, team_id, killer_participant_id, lane)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, to_insert_obj)

            conn.commit()
            log_message(f"DONE G:{game_id}: Saved {len(timeline_records)} pos & {len(objective_events_list)} events.")
        except Exception as e:
            conn.rollback()
            raise e

    except Exception as e:
        log_message(f"!!! ERROR in process_replay_to_db (G:{game_id}): {e}")
    finally:
        if conn: conn.close()

def extract_single_event(snapshot, game_id, pid_to_info):
    """Вспомогательная функция для парсинга одного события из строки NDJSON."""
    schema = snapshot.get("rfc461Schema")
    game_time = snapshot.get("gameTime") or snapshot.get("timestamp")
    
    # Эпические монстры
    if schema == "epic_monster_kill" or snapshot.get("eventType") == "ELITE_MONSTER_KILL":
        monster_type = snapshot.get("monsterType")
        obj_type, obj_subtype = None, None
        
        if monster_type == 'dragon':
            obj_type = 'DRAGON'
            obj_subtype = snapshot.get("dragonType", "UNKNOWN").upper()
        elif monster_type == 'baron': obj_type, obj_subtype = 'BARON', 'BARON'
        elif monster_type == 'riftHerald': obj_type, obj_subtype = 'HERALD', 'HERALD'
        elif monster_type == 'VoidGrub': obj_type, obj_subtype = 'VOIDGRUB', 'VOIDGRUB'
        
        if obj_type:
            killer_pid = snapshot.get("killer") or snapshot.get("killerId")
            team_id = snapshot.get("killerTeamId")
            if not team_id and killer_pid:
                team_id = pid_to_info.get(killer_pid, {}).get("teamId")
            
            return {
                "game_id": game_id, "timestamp_ms": game_time, 
                "objective_type": obj_type, "objective_subtype": obj_subtype, 
                "team_id": team_id, "killer_participant_id": killer_pid, "lane": None
            }

    # Башни
    elif schema == "building_destroyed":
        if snapshot.get("buildingType") == "turret":
            owner_team = snapshot.get("teamID")
            killer_team = 200 if owner_team == 100 else 100
            return {
                "game_id": game_id, "timestamp_ms": game_time,
                "objective_type": "TOWER", 
                "objective_subtype": snapshot.get("turretTier", "UNKNOWN").upper(),
                "team_id": killer_team, 
                "killer_participant_id": snapshot.get("lastHitter"), 
                "lane": snapshot.get("lane", "UNKNOWN").upper()
            }
    return None