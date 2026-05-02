import sqlite3
import json
import os
from database import get_db_connection

LEAGUES_FILE = 'leagues.json'

def load_leagues():
    if os.path.exists(LEAGUES_FILE):
        with open(LEAGUES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def get_filter_options():
    conn = get_db_connection()
    options = {
        'patches': [],
        'teams': [],
        'champions': [],
        'game_numbers': [],
        'leagues': list(load_leagues().keys()),
        'pick_positions': ['B1', 'B2', 'B3', 'B4', 'B5', 'R1', 'R2', 'R3', 'R4', 'R5']
    }
    
    if not conn:
        return options

    try:
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT Patch FROM tournament_games WHERE Patch IS NOT NULL AND Patch != "N/A" ORDER BY Patch DESC')
        options['patches'] = [row['Patch'] for row in cursor.fetchall()]
        
        cursor.execute('''
            SELECT DISTINCT Blue_Team_Name as team FROM tournament_games WHERE Blue_Team_Name NOT IN ('UnknownBlue', 'Blue Team', 'N/A')
            UNION
            SELECT DISTINCT Red_Team_Name as team FROM tournament_games WHERE Red_Team_Name NOT IN ('UnknownRed', 'Red Team', 'N/A')
            ORDER BY team ASC
        ''')
        options['teams'] = [row['team'] for row in cursor.fetchall()]
        
        cursor.execute('SELECT DISTINCT Sequence_Number FROM tournament_games WHERE Sequence_Number IS NOT NULL ORDER BY Sequence_Number ASC')
        raw_seq_nums = [row['Sequence_Number'] for row in cursor.fetchall()]
        processed_nums = set()
        
        for num in raw_seq_nums:
            if num <= 0:
                processed_nums.add(1)
            else:
                processed_nums.add(num)
                
        options['game_numbers'] = sorted(list(processed_nums))
        
        pick_slots = [7, 8, 9, 10, 11, 12, 17, 18, 19, 20]
        union_query = " UNION ".join([f"SELECT DISTINCT Draft_Action_{i}_ChampName as champ FROM tournament_games" for i in pick_slots])
        cursor.execute(union_query)
        
        champs = set()
        for row in cursor.fetchall():
            if row['champ'] and row['champ'] != 'N/A':
                champs.add(row['champ'])
                
        options['champions'] = sorted(list(champs))
        
    except sqlite3.Error as e:
        print(f"Error fetching filter options: {e}")
    finally:
        conn.close()
        
    return options

def get_filtered_drafts(filters):
    conn = get_db_connection()
    if not conn:
        return []

    leagues_data = load_leagues()
    drafts = []

    try:
        cursor = conn.cursor()
        query = "SELECT * FROM tournament_games WHERE 1=1"
        params = []

        # Функция для удаления пустых строк из списков
        def clean_list(val):
            if not val:
                return []
            
            result = []
            if isinstance(val, list):
                for v in val:
                    if v:
                        if str(v).strip() != "":
                            result.append(v)
            else:
                if str(val).strip() != "":
                    result.append(val)
                    
            return result

        def apply_league_filter(leagues_list, exclude=False):
            nonlocal query
            
            cleaned_leagues = clean_list(leagues_list)
            if not cleaned_leagues:
                return
                
            all_teams = []
            for l_name in cleaned_leagues:
                if l_name in leagues_data:
                    all_teams.extend(leagues_data[l_name])
                    
            if all_teams:
                placeholders = ','.join(['?'] * len(all_teams))
                if exclude:
                    query += f" AND (Blue_Team_Name NOT IN ({placeholders}) AND Red_Team_Name NOT IN ({placeholders}))"
                else:
                    query += f" AND (Blue_Team_Name IN ({placeholders}) OR Red_Team_Name IN ({placeholders}))"
                params.extend(all_teams * 2)

        selected_leagues = filters.get('leagues', [])
        excluded_leagues = filters.get('leagues_exclude', [])
        
        if selected_leagues:
            apply_league_filter(selected_leagues, exclude=False)
            
        if excluded_leagues:
            apply_league_filter(excluded_leagues, exclude=True)

        def apply_list_filter(field_name, values, exclude=False):
            nonlocal query
            
            cleaned_values = clean_list(values)
            if not cleaned_values:
                return
                
            placeholders = ','.join(['?'] * len(cleaned_values))
            
            if exclude:
                op = "NOT IN"
            else:
                op = "IN"
                
            if field_name == 'team':
                query += f" AND (Blue_Team_Name {op} ({placeholders}) OR Red_Team_Name {op} ({placeholders}))"
                params.extend(cleaned_values * 2)
            else:
                query += f" AND {field_name} {op} ({placeholders})"
                params.extend(cleaned_values)

        apply_list_filter('Patch', filters.get('patches'))
        apply_list_filter('team', filters.get('teams'))
        apply_list_filter('team', filters.get('teams_exclude'), exclude=True)

        raw_game_numbers = filters.get('game_number')
        cleaned_game_numbers = clean_list(raw_game_numbers)
        
        if cleaned_game_numbers:
            try:
                g_nums = []
                for n in cleaned_game_numbers:
                    g_nums.append(int(n))
                    
                placeholders = ','.join(['?'] * len(g_nums))
                
                if 1 in g_nums:
                    query += f" AND (Sequence_Number IN ({placeholders}) OR Sequence_Number = 0)"
                else:
                    query += f" AND Sequence_Number IN ({placeholders})"
                    
                params.extend(g_nums)
            except (ValueError, TypeError):
                pass

        blue_pick_slots = [7, 10, 11, 18, 19]
        red_pick_slots = [8, 9, 12, 17, 20]
        all_pick_slots = blue_pick_slots + red_pick_slots

        PICK_MAP = {
            'B1': 7, 'B2': 10, 'B3': 11, 'B4': 18, 'B5': 19,
            'R1': 8, 'R2': 9, 'R3': 12, 'R4': 17, 'R5': 20
        }

        selected_champs = clean_list(filters.get('champions', []))
        excluded_champs = clean_list(filters.get('champions_exclude', []))
        selected_side = filters.get('side')
        selected_pick_pos = filters.get('pick_position')

        def build_champ_query(champs, exclude=False):
            nonlocal query
            if not champs:
                return
                
            slots = all_pick_slots
            
            if selected_pick_pos and selected_pick_pos in PICK_MAP:
                slots = [PICK_MAP[selected_pick_pos]]
            elif selected_side == 'Blue':
                slots = blue_pick_slots
            elif selected_side == 'Red':
                slots = red_pick_slots

            if exclude:
                op = "!="
                conj = " AND "
            else:
                op = "="
                conj = " OR "
            
            sub_conditions = []
            for champ in champs:
                champ_cond_list = []
                for i in slots:
                    champ_cond_list.append(f"Draft_Action_{i}_ChampName {op} ?")
                    
                champ_cond = conj.join(champ_cond_list)
                sub_conditions.append(f"({champ_cond})")
                params.extend([champ] * len(slots))
            
            if exclude:
                final_conj = " AND "
            else:
                final_conj = " OR "
                
            query += f" AND ({final_conj.join(sub_conditions)})"

        if selected_champs:
            build_champ_query(selected_champs, exclude=False)
            
        if excluded_champs:
            build_champ_query(excluded_champs, exclude=True)
            
        # Добавляем фильтр по результату только если выбран чемпион
        selected_result = filters.get('result')
        if selected_result and selected_result != "":
            if selected_champs:
                res_slots = all_pick_slots
                if selected_pick_pos and selected_pick_pos in PICK_MAP:
                    res_slots = [PICK_MAP[selected_pick_pos]]
                
                result_conditions = []
                for i in res_slots:
                    if i in blue_pick_slots:
                        side_of_slot = 'Blue'
                    else:
                        side_of_slot = 'Red'
                        
                    if selected_result == "Win":
                        target_side = side_of_slot
                    else:
                        if side_of_slot == 'Blue':
                            target_side = 'Red'
                        else:
                            target_side = 'Blue'
                            
                    for c in selected_champs:
                        result_conditions.append(f"(Draft_Action_{i}_ChampName = ? AND Winner_Side = ?)")
                        params.extend([c, target_side])
                
                if result_conditions:
                    joined_conditions = ' OR '.join(result_conditions)
                    query += f" AND ({joined_conditions})"

        query += ' ORDER BY "Date" DESC, Sequence_Number ASC LIMIT 50'
        
        cursor.execute(query, params)
        rows = cursor.fetchall()

        for row in rows:
            game = dict(row)
            
            draft_actions_dict = {}
            for i in range(1, 21):
                c_name = game.get(f"Draft_Action_{i}_ChampName")
                if not c_name:
                    c_name = "N/A"
                draft_actions_dict[i] = {"Champion_Name": c_name}
            
            winner = game.get('Winner_Side', 'Unknown')
            blue_team = game.get('Blue_Team_Name', 'Unknown Blue')
            red_team = game.get('Red_Team_Name', 'Unknown Red')
            
            if winner == "Blue":
                blue_res = "WIN"
                red_res = "LOSS"
            elif winner == "Red":
                blue_res = "LOSS"
                red_res = "WIN"
            else:
                blue_res = "-"
                red_res = "-"

            blue_map_champs = set()
            for role in ["TOP", "JGL", "MID", "BOT", "SUP"]:
                c = game.get(f"Blue_{role}_Champ")
                if c and c != "N/A":
                    blue_map_champs.add(c)
                    
            first_pick_champs = []
            for seq in [7, 10, 11, 18, 19]:
                c = game.get(f"Draft_Action_{seq}_ChampName")
                if c and c != "N/A":
                    first_pick_champs.append(c)
                    
            m_count = 0
            for c in first_pick_champs:
                if c in blue_map_champs:
                    m_count += 1
            
            is_swapped = False
            if m_count < 3 and len(first_pick_champs) > 0:
                is_swapped = True

            if is_swapped:
                d_left_team = red_team
                d_right_team = blue_team
                d_left_res = red_res
                d_right_res = blue_res
            else:
                d_left_team = blue_team
                d_right_team = red_team
                d_left_res = blue_res
                d_right_res = red_res

            raw_seq = game.get('Sequence_Number', 0)
            if raw_seq <= 0:
                display_num = 1
            else:
                display_num = raw_seq

            drafts.append({
                "game_id": game.get('Game_ID'),
                "date": game.get('Date', 'N/A'),
                "patch": game.get('Patch', 'N/A'),
                "display_game_number": display_num,
                "draft_left_team_tag": d_left_team,
                "draft_right_team_tag": d_right_team,
                "draft_left_result": d_left_res,
                "draft_right_result": d_right_res,
                "draft_actions_dict": draft_actions_dict
            })

    except sqlite3.Error as e:
        print(f"Error filtering drafts: {e}")
    finally:
        conn.close()
        
    return drafts