



from flask import Flask, render_template, request, jsonify
import requests
import json
import uuid
import threading
import sqlite3
from datetime import datetime, timedelta
import schedule
import time
import logging
import time as time_module

app = Flask(__name__)
application = app

# --- Superset API Config ---
SUPERSET_EXECUTE_URL = "https://superset.your-company.example/api/v1/sqllab/execute/"
SUPERSET_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Referer": "Placeholder"
    "Origin": "Placeholder
    "X-CSRFToken": Placeholder
    "Cookie": "Placeholder
}
SUPERSET_DB_ID = 2

# Create a session for maintaining cookies
def create_superset_session():
    session = requests.Session()
    session.headers.update(SUPERSET_HEADERS)
    return session

SUPERSET_SESSION = create_superset_session()

# --- Query Template ---
QUERY_TEMPLATE = """
WITH filtered_deals AS (
    SELECT DISTINCT id, name
    FROM ads.dim_rams_deals_history
    WHERE date_key BETWEEN '{date_from}' AND '{date_to}' 
      {deal_name_condition}
)
SELECT 
    f.date_key,
    d.id AS deal_id,
    d.name AS deal_name,
    SUM(f.is_impressed) AS imps,
    SUM(CASE WHEN f.ad_fetch_source IS NULL AND f.event_type = 'candidate' THEN 1 ELSE 0 END) AS real_demand_returned,
    SUM(CASE WHEN f.ad_fetch_source IS NOT NULL AND f.event_type = 'candidate' THEN 1 ELSE 0 END) AS Bid_Cache_demand,
    SUM(f.num_opportunities) AS oppos,
    SUM(f.is_selected) AS total_bids,
    SUM(CASE WHEN f.is_impressed IS NULL AND f.event_type = 'candidate' AND f.ad_fetch_source IS NULL THEN 1 ELSE 0 END) AS Loss_Totals,
    (SUM(f.is_impressed) * 1.000 / NULLIF(SUM(f.num_opportunities), 0)) * 100.00 AS fill_rate,
    SUM(CASE WHEN f.event_type = 'demand_request' THEN 1 ELSE 0 END) AS Requests,
    (SUM(f.is_impressed) * 1.000 / NULLIF(SUM(f.is_selected), 0)) * 100.00 AS render_rate,
    (SUM(f.is_selected) * 1.000 / NULLIF(SUM(CASE WHEN f.ad_fetch_source IS NULL AND f.event_type = 'candidate' THEN 1 ELSE 0 END), 0)) * 100.00 AS win_rate,
    SUM(CASE WHEN f.event_type = 'candidate' THEN 1 ELSE 0 END) AS candidates
FROM advertising.demand_funnel f
JOIN filtered_deals d
  ON CONTAINS(f.deal_id, d.id)   -- pruning without full UNNEST
WHERE f.date_key BETWEEN '{date_from}' AND '{date_to}'
  AND CONTAINS(f.demand_systems, 'PARTNER_AD_SERVER_VIDEO')
GROUP BY f.date_key, d.id, d.name
ORDER BY f.date_key ASC, d.id ASC
"""

# --- Loss Reason Query Template ---
QUERY_TEMPLATE_LOSS_REASON = """
SELECT 
    f.date_key,
    d AS deal_id,
    h.name AS deal_name,
    f.filter_reason,
    SUM(CASE WHEN ad_fetch_source IS null and is_impressed is NOT NULL Then 1 Else 0 END) AS imps,
    SUM(CASE WHEN f.ad_fetch_source IS NULL AND f.event_type = 'candidate' THEN 1 ELSE 0 END) AS real_demand_returned,
    SUM(f.num_opportunities) AS oppos,
    SUM(f.is_selected) AS total_bids,
    SUM(CASE WHEN f.is_impressed IS NULL AND f.event_type = 'candidate' AND f.ad_fetch_source IS NULL THEN 1 ELSE 0 END) AS Loss_Totals,
    (SUM(f.is_impressed) * 1.000 / NULLIF(SUM(f.num_opportunities), 0)) * 100.00 AS fill_rate,
    SUM(CASE WHEN f.event_type = 'demand_request' THEN 1 ELSE 0 END) AS Requests,
    (SUM(f.is_impressed) * 1.000 / NULLIF(SUM(f.is_selected), 0)) * 100.00 AS render_rate,
    (SUM(CASE WHEN f.is_selected IS NOT NULL AND f.ad_fetch_source IS NULL THEN 1 ELSE 0 END) * 1.0
      / NULLIF(SUM(CASE WHEN f.ad_fetch_source IS NULL AND f.event_type = 'candidate' THEN 1 ELSE 0 END), 0)) * 100.0 AS win_rate,    
      SUM(CASE WHEN f.event_type = 'candidate' THEN 1 ELSE 0 END) AS candidates
FROM
    advertising.demand_funnel f
CROSS JOIN UNNEST(f.deal_id) AS t(d)
LEFT JOIN ads.dim_rams_deals_history h
    ON d = h.id
    AND f.date_key = h.date_key
WHERE 
    f.date_key BETWEEN '{date_from}' AND '{date_to}'
    AND CONTAINS(f.demand_systems, 'PARTNER_AD_SERVER_VIDEO')
    {deal_name_condition}
    AND f.filter_reason IS NOT NULL
GROUP BY 
    f.date_key, d, h.name, f.filter_reason
ORDER BY 
    f.date_key ASC, d ASC
"""



# --- In-Memory Job Tracking ---
JOBS = {}

# --- SQLite Cache ---
DB_PATH = "query_cache.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Check if table exists and has the new normalized structure
    c.execute("PRAGMA table_info(query_results)")
    columns = [row[1] for row in c.fetchall()]
    
    # If old schema or missing columns, recreate table
    if 'deal_name' not in columns or 'imps' not in columns:
        c.execute("DROP TABLE IF EXISTS query_results")
        print("Recreated database schema for normalized deal storage")
    
    c.execute("""
    CREATE TABLE IF NOT EXISTS query_results (
        date_key TEXT,
        deal_id TEXT,
        deal_name TEXT,
        imps REAL,
        oppos REAL,
        total_bids REAL,
        fill_rate REAL,
        render_rate REAL,
        requests REAL,
        original_data JSON,
        created_at TIMESTAMP,
        real_demand_returned REAL,
        Loss_Totals REAL,
        win_rate REAL,
        candidates REAL,
        PRIMARY KEY (deal_name, date_key)
    )
    """)
    
    # Create indexes for better query performance
    c.execute("CREATE INDEX IF NOT EXISTS idx_deal_name ON query_results(deal_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_date_key ON query_results(date_key)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_deal_date ON query_results(deal_name, date_key)")
    
    conn.commit()
    conn.close()

init_db()

import sqlite3
LOSS_REASON_DB_PATH = "loss_reason_cache.db"

# --- Loss Reason Cache ---
def init_loss_reason_db():
    conn = sqlite3.connect(LOSS_REASON_DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS loss_reason_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_key TEXT,
            deal_id TEXT,
            deal_name TEXT,
            filter_reason TEXT,
            imps REAL,
            real_demand_returned REAL,
            oppos REAL,
            total_bids REAL,
            Loss_Totals REAL,
            fill_rate REAL,
            Requests REAL,
            render_rate REAL,
            win_rate REAL,
            candidates REAL,
            original_data TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_loss_reason_db()

def save_loss_reason_results_to_db(deal_name, date_from, date_to, rows):
    conn = sqlite3.connect(LOSS_REASON_DB_PATH)
    c = conn.cursor()
    inserted_count = 0
    duplicate_count = 0
    for row in rows:
        # Use a unique constraint on (date_key, deal_id, deal_name, filter_reason) if needed
        c.execute('''
            SELECT COUNT(*) FROM loss_reason_results WHERE date_key=? AND deal_id=? AND deal_name=? AND filter_reason=?
        ''', (row.get('date_key'), row.get('deal_id'), row.get('deal_name'), row.get('filter_reason')))
        if c.fetchone()[0] == 0:
            c.execute('''
                INSERT INTO loss_reason_results (
                    date_key, deal_id, deal_name, filter_reason, imps, real_demand_returned, oppos, total_bids, Loss_Totals, fill_rate, Requests, render_rate, win_rate, candidates, original_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                row.get('date_key'),
                row.get('deal_id'),
                row.get('deal_name'),
                row.get('filter_reason'),
                row.get('imps'),
                row.get('real_demand_returned'),
                row.get('oppos'),
                row.get('total_bids'),
                row.get('Loss_Totals'),
                row.get('fill_rate'),
                row.get('Requests'),
                row.get('render_rate'),
                row.get('win_rate'),
                row.get('candidates'),
                json.dumps(row)
            ))
            inserted_count += 1
        else:
            duplicate_count += 1
    conn.commit()
    conn.close()
    print(f"[LOSS_REASON_CACHE] Inserted {inserted_count} new rows, skipped {duplicate_count} duplicates")
    return inserted_count

# --- Automated Data Pulling ---
def get_all_cached_deals():
    """Get list of all deals we have cached data for"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT deal_name 
        FROM query_results 
        WHERE deal_name IS NOT NULL AND deal_name != ''
        ORDER BY deal_name
    """)
    
    deals = [row[0] for row in cursor.fetchall()]
    conn.close()
    return deals

def pull_previous_day_data():
    """Pull data for all known deals for the previous day"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    # Get all deals we have data for
    all_deals = get_all_cached_deals()
    
    if not all_deals:
        print(f"[SCHEDULER] No deals found in cache to update")
        return
    
    # Check if yesterday's data already exists for most deals
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(DISTINCT deal_name) 
        FROM query_results 
        WHERE date_key = ? AND deal_name IS NOT NULL AND deal_name != ''
    """, (yesterday,))
    existing_count = cursor.fetchone()[0]
    conn.close()
    
    # If we already have data for >80% of deals, skip the run
    coverage_percent = (existing_count / len(all_deals)) * 100 if all_deals else 0
    if coverage_percent > 80:
        print(f"[SCHEDULER] {coverage_percent:.1f}% coverage exists for {yesterday}, skipping")
        return
    
    print(f"[SCHEDULER] Starting daily data pull for {len(all_deals)} deals for {yesterday}")
    
    # Split deals into batches to avoid overwhelming the system
    batch_size = 10
    deal_batches = [all_deals[i:i + batch_size] for i in range(0, len(all_deals), batch_size)]
    
    total_new_rows = 0
    successful_batches = 0
    
    for batch_num, deal_batch in enumerate(deal_batches, 1):
        print(f"[SCHEDULER] Processing batch {batch_num}/{len(deal_batches)}: {len(deal_batch)} deals")
        
        # Build SQL query for this batch
        like_conditions = [f"name LIKE '%{deal}%'" for deal in deal_batch]
        deal_name_condition = f"AND ({' OR '.join(like_conditions)})"
        
        sql_query = QUERY_TEMPLATE.format(
            date_from=yesterday,
            date_to=yesterday,  # Single day only
            deal_name_condition=deal_name_condition
        )
        
        payload = {
            "database_id": SUPERSET_DB_ID,
            "schema": "advertising",
            "sql": sql_query,
            "runAsync": False
        }
        
        try:
            resp = requests.post(
                SUPERSET_EXECUTE_URL,
                headers=SUPERSET_HEADERS,
                data=json.dumps(payload),
                timeout=1800  # 15 minutes per batch
            )
            
            if resp.status_code == 200:
                resp_data = resp.json()
                rows = resp_data.get("data", [])
                print(f"[SCHEDULER] Batch {batch_num} completed: {len(rows)} rows")
                
                # Save to cache
                inserted_count = save_results_to_db(f"scheduler_batch_{batch_num}", deal_batch, yesterday, yesterday, rows)
                total_new_rows += inserted_count
                successful_batches += 1
                
                # Small delay between batches to be nice to the server
                time.sleep(2)
                
            else:
                print(f"[SCHEDULER] Batch {batch_num} failed: {resp.status_code} - {resp.text}")
                
        except Exception as e:
            print(f"[SCHEDULER] Batch {batch_num} exception: {e}")
            continue
    
    print(f"[SCHEDULER] Daily pull completed: {successful_batches}/{len(deal_batches)} batches successful, {total_new_rows} new rows cached")

# Scheduler disabled to prevent caching conflicts
# def run_scheduler():
#     """Background thread to run scheduled tasks"""
#     # Schedule daily data pull at 11 AM
#     schedule.every().day.at("11:00").do(pull_previous_day_data)
#     
#     # Fallback: retry every hour after 11 AM if no deals found
#     for hour in range(12, 24):  # 12 PM to 11 PM
#         schedule.every().day.at(f"{hour:02d}:00").do(pull_previous_day_data)
#     
#     print("[SCHEDULER] Scheduler started - daily data pull at 11:00 AM with hourly fallbacks")
#     
#     while True:
#         schedule.run_pending()
#         time.sleep(60)  # Check every minute

# # Start scheduler in background thread
# scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
# scheduler_thread.start()
print("[SCHEDULER] Scheduler disabled to prevent caching conflicts")

def save_results_to_db(job_id, deal_names, date_from, date_to, rows):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    inserted_count = 0
    duplicate_count = 0
    
    # Insert each row individually with extracted fields for better querying
    for row in rows:
        date_key = str(row.get("date_key"))
        deal_name = str(row.get("deal_name", ""))
        deal_id = str(row.get("deal_id", ""))
        
        # Only insert if we have valid deal information
        if deal_name and deal_name.strip():
            try:
                c.execute("""
                    INSERT INTO query_results(
                        date_key, deal_id, deal_name, imps, oppos, total_bids, 
                        fill_rate, render_rate, requests, original_data, created_at,
                        real_demand_returned, Loss_Totals, win_rate, candidates
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    date_key,
                    deal_id,
                    deal_name.strip(),
                    float(row.get('imps', 0) or 0),
                    float(row.get('oppos', 0) or 0),
                    float(row.get('total_bids', 0) or 0),
                    float(row.get('fill_rate', 0) or 0),
                    float(row.get('render_rate', 0) or 0),
                    float(row.get('Requests', 0) or 0),
                    json.dumps(row),
                    datetime.now(),
                    float(row.get('real_demand_returned', 0) or 0),
                    float(row.get('Loss_Totals', 0) or 0),
                    float(row.get('win_rate', 0) or 0),
                    float(row.get('candidates', 0) or 0)
                ))
                inserted_count += 1
            except sqlite3.IntegrityError:
                # This combination already exists, skip it
                duplicate_count += 1
                continue
    
    conn.commit()
    conn.close()
    
    print(f"[CACHE] Inserted {inserted_count} new rows, skipped {duplicate_count} duplicates")
    return inserted_count

def get_cached_results(deal_names, date_from, date_to):
    """Get cached results for multiple deal names with smart incremental querying"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # For multiple deal names, we need to query each one and combine results
    all_cached_rows = []
    missing_ranges = []
    
    # Check what date range we have cached for ALL deals that match the search patterns
    for deal_name in deal_names:
        # Check what date range we have cached for ALL deals that match this pattern
        c.execute("""
            SELECT MIN(date_key) as cached_min, MAX(date_key) as cached_max, COUNT(*) as cached_count,
                   COUNT(DISTINCT deal_name) as unique_deals
            FROM query_results 
            WHERE deal_name LIKE ?
        """, (f'%{deal_name}%',))
        
        cache_info = c.fetchone()
        cached_min, cached_max, cached_count, unique_deals = cache_info if cache_info else (None, None, 0, 0)
        
        if cached_count == 0:
            print(f"[CACHE] Deal '{deal_name}': No cached data found - need full query")
            missing_ranges.append({
                'deal_name': deal_name,
                'date_from': date_from,
                'date_to': date_to,
                'reason': 'no_cache'
            })
            continue    
        
        print(f"[DEBUG] Cache coverage check for '{deal_name}':")
        print(f"[DEBUG] - Cached range: {cached_min} to {cached_max}")
        print(f"[DEBUG] - Requested range: {date_from} to {date_to}")
        print(f"[DEBUG] - Unique deals in cache: {unique_deals}")
        
        # Check for different overlap scenarios
        cache_covers_request = (cached_min <= date_from and cached_max >= date_to)
        
        # For search patterns that should match multiple deals, be more conservative
        # Only force fresh queries for broad patterns that are likely to match multiple deals
        is_broad_search = (
            len(deal_name) <= 10 or  # Short search terms like "Alpha", "Studio"
            deal_name.lower() in ['alpha', 'studio', 'media', 'network', 'partner'] or  # Known multi-deal patterns
            '_' not in deal_name  # No underscores suggests a broad search
        )
        
        if cache_covers_request and unique_deals > 1:
            # Full coverage with multiple deals - use cache completely
            print(f"[DEBUG] - Full coverage available with {unique_deals} deals")
            
            # Get the requested data from cache
            c.execute("""
                SELECT date_key, deal_id, deal_name, imps, oppos, total_bids, 
                       fill_rate, render_rate, requests, real_demand_returned, 
                       Loss_Totals, win_rate, candidates
                FROM query_results 
                WHERE deal_name LIKE ? AND date_key >= ? AND date_key <= ?
                ORDER BY date_key ASC
            """, (f'%{deal_name}%', date_from, date_to))
            
            rows = c.fetchall()
            
            # Convert to the expected format using direct columns
            for row in rows:
                all_cached_rows.append({
                    'date_key': row[0],
                    'deal_id': row[1], 
                    'deal_name': row[2],
                    'imps': float(row[3] or 0),
                    'oppos': float(row[4] or 0),
                    'total_bids': float(row[5] or 0),
                    'fill_rate': float(row[6] or 0),
                    'render_rate': float(row[7] or 0),
                    'Requests': float(row[8] or 0),
                    'real_demand_returned': float(row[9] or 0),
                    'Loss_Totals': float(row[10] or 0),
                    'win_rate': float(row[11] or 0),
                    'candidates': float(row[12] or 0)
                })
        elif cache_covers_request and unique_deals == 1 and is_broad_search:
            # Only one deal in cache for a broad search - likely incomplete, need to query for all matching deals
            print(f"[DEBUG] - Only one deal in cache for broad search '{deal_name}', need to query for all matching deals")
            missing_ranges.append({
                'deal_name': deal_name,
                'date_from': date_from,
                'date_to': date_to,
                'reason': 'incomplete_cache'
            })
            continue
        elif cache_covers_request and unique_deals == 1 and not is_broad_search:
            # Only one deal in cache for specific search - this is probably correct
            print(f"[DEBUG] - Only one deal in cache for specific search '{deal_name}', using cache")
            
            # Get the requested data from cache
            c.execute("""
                SELECT date_key, deal_id, deal_name, imps, oppos, total_bids, 
                       fill_rate, render_rate, requests, real_demand_returned, 
                       Loss_Totals, win_rate, candidates
                FROM query_results 
                WHERE deal_name LIKE ? AND date_key >= ? AND date_key <= ?
                ORDER BY date_key ASC
            """, (f'%{deal_name}%', date_from, date_to))
            
            rows = c.fetchall()
            
            # Check if we have sufficient coverage (allow some gaps)
            if rows:
                    date_set = set(row[0] for row in rows)
                    current_date = datetime.strptime(date_from, '%Y-%m-%d')
                    end_date = datetime.strptime(date_to, '%Y-%m-%d')
                    
                    total_days = (end_date - current_date).days + 1
                    available_days = len(date_set)
                    coverage_percent = (available_days / total_days) * 100
                    
                    # Allow gaps if we have at least 80% coverage
                    if coverage_percent >= 80:
                        print(f"[CACHE] Deal '{deal_name}': Using cache with {coverage_percent:.1f}% coverage ({available_days}/{total_days} days)")
                        
                        # Convert to the expected format using direct columns
                        for row in rows:
                            all_cached_rows.append({
                                'date_key': row[0],
                                'deal_id': row[1], 
                                'deal_name': row[2],
                                'imps': float(row[3] or 0),
                                'oppos': float(row[4] or 0),
                                'total_bids': float(row[5] or 0),
                                'fill_rate': float(row[6] or 0),
                                'render_rate': float(row[7] or 0),
                                'Requests': float(row[8] or 0),
                                'real_demand_returned': float(row[9] or 0),
                                'Loss_Totals': float(row[10] or 0),
                                'win_rate': float(row[11] or 0),
                                'candidates': float(row[12] or 0)
                            })
                    else:
                        print(f"[CACHE] Deal '{deal_name}': Insufficient coverage ({coverage_percent:.1f}%) - need fresh query")
                        missing_ranges.append({
                            'deal_name': deal_name,
                            'date_from': date_from,
                            'date_to': date_to,
                            'reason': 'insufficient_coverage'
                        })
            else:
                print(f"[CACHE] Deal '{deal_name}': No data found in requested range")
                missing_ranges.append({
                    'deal_name': deal_name,
                    'date_from': date_from,
                    'date_to': date_to,
                    'reason': 'no_data_in_range'
                })
                
        else:
            # Partial coverage - check for incremental query opportunities
            cache_overlap = not (cached_max < date_from or cached_min > date_to)
            
            if cache_overlap:
                print(f"[DEBUG] - Partial coverage detected")
                
                # Get overlapping cached data
                overlap_start = max(date_from, cached_min)
                overlap_end = min(date_to, cached_max)
                
                c.execute("""
                    SELECT date_key, deal_id, deal_name, imps, oppos, total_bids, 
                           fill_rate, render_rate, requests, real_demand_returned, 
                           Loss_Totals, win_rate, candidates
                    FROM query_results 
                    WHERE deal_name LIKE ? AND date_key >= ? AND date_key <= ?
                    ORDER BY date_key ASC
                """, (f'%{deal_name}%', overlap_start, overlap_end))
                
                cached_rows = c.fetchall()
                overlap_days = len(cached_rows)
                requested_days = (datetime.strptime(date_to, '%Y-%m-%d') - datetime.strptime(date_from, '%Y-%m-%d')).days + 1
                coverage_percent = (overlap_days / requested_days) * 100
                
                print(f"[DEBUG] - Overlap coverage: {coverage_percent:.1f}% ({overlap_days}/{requested_days} days)")
                
                # If we have significant overlap (>50%), use incremental approach
                if coverage_percent >= 50:
                    print(f"[CACHE] Deal '{deal_name}': Using incremental query approach")
                    
                    # Add cached data to results using direct columns
                    for row in cached_rows:
                        all_cached_rows.append({
                            'date_key': row[0],
                            'deal_id': row[1], 
                            'deal_name': row[2],
                            'imps': float(row[3] or 0),
                            'oppos': float(row[4] or 0),
                            'total_bids': float(row[5] or 0),
                            'fill_rate': float(row[6] or 0),
                            'render_rate': float(row[7] or 0),
                            'Requests': float(row[8] or 0),
                            'real_demand_returned': float(row[9] or 0),
                            'Loss_Totals': float(row[10] or 0),
                            'win_rate': float(row[11] or 0),
                            'candidates': float(row[12] or 0)
                        })
                    
                    # Determine missing date ranges to query
                    if date_from < cached_min:
                        # Need data before cached range
                        end_before = (datetime.strptime(cached_min, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
                        missing_ranges.append({
                            'deal_name': deal_name,
                            'date_from': date_from,
                            'date_to': end_before,
                            'reason': 'before_cache'
                        })
                    
                    if date_to > cached_max:
                        # Need data after cached range
                        start_after = (datetime.strptime(cached_max, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
                        missing_ranges.append({
                            'deal_name': deal_name,
                            'date_from': start_after,
                            'date_to': date_to,
                            'reason': 'after_cache'
                        })
                else:
                    print(f"[CACHE] Deal '{deal_name}': Coverage too low for incremental approach")
                    missing_ranges.append({
                        'deal_name': deal_name,
                        'date_from': date_from,
                        'date_to': date_to,
                        'reason': 'low_coverage'
                    })
            else:
                print(f"[DEBUG] - No overlap between cache and requested range")
                missing_ranges.append({
                    'deal_name': deal_name,
                    'date_from': date_from,
                    'date_to': date_to,
                    'reason': 'no_overlap'
                })
    
    conn.close()
    
    # Return results: cached data and list of ranges that need fresh queries
    return all_cached_rows, missing_ranges

def run_incremental_superset_query(job_id, missing_ranges, original_date_from, original_date_to, original_search_terms):
    """Run multiple targeted queries for missing date ranges and combine with cached data"""
    print(f"[JOB {job_id}] Running incremental queries for {len(missing_ranges)} missing ranges...")
    
    all_new_rows = []
    cached_rows = JOBS[job_id].get("cached_rows", [])
    
    for i, range_info in enumerate(missing_ranges):
        deal_name = range_info['deal_name']
        date_from = range_info['date_from']
        date_to = range_info['date_to']
        reason = range_info['reason']
        
        print(f"[JOB {job_id}] Query {i+1}/{len(missing_ranges)}: {deal_name} from {date_from} to {date_to} ({reason})")
        
        # Build targeted SQL query for this missing range using original search terms
        
        # Use original search terms instead of specific deal name
        if len(original_search_terms) == 1:
            deal_name_condition = f"AND name LIKE '%{original_search_terms[0]}%'"
        else:
            like_conditions = [f"name LIKE '%{term}%'" for term in original_search_terms]
            deal_name_condition = f"AND ({' OR '.join(like_conditions)})"
        
        sql_query = QUERY_TEMPLATE.format(
            date_from=date_from,
            date_to=date_to,
            deal_name_condition=deal_name_condition
        )
        
        print(f"[DEBUG] Incremental query {i+1}: {sql_query[:200]}...")
        
        payload = {
            "database_id": SUPERSET_DB_ID,
            "schema": "advertising",
            "sql": sql_query,
            "runAsync": False
        }
        
        try:
            print(f"[DEBUG] Making incremental request with session")
            print(f"[DEBUG] Payload: {json.dumps(payload)[:200]}...")
            resp = SUPERSET_SESSION.post(
                SUPERSET_EXECUTE_URL,
                data=json.dumps(payload),
                timeout=1800  # 15 minutes per query
            )
            
            # Add a small delay between requests to avoid rate limiting
            if i < len(missing_ranges) - 1:  # Don't delay after the last request
                time_module.sleep(2)
            
            if resp.status_code == 200:
                resp_data = resp.json()
                rows = resp_data.get("data", [])
                print(f"[JOB {job_id}] Query {i+1} completed: {len(rows)} new rows")
                all_new_rows.extend(rows)
                
                # Save incremental results to cache
                save_results_to_db(job_id, [deal_name], date_from, date_to, rows)
            else:
                print(f"[JOB {job_id}] Query {i+1} failed: {resp.text}")
                JOBS[job_id] = {"status": "error", "message": f"Incremental query failed: {resp.text}"}
                return
                
        except Exception as e:
            print(f"[JOB {job_id}] Query {i+1} exception: {e}")
            JOBS[job_id] = {"status": "error", "message": f"Incremental query exception: {e}"}
            return
        
        # Update progress
        progress = ((i + 1) / len(missing_ranges)) * 100
        JOBS[job_id]["progress"] = progress
    
    # Combine cached data with new data
    combined_rows = cached_rows + all_new_rows
    
    # Sort by date for consistent ordering
    combined_rows.sort(key=lambda x: (x.get('date_key', ''), x.get('deal_name', '')))
    
    # Determine columns safely
    if combined_rows:
        columns = list(combined_rows[0].keys())
    else:
        columns = []
    
    JOBS[job_id] = {
        "status": "success",
        "columns": columns,
        "rows": combined_rows,
        "progress": 100,
        "incremental_summary": {
            "cached_rows": len(cached_rows),
            "new_rows": len(all_new_rows),
            "total_rows": len(combined_rows),
            "queries_executed": len(missing_ranges)
        }
    }
    
    print(f"[JOB {job_id}] Incremental query completed: {len(cached_rows)} cached + {len(all_new_rows)} new = {len(combined_rows)} total rows")

# --- Background Job ---
# --- Background Job ---
def run_superset_query(job_id, deal_names, date_from, date_to, sql_query):
    JOBS[job_id] = {"status": "running", "progress": 0}
    print(f"[JOB {job_id}] Running query for deals '{', '.join(deal_names)}' from {date_from} to {date_to}...")

    payload = {
        "database_id": SUPERSET_DB_ID,
        "schema": "advertising",
        "sql": sql_query,
        "runAsync": False
    }

    try:
        print(f"[DEBUG] Making main request with headers: {SUPERSET_HEADERS}")
        print(f"[DEBUG] Payload: {json.dumps(payload)[:200]}...")
        resp = requests.post(
            SUPERSET_EXECUTE_URL,
            headers=SUPERSET_HEADERS,
            data=json.dumps(payload),
            timeout=1800  # 20 minutes
        )
    except requests.exceptions.Timeout:
        JOBS[job_id] = {"status": "error", "message": "Query timed out after 20 minutes"}
        return

    if resp.status_code != 200:
        JOBS[job_id] = {"status": "error", "message": f"Superset HTTP error: {resp.text}"}
        return

    try:
        resp_data = resp.json()
        rows = resp_data.get("data", [])

        # --- Determine columns safely ---
        if "columns" in resp_data and resp_data["columns"]:
            columns = [col.get("name") or col.get("column_name") for col in resp_data["columns"]]
        elif rows:
            columns = list(rows[0].keys())  # fallback if columns missing
        else:
            columns = []

        # Save to cache
        save_results_to_db(job_id, deal_names, date_from, date_to, rows)

        JOBS[job_id] = {
            "status": "success",
            "columns": columns,
            "rows": rows,
            "progress": 100
        }
        print(f"[JOB {job_id}] Completed successfully. {len(rows)} rows cached.")
        
    except Exception as e:
        JOBS[job_id] = {
            "status": "error",
            "message": f"Failed to parse Superset JSON: {e}\nRaw: {resp.text[:500]}"
        }
        print(f"[JOB {job_id}] JSON parse failed: {e}")

def complete_cache_for_missing_deals(deal_names, date_from, date_to, returned_rows):
    """Check if all deals in the results are fully cached, and fill any gaps"""
    if not returned_rows:
        return
    
    # Extract unique deals from returned data
    returned_deals = set()
    for row in returned_rows:
        deal_name = row.get('deal_name', '')
        if deal_name:
            returned_deals.add(deal_name)
    
    if not returned_deals:
        return
    
    print(f"[CACHE COMPLETION] Checking cache completeness for {len(returned_deals)} deals")
    
    # Check cache coverage for each deal
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    missing_data = []
    
    for deal_name in returned_deals:
        # Check what dates we have cached for this deal
        c.execute("""
            SELECT date_key FROM query_results 
            WHERE deal_name = ? AND date_key >= ? AND date_key <= ?
            ORDER BY date_key
        """, (deal_name, date_from, date_to))
        
        cached_dates = {row[0] for row in c.fetchall()}
        
        # Generate all dates in the range
        current_date = datetime.strptime(date_from, '%Y-%m-%d')
        end_date = datetime.strptime(date_to, '%Y-%m-%d')
        
        missing_dates = []
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            if date_str not in cached_dates:
                missing_dates.append(date_str)
            current_date += timedelta(days=1)
        
        if missing_dates:
            print(f"[CACHE COMPLETION] Deal '{deal_name}' missing {len(missing_dates)} dates: {missing_dates}")
            missing_data.append({
                'deal_name': deal_name,
                'missing_dates': missing_dates
            })
    
    conn.close()
    
    if missing_data:
        print(f"[CACHE COMPLETION] Running query to fill {len(missing_data)} deals with missing data")
        
        # Build the SQL query to get missing data
        missing_deal_names = [item['deal_name'] for item in missing_data]
        missing_date_conditions = []
        
        for item in missing_data:
            for date in item['missing_dates']:
                missing_date_conditions.append(f"(h.name = '{item['deal_name']}' AND f.date_key = '{date}')")
        
        if missing_date_conditions:
            date_condition = " OR ".join(missing_date_conditions)
            
            sql_query = f"""
            SELECT 
                f.date_key,
                d AS deal_id,
                h.name AS deal_name,
                SUM(f.is_impressed) AS imps,
                SUM(CASE WHEN f.ad_fetch_source IS NULL AND f.event_type = 'candidate' THEN 1 ELSE 0 END) AS real_demand_returned,
                SUM(f.num_opportunities) AS oppos,
                SUM(f.is_selected) AS total_bids,
                SUM(CASE WHEN f.is_impressed IS NULL AND f.event_type = 'candidate' AND f.ad_fetch_source IS NULL THEN 1 ELSE 0 END) AS Loss_Totals,
                (SUM(f.is_impressed) * 1.000 / NULLIF(SUM(f.num_opportunities), 0)) * 100.00 AS fill_rate,
                SUM(CASE WHEN f.event_type = 'demand_request' THEN 1 ELSE 0 END) AS Requests,
                (SUM(f.is_impressed) * 1.000 / NULLIF(SUM(f.is_selected), 0)) * 100.00 AS render_rate,
                (SUM(f.is_selected) * 1.000 / NULLIF(SUM(CASE WHEN f.ad_fetch_source IS NULL AND f.event_type = 'candidate' THEN 1 ELSE 0 END), 0)) * 100.00 AS win_rate,
                SUM(CASE WHEN f.event_type = 'candidate' THEN 1 ELSE 0 END) AS candidates
            FROM advertising.demand_funnel f
            CROSS JOIN UNNEST(f.deal_id) AS t(d)
            LEFT JOIN ads.dim_rams_deals_history h ON d = h.id AND f.date_key = h.date_key
            WHERE f.date_key >= '{date_from}' AND f.date_key <= '{date_to}'
            AND CONTAINS(f.demand_systems, 'PARTNER_AD_SERVER_VIDEO')
            AND ({date_condition})
            GROUP BY f.date_key, d, h.name
            ORDER BY f.date_key ASC, d ASC
            """
            
            # Run the query in background
            job_id = str(uuid.uuid4())
            threading.Thread(target=run_superset_query, args=(job_id, missing_deal_names, date_from, date_to, sql_query), daemon=True).start()
            print(f"[CACHE COMPLETION] Started background job {job_id} to fill missing data")
    else:
        print(f"[CACHE COMPLETION] All deals are fully cached for the requested date range")

def check_for_missing_deals_data(deal_names, date_from, date_to):
    """Check if any deals have missing data for specific dates in the range"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    missing_data = []
    
    for deal_name in deal_names:
        # Check what dates we have cached for this deal
        c.execute("""
            SELECT date_key FROM query_results 
            WHERE deal_name = ? AND date_key >= ? AND date_key <= ?
            ORDER BY date_key
        """, (deal_name, date_from, date_to))
        
        cached_dates = {row[0] for row in c.fetchall()}
        
        # Generate all dates in the range
        current_date = datetime.strptime(date_from, '%Y-%m-%d')
        end_date = datetime.strptime(date_to, '%Y-%m-%d')
        
        missing_dates = []
        while current_date <= end_date:
            date_str = current_date.strftime('%Y-%m-%d')
            if date_str not in cached_dates:
                missing_dates.append(date_str)
            current_date += timedelta(days=1)
        
        if missing_dates:
            print(f"[CACHE CHECK] Deal '{deal_name}' missing {len(missing_dates)} dates: {missing_dates}")
            missing_data.append({
                'deal_name': deal_name,
                'missing_dates': missing_dates
            })
    
    conn.close()
    return missing_data

def build_targeted_sql_query(missing_deals_data, date_from, date_to):
    """Build SQL query targeting only missing deal+date combinations"""
    # Extract unique deal names and missing dates
    deal_names = list(set(item['deal_name'] for item in missing_deals_data))
    missing_dates = set()
    for item in missing_deals_data:
        missing_dates.update(item['missing_dates'])
    
    if not missing_dates:
        return None
    
    # Build deal name condition
    if len(deal_names) == 1:
        deal_name_condition = f"AND name LIKE '%{deal_names[0]}%'"
    else:
        like_conditions = [f"name LIKE '%{name}%'" for name in deal_names]
        deal_name_condition = f"AND ({' OR '.join(like_conditions)})"
    
    # Build date condition for missing dates only
    date_conditions = [f"f.date_key = '{date}'" for date in sorted(missing_dates)]
    date_condition = " OR ".join(date_conditions)
    
    sql_query = f"""
    WITH filtered_deals AS (
        SELECT DISTINCT id, name
        FROM ads.dim_rams_deals_history
        WHERE date_key BETWEEN '{date_from}' AND '{date_to}' 
          {deal_name_condition}
    )
    SELECT 
        f.date_key,
        d.id AS deal_id,
        d.name AS deal_name,
        SUM(f.is_impressed) AS imps,
        SUM(CASE WHEN f.ad_fetch_source IS NULL AND f.event_type = 'candidate' THEN 1 ELSE 0 END) AS real_demand_returned,
        SUM(CASE WHEN f.ad_fetch_source IS NOT NULL AND f.event_type = 'candidate' THEN 1 ELSE 0 END) AS Bid_Cache_demand,
        SUM(f.num_opportunities) AS oppos,
        SUM(f.is_selected) AS total_bids,
        SUM(CASE WHEN f.is_impressed IS NULL AND f.event_type = 'candidate' AND f.ad_fetch_source IS NULL THEN 1 ELSE 0 END) AS Loss_Totals,
        (SUM(f.is_impressed) * 1.000 / NULLIF(SUM(f.num_opportunities), 0)) * 100.00 AS fill_rate,
        SUM(CASE WHEN f.event_type = 'demand_request' THEN 1 ELSE 0 END) AS Requests,
        (SUM(f.is_impressed) * 1.000 / NULLIF(SUM(f.is_selected), 0)) * 100.00 AS render_rate,
        (SUM(f.is_selected) * 1.000 / NULLIF(SUM(CASE WHEN f.ad_fetch_source IS NULL AND f.event_type = 'candidate' THEN 1 ELSE 0 END), 0)) * 100.00 AS win_rate,
        SUM(CASE WHEN f.event_type = 'candidate' THEN 1 ELSE 0 END) AS candidates
    FROM advertising.demand_funnel f
    JOIN filtered_deals d
      ON CONTAINS(f.deal_id, d.id)   -- pruning without full UNNEST
    WHERE f.date_key BETWEEN '{date_from}' AND '{date_to}'
      AND CONTAINS(f.demand_systems, 'PARTNER_AD_SERVER_VIDEO')
      AND ({date_condition})
    GROUP BY f.date_key, d.id, d.name
    ORDER BY f.date_key ASC, d.id ASC
    """
    
    return sql_query

# --- Flask Routes ---
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/loss-reason-query", methods=["GET"])
def loss_reason_query_page():
    return render_template("loss_reason_query.html")

@app.route("/filter-reason-overview", methods=["GET"])
def filter_reason_overview_page():
    return render_template("filter_reason_overview.html")

@app.route("/dod-graphs", methods=["GET"])
def dod_graphs():
    return render_template("dod_graphs.html")

@app.route("/breakout-analysis", methods=["GET"])
def breakout_analysis():
    return render_template("breakout_analysis.html")

@app.route("/aggregate-view", methods=["GET"])
def aggregate_view():
    return render_template("aggregate_view.html")

@app.route("/run_query", methods=["POST"])
def run_query():
    # Get multiple deal names
    deal_names = request.form.getlist("deal_name")
    deal_names = [name.strip() for name in deal_names if name.strip()]
    
    # Get date range parameters
    date_from = request.form.get("date_from", "2025-01-07").strip()
    date_to = request.form.get("date_to", "").strip()
    
    if not deal_names:
        return jsonify({"error": "Please enter at least one deal name!"}), 400
    
    # Set default date_to if not provided
    if not date_to:
        date_to = date_from

    # Check cache first
    cached_rows, missing_ranges = get_cached_results(deal_names, date_from, date_to)
    
    if not missing_ranges:
        # Full cache hit - return cached data
        job_id = str(uuid.uuid4())
        JOBS[job_id] = {
            "status": "success",
            "columns": list(cached_rows[0].keys()) if cached_rows else [],
            "rows": cached_rows,
            "progress": 100
        }
        print(f"[JOB {job_id}] Returned from cache with {len(cached_rows)} rows.")
        return jsonify({"job_id": job_id, "cached": True})
    
    elif cached_rows and missing_ranges:
        # Partial cache hit - use incremental query approach
        print(f"[CACHE] Using incremental query: {len(cached_rows)} cached rows, {len(missing_ranges)} missing ranges")
        
        # Launch background job for missing ranges only
        job_id = str(uuid.uuid4())
        
        # Store cached data in job for later combination
        JOBS[job_id] = {
            "status": "running", 
            "progress": 0,
            "cached_rows": cached_rows,
            "missing_ranges": missing_ranges,
            "original_search_terms": deal_names  # Pass the original search terms
        }
        
        threading.Thread(target=run_incremental_superset_query, args=(job_id, missing_ranges, date_from, date_to, deal_names), daemon=True).start()
        return jsonify({"job_id": job_id, "cached": False, "incremental": True})
    
    else:
        # No cache hit - query everything
        print(f"[CACHE] No cache available, querying full range")
        
        # Check for any deals that might be partially cached and need completion
        missing_deals_data = check_for_missing_deals_data(deal_names, date_from, date_to)
        
        if missing_deals_data:
            print(f"[CACHE COMPLETION] Found {len(missing_deals_data)} deals with missing data, will query for specific missing dates")
            
            # Count total missing dates to decide approach
            total_missing_dates = sum(len(item['missing_dates']) for item in missing_deals_data)
            print(f"[CACHE COMPLETION] Total missing dates: {total_missing_dates}")
            
            # If too many missing dates (>5), use full range query instead of targeted query
            if total_missing_dates > 5:
                print(f"[CACHE COMPLETION] Too many missing dates ({total_missing_dates}), using full range query")
                if len(deal_names) == 1:
                    deal_name_condition = f"AND name LIKE '%{deal_names[0]}%'"
                else:
                    like_conditions = [f"name LIKE '%{name}%'" for name in deal_names]
                    deal_name_condition = f"AND ({' OR '.join(like_conditions)})"
                
                sql_query = QUERY_TEMPLATE.format(
                    date_from=date_from,
                    date_to=date_to,
                    deal_name_condition=deal_name_condition
                )
            else:
                # Use targeted query for missing dates only
                sql_query = build_targeted_sql_query(missing_deals_data, date_from, date_to)
                if sql_query:
                    print(f"[CACHE COMPLETION] Using targeted query for missing dates only")
                else:
                    # Fallback to full range query
                    if len(deal_names) == 1:
                        deal_name_condition = f"AND name LIKE '%{deal_names[0]}%'"
                    else:
                        like_conditions = [f"name LIKE '%{name}%'" for name in deal_names]
                        deal_name_condition = f"AND ({' OR '.join(like_conditions)})"
                    
                    sql_query = QUERY_TEMPLATE.format(
                        date_from=date_from,
                        date_to=date_to,
                        deal_name_condition=deal_name_condition
                    )
        else:
            # Build SQL query for full range
            
            # Build deal name condition for multiple names
            if len(deal_names) == 1:
                deal_name_condition = f"AND name LIKE '%{deal_names[0]}%'"
            else:
                like_conditions = [f"name LIKE '%{name}%'" for name in deal_names]
                deal_name_condition = f"AND ({' OR '.join(like_conditions)})"
            
            sql_query = QUERY_TEMPLATE.format(
                date_from=date_from,
                date_to=date_to,
                deal_name_condition=deal_name_condition
            )

        print(f"[DEBUG] Generated SQL query:")
        print(f"[DEBUG] {sql_query}")

        # Launch background job
        job_id = str(uuid.uuid4())
        threading.Thread(target=run_superset_query, args=(job_id, deal_names, date_from, date_to, sql_query), daemon=True).start()

        return jsonify({"job_id": job_id, "cached": False})

@app.route("/dod_data", methods=["POST"])
def get_dod_data():
    # Get parameters
    deal_names = request.form.getlist("deal_name")
    deal_names = [name.strip() for name in deal_names if name.strip()]
    date_from = request.form.get("date_from", "2025-07-01").strip()
    date_to = request.form.get("date_to", "2025-08-03").strip()
    metric = request.form.get("metric", "imps").strip()
    dual_metric = request.form.get("dual_metric", "").strip()  # New parameter for dual metrics
    
    if not deal_names:
        return jsonify({"status": "error", "message": "Please enter at least one deal name!"}), 400
    
    # For DoD graphs, we should only handle ONE deal at a time
    if len(deal_names) > 1:
        return jsonify({
            "status": "error", 
            "message": f"DoD graphs work best with a single deal. You provided {len(deal_names)} deals: {', '.join(deal_names)}. Please select only one deal for meaningful trend analysis."
        }), 400
    
    single_deal = deal_names[0]
    
    # Try to get data from cache first - but we need to find the right cached query
    # The cache might have this deal as part of a multi-deal query
    cached_rows = get_cached_results_for_single_deal(single_deal, date_from, date_to)
    
    if cached_rows:
        print(f"[DoD] Using cached data for single deal '{single_deal}': {len(cached_rows)} rows")
        # Aggregate cached data by date for DoD calculations
        from collections import defaultdict
        
        daily_aggregates = defaultdict(lambda: {
            'imps': 0, 'oppos': 0, 'total_bids': 0, 'requests': 0
        })
        
        # Group by date_key and sum metrics for this specific deal
        # No need for duplicate detection since we now have clean normalized data
        for row in cached_rows:
            date_key = row.get('date_key', '')
            
            if date_key:
                # Use the correct field names from cached data
                daily_aggregates[date_key]['imps'] += float(row.get('imps', 0) or 0)
                daily_aggregates[date_key]['oppos'] += float(row.get('oppos', 0) or 0)
                daily_aggregates[date_key]['total_bids'] += float(row.get('total_bids', 0) or 0)
                daily_aggregates[date_key]['requests'] += float(row.get('Requests', 0) or 0)
        
        # Calculate rates and sort by date
        aggregated_rows = []
        for date_key in sorted(daily_aggregates.keys()):
            agg = daily_aggregates[date_key]
            # Skip days with no data for this deal
            if agg['imps'] == 0 and agg['oppos'] == 0 and agg['total_bids'] == 0:
                continue
                
            fill_rate = (agg['imps'] / agg['oppos'] * 100) if agg['oppos'] > 0 else 0
            render_rate = (agg['imps'] / agg['total_bids'] * 100) if agg['total_bids'] > 0 else 0
            
            aggregated_rows.append({
                'date_key': date_key,
                'imps': agg['imps'],
                'oppos': agg['oppos'], 
                'total_bids': agg['total_bids'],
                'fill_rate': fill_rate,
                'render_rate': render_rate,
                'requests': agg['requests']
            })
        
        # Calculate DoD changes from cached data
        dod_data = []
        for i, row in enumerate(aggregated_rows):
            if i == 0:
                # First day - no previous day to compare
                dod_entry = {
                    "date_key": row["date_key"],
                    "value": row[metric] or 0,
                    "dod_change": 0,
                    "dod_percent": 0
                }
                # Add dual metric if specified
                if dual_metric and dual_metric in row:
                    dod_entry["dual_value"] = row[dual_metric] or 0
                    dod_entry["dual_dod_change"] = 0
                    dod_entry["dual_dod_percent"] = 0
                dod_data.append(dod_entry)
            else:
                current_value = row[metric] or 0
                previous_value = aggregated_rows[i-1][metric] or 0
                
                dod_change = current_value - previous_value
                dod_percent = (dod_change / previous_value * 100) if previous_value != 0 else 0
                
                dod_entry = {
                    "date_key": row["date_key"],
                    "value": current_value,
                    "dod_change": dod_change,
                    "dod_percent": dod_percent
                }
                
                # Add dual metric calculations if specified
                if dual_metric and dual_metric in row:
                    dual_current_value = row[dual_metric] or 0
                    dual_previous_value = aggregated_rows[i-1][dual_metric] or 0
                    
                    dual_dod_change = dual_current_value - dual_previous_value
                    dual_dod_percent = (dual_dod_change / dual_previous_value * 100) if dual_previous_value != 0 else 0
                    
                    dod_entry["dual_value"] = dual_current_value
                    dod_entry["dual_dod_change"] = dual_dod_change
                    dod_entry["dual_dod_percent"] = dual_dod_percent
                
                dod_data.append(dod_entry)
        
        return jsonify({
            "status": "success",
            "data": dod_data,
            "metric": metric,
            "dual_metric": dual_metric if dual_metric else None,
            "deal_names": [single_deal],
            "cached": True
        })
    
    else:
        # No cache - need to query Superset for single deal
        print(f"[DoD] No cache found, querying Superset for single deal '{single_deal}', {date_from} to {date_to}")
        
        # SQL for day-over-day data for single deal
        dod_sql = f"""
        SELECT 
            f.date_key,
            SUM(f.is_impressed) AS imps,
            SUM(f.num_opportunities) AS oppos,
            SUM(f.is_selected) AS total_bids,
            (SUM(f.is_impressed) * 1.000 / NULLIF(SUM(f.num_opportunities), 0)) * 100.00 AS fill_rate,
            (SUM(f.is_impressed) * 1.000 / NULLIF(SUM(f.is_selected), 0)) * 100.00 AS render_rate,
            SUM(CASE WHEN f.event_type = 'demand_request' THEN 1 ELSE 0 END) AS requests
        FROM
            advertising.demand_funnel f
        CROSS JOIN UNNEST(f.deal_id) AS t(d)
        LEFT JOIN ads.dim_rams_deals_history h
            ON CAST(d AS VARCHAR) = CAST(h.id AS VARCHAR)
            AND f.date_key = h.date_key
        WHERE 
            f.date_key >= '{date_from}'
            AND f.date_key <= '{date_to}'
            AND CONTAINS(f.demand_systems, 'PARTNER_AD_SERVER_VIDEO')
            AND h.name = '{single_deal}'
        GROUP BY 
            f.date_key
        ORDER BY 
            f.date_key ASC
        """
        
        payload = {
            "database_id": SUPERSET_DB_ID,
            "schema": "advertising",
            "sql": dod_sql,
            "runAsync": False
        }
        
        try:
            resp = requests.post(
                SUPERSET_EXECUTE_URL,
                headers=SUPERSET_HEADERS,
                data=json.dumps(payload),
                timeout=1800
            )
            
            if resp.status_code == 200:
                data = resp.json()
                rows = data.get("data", [])
                
                # Calculate DoD changes
                dod_data = []
                for i, row in enumerate(rows):
                    if i == 0:
                        # First day - no previous day to compare
                        dod_entry = {
                            "date_key": row["date_key"],
                            "value": row[metric] or 0,
                            "dod_change": 0,
                            "dod_percent": 0
                        }
                        # Add dual metric if specified
                        if dual_metric and dual_metric in row:
                            dod_entry["dual_value"] = row[dual_metric] or 0
                            dod_entry["dual_dod_change"] = 0
                            dod_entry["dual_dod_percent"] = 0
                        dod_data.append(dod_entry)
                    else:
                        current_value = row[metric] or 0
                        previous_value = rows[i-1][metric] or 0
                        
                        dod_change = current_value - previous_value
                        dod_percent = (dod_change / previous_value * 100) if previous_value != 0 else 0
                        
                        dod_entry = {
                            "date_key": row["date_key"],
                            "value": current_value,
                            "dod_change": dod_change,
                            "dod_percent": dod_percent
                        }
                        
                        # Add dual metric calculations if specified
                        if dual_metric and dual_metric in row:
                            dual_current_value = row[dual_metric] or 0
                            dual_previous_value = rows[i-1][dual_metric] or 0
                            
                            dual_dod_change = dual_current_value - dual_previous_value
                            dual_dod_percent = (dual_dod_change / dual_previous_value * 100) if dual_previous_value != 0 else 0
                            
                            dod_entry["dual_value"] = dual_current_value
                            dod_entry["dual_dod_change"] = dual_dod_change
                            dod_entry["dual_dod_percent"] = dual_dod_percent
                        
                        dod_data.append(dod_entry)
                
                return jsonify({
                    "status": "success",
                    "data": dod_data,
                    "metric": metric,
                    "dual_metric": dual_metric if dual_metric else None,
                    "deal_names": [single_deal],
                    "cached": False
                })
            else:
                return jsonify({"status": "error", "message": resp.text})
                
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

def get_cached_results_for_single_deal(deal_name, date_from, date_to):
    """Get cached results for a specific deal within the date range"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Query the normalized table directly for this deal and date range
        cursor.execute("""
            SELECT date_key, deal_id, deal_name, imps, oppos, total_bids, 
                   fill_rate, render_rate, requests, original_data
            FROM query_results 
            WHERE deal_name = ? AND date_key >= ? AND date_key <= ?
            ORDER BY date_key ASC
        """, (deal_name, date_from, date_to))
        
        rows = cursor.fetchall()
        conn.close()
        
        if rows:
            # Convert to the expected format
            filtered_data = []
            for row in rows:
                filtered_data.append({
                    'date_key': row[0],
                    'deal_id': row[1], 
                    'deal_name': row[2],
                    'imps': float(row[3] or 0),
                    'oppos': float(row[4] or 0),
                    'total_bids': float(row[5] or 0),
                    'fill_rate': float(row[6] or 0),
                    'render_rate': float(row[7] or 0),
                    'Requests': float(row[8] or 0)  # Note: capital R for compatibility
                })
            
            print(f"[DoD] Found {len(filtered_data)} cached rows for deal '{deal_name}' (normalized)")
            return filtered_data
                
        return None
    except Exception as e:
        print(f"Error getting cached results for single deal: {e}")
        return None

app.add_url_rule(
    "/get_yesterday_data",
    view_func=pull_previous_day_data,
    methods=["POST", "GET"]
)

# --- API route to fetch all loss reason results as JSON ---
@app.route("/api/loss-reason-results", methods=["GET"])
def get_loss_reason_results():
    """API endpoint to fetch all loss reason results as JSON for frontend rendering."""
    conn = sqlite3.connect(LOSS_REASON_DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT date_key, deal_id, deal_name, filter_reason, imps, real_demand_returned, oppos, total_bids, Loss_Totals, fill_rate, Requests, render_rate, win_rate, candidates
        FROM loss_reason_results
        ORDER BY date_key DESC, deal_name ASC, filter_reason ASC
    """)
    rows = c.fetchall()
    columns = ["date_key", "deal_id", "deal_name", "filter_reason", "imps", "real_demand_returned", "oppos", "total_bids", "Loss_Totals", "fill_rate", "Requests", "render_rate", "win_rate", "candidates"]
    results = [dict(zip(columns, row)) for row in rows]
    conn.close()
    return jsonify({"columns": columns, "rows": results})

@app.route("/api/all-filter-reason-data", methods=["GET"])
def get_all_filter_reason_data():
    """API endpoint to fetch all cached filter reason data with optional date filtering."""
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")
    
    conn = sqlite3.connect(LOSS_REASON_DB_PATH)
    c = conn.cursor()
    
    if date_from and date_to:
        # Filter by date range
        c.execute("""
            SELECT date_key, deal_id, deal_name, filter_reason, imps, real_demand_returned, oppos, total_bids, Loss_Totals, fill_rate, Requests, render_rate, win_rate, candidates
            FROM loss_reason_results
            WHERE date_key >= ? AND date_key <= ?
            ORDER BY date_key DESC, deal_name ASC, filter_reason ASC
        """, (date_from, date_to))
    else:
        # Get all data
        c.execute("""
            SELECT date_key, deal_id, deal_name, filter_reason, imps, real_demand_returned, oppos, total_bids, Loss_Totals, fill_rate, Requests, render_rate, win_rate, candidates
            FROM loss_reason_results
            ORDER BY date_key DESC, deal_name ASC, filter_reason ASC
        """)
    
    rows = c.fetchall()
    columns = ["date_key", "deal_id", "deal_name", "filter_reason", "imps", "real_demand_returned", "oppos", "total_bids", "Loss_Totals", "fill_rate", "Requests", "render_rate", "win_rate", "candidates"]
    results = [dict(zip(columns, row)) for row in rows]
    conn.close()
    
    return jsonify({
        "status": "success",
        "columns": columns, 
        "rows": results,
        "total_rows": len(results)
    })
@app.route("/get_available_deals", methods=["GET"])
def get_available_deals():
    """Get list of unique deal names from cached data"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get deal summary from normalized table
        cursor.execute("""
            SELECT 
                deal_name,
                MIN(date_key) as min_date,
                MAX(date_key) as max_date,
                COUNT(*) as row_count
            FROM query_results 
            WHERE deal_name IS NOT NULL AND deal_name != ''
            GROUP BY deal_name
            ORDER BY deal_name
        """)
        
        deal_summaries = cursor.fetchall()
        conn.close()
        
        # Format the response
        available_deals = []
        for deal_name, min_date, max_date, row_count in deal_summaries:
            # Format date range
            if min_date == max_date:
                date_range = min_date
            else:
                date_range = f"{min_date} to {max_date}"
            
            available_deals.append({
                "name": deal_name,
                "display_name": f"{deal_name} ({date_range}, {row_count} days)",
                "date_range": date_range,
                "row_count": row_count
            })
        
        return jsonify({
            "status": "success",
            "deals": available_deals,
            "total_count": len(available_deals)
        })
        
    except Exception as e:
        return jsonify({
            "status": "error", 
            "message": f"Failed to get available deals: {str(e)}"
        })

@app.route("/breakout_data", methods=["POST"])
def get_breakout_data():
    """Get fill rate data broken down by provider type from cached data"""
    # Get parameters
    date_from = request.form.get("date_from", "2025-07-01").strip()
    date_to = request.form.get("date_to", "2025-08-05").strip()
    
    try:
        # Get all cached data for the date range
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT date_key, deal_name, imps, oppos, total_bids, fill_rate, render_rate, requests
            FROM query_results 
            WHERE date_key >= ? AND date_key <= ?
            AND deal_name IS NOT NULL AND deal_name != ''
            ORDER BY date_key DESC, deal_name ASC
        """, (date_from, date_to))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return jsonify({
                "status": "error", 
                "message": f"No cached data found for date range {date_from} to {date_to}. Please run some queries first."
            })
        
        # Organize data by provider type and aggregate by provider and date
        from collections import defaultdict
        
        # Group by provider type and provider name, then by date
        breakout_data = {
            'standard': defaultdict(lambda: defaultdict(lambda: {'imps': 0, 'oppos': 0, 'requests': 0})),
            'reseller': defaultdict(lambda: defaultdict(lambda: {'imps': 0, 'oppos': 0, 'requests': 0})),
            'vod': defaultdict(lambda: defaultdict(lambda: {'imps': 0, 'oppos': 0, 'requests': 0}))
        }
        
        for row in rows:
            date_key, deal_name, imps, oppos, total_bids, fill_rate, render_rate, requests = row
            
            # Classify provider type based on deal name using AND clauses
            deal_name_lower = deal_name.lower()
            has_reseller = 'reseller' in deal_name_lower
            has_vod = 'vod' in deal_name_lower or 'video on demand' in deal_name_lower
            
            if has_vod:
                # VOD: Contains VOD (regardless of reseller)
                provider_type = 'vod'
            elif has_reseller and not has_vod:
                # Reseller: Contains reseller AND does not contain VOD
                provider_type = 'reseller'
            else:
                # Standard/Direct: Does not contain reseller AND does not contain VOD
                provider_type = 'standard'
            
            # Aggregate data
            breakout_data[provider_type][deal_name][date_key]['imps'] += float(imps or 0)
            breakout_data[provider_type][deal_name][date_key]['oppos'] += float(oppos or 0)
            breakout_data[provider_type][deal_name][date_key]['requests'] += float(requests or 0)
        
        # Calculate fill rates and format for frontend
        formatted_data = {}
        for provider_type, providers in breakout_data.items():
            formatted_data[provider_type] = []
            
            for deal_name, dates in providers.items():
                provider_summary = {
                    'deal_name': deal_name,
                    'dates': {}
                }
                
                total_imps = 0
                total_oppos = 0
                total_requests = 0
                
                for date_key, metrics in dates.items():
                    imps = metrics['imps']
                    oppos = metrics['oppos']
                    requests = metrics['requests']
                    fill_rate = (imps / oppos * 100) if oppos > 0 else 0
                    
                    provider_summary['dates'][date_key] = {
                        'imps': imps,
                        'oppos': oppos,
                        'fill_rate': fill_rate,
                        'requests': requests
                    }
                    
                    total_imps += imps
                    total_oppos += oppos
                    total_requests += requests
                
                # Calculate overall fill rate
                overall_fill_rate = (total_imps / total_oppos * 100) if total_oppos > 0 else 0
                provider_summary['totals'] = {
                    'imps': total_imps,
                    'oppos': total_oppos,
                    'fill_rate': overall_fill_rate,
                    'requests': total_requests
                }
                
                formatted_data[provider_type].append(provider_summary)
        
        return jsonify({
            "status": "success",
            "data": formatted_data,
            "date_range": f"{date_from} to {date_to}",
            "cached": True,
            "total_rows": len(rows)
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/aggregate_data", methods=["POST"])
def get_aggregate_data():
    """Get aggregated summary data instead of day-by-day breakdown"""
    # Get parameters
    deal_names = request.form.getlist("deal_name")
    deal_names = [name.strip() for name in deal_names if name.strip()]
    date_from = request.form.get("date_from", "2025-07-01").strip()
    date_to = request.form.get("date_to", "2025-08-03").strip()
    group_by = request.form.get("group_by", "deal").strip()  # deal, provider_type, or total
    
    if not deal_names:
        return jsonify({"status": "error", "message": "Please enter at least one deal name!"}), 400
    
    try:
        # Get cached data for the requested deals and date range
        cached_rows, missing_ranges = get_cached_results(deal_names, date_from, date_to)
        
        # if missing_ranges:
        #     return jsonify({
        #         "status": "error", 
        #         "message": f"Missing data for some date ranges. Please run a regular query first to cache the data, then try the aggregate view."
        #     }), 400
        
        # if not cached_rows:
        #     return jsonify({
        #         "status": "error", 
        #         "message": f"No data found for the specified deals and date range."
        #     }), 400
        
        # Calculate aggregates based on grouping
        from collections import defaultdict
        
        if group_by == "deal":
            # Group by individual deal names
            aggregates = defaultdict(lambda: {
                'imps': 0, 'oppos': 0, 'total_bids': 0, 'requests': 0,
                'real_demand_returned': 0, 'Loss_Totals': 0, 'candidates': 0,
                'days_with_data': 0, 'date_range': {'min': None, 'max': None}
            })
            
            for row in cached_rows:
                deal_name = row.get('deal_name', 'Unknown')
                date_key = row.get('date_key', '')
                
                agg = aggregates[deal_name]
                agg['imps'] += float(row.get('imps', 0) or 0)
                agg['oppos'] += float(row.get('oppos', 0) or 0)
                agg['total_bids'] += float(row.get('total_bids', 0) or 0)
                agg['requests'] += float(row.get('Requests', 0) or 0)
                agg['real_demand_returned'] += float(row.get('real_demand_returned', 0) or 0)
                agg['Loss_Totals'] += float(row.get('Loss_Totals', 0) or 0)
                agg['candidates'] += float(row.get('candidates', 0) or 0)
                agg['days_with_data'] += 1
                
                # Track date range
                if agg['date_range']['min'] is None or date_key < agg['date_range']['min']:
                    agg['date_range']['min'] = date_key
                if agg['date_range']['max'] is None or date_key > agg['date_range']['max']:
                    agg['date_range']['max'] = date_key
            
        elif group_by == "provider_type":
            # Group by provider classification
            aggregates = defaultdict(lambda: {
                'imps': 0, 'oppos': 0, 'total_bids': 0, 'requests': 0,
                'real_demand_returned': 0, 'Loss_Totals': 0, 'candidates': 0,
                'days_with_data': 0, 'deal_count': set(), 'deals': set()
            })
            
            for row in cached_rows:
                deal_name = row.get('deal_name', 'Unknown')
                
                # Classify provider type using the same logic as breakout analysis
                deal_name_lower = deal_name.lower()
                has_reseller = 'reseller' in deal_name_lower
                has_vod = 'vod' in deal_name_lower or 'video on demand' in deal_name_lower
                
                if has_vod:
                    provider_type = 'VOD'
                elif has_reseller and not has_vod:
                    provider_type = 'Reseller'
                else:
                    provider_type = 'Standard/Direct'
                
                agg = aggregates[provider_type]
                agg['imps'] += float(row.get('imps', 0) or 0)
                agg['oppos'] += float(row.get('oppos', 0) or 0)
                agg['total_bids'] += float(row.get('total_bids', 0) or 0)
                agg['requests'] += float(row.get('Requests', 0) or 0)
                agg['real_demand_returned'] += float(row.get('real_demand_returned', 0) or 0)
                agg['Loss_Totals'] += float(row.get('Loss_Totals', 0) or 0)
                agg['candidates'] += float(row.get('candidates', 0) or 0)
                agg['days_with_data'] += 1
                agg['deals'].add(deal_name)
                
        else:  # group_by == "total"
            # Single total across all deals
            aggregates = {
                'Grand Total': {
                    'imps': 0, 'oppos': 0, 'total_bids': 0, 'requests': 0,
                    'real_demand_returned': 0, 'Loss_Totals': 0, 'candidates': 0,
                    'days_with_data': 0, 'deal_count': set(), 'deals': set()
                }
            }
            
            for row in cached_rows:
                deal_name = row.get('deal_name', 'Unknown')
                agg = aggregates['Grand Total']
                agg['imps'] += float(row.get('imps', 0) or 0)
                agg['oppos'] += float(row.get('oppos', 0) or 0)
                agg['total_bids'] += float(row.get('total_bids', 0) or 0)
                agg['requests'] += float(row.get('Requests', 0) or 0)
                agg['real_demand_returned'] += float(row.get('real_demand_returned', 0) or 0)
                agg['Loss_Totals'] += float(row.get('Loss_Totals', 0) or 0)
                agg['candidates'] += float(row.get('candidates', 0) or 0)
                agg['days_with_data'] += 1
                agg['deals'].add(deal_name)
        
        # Calculate rates and format results
        result_data = []
        for group_name, agg in aggregates.items():
            # Calculate rates
            fill_rate = (agg['imps'] / agg['oppos'] * 100) if agg['oppos'] > 0 else 0
            render_rate = (agg['imps'] / agg['total_bids'] * 100) if agg['total_bids'] > 0 else 0
            win_rate = (agg['total_bids'] / agg['real_demand_returned'] * 100) if agg['real_demand_returned'] > 0 else 0
            
            # Calculate averages
            days = agg['days_with_data'] if agg['days_with_data'] > 0 else 1
            avg_daily_imps = agg['imps'] / days
            avg_daily_oppos = agg['oppos'] / days
            avg_daily_requests = agg['requests'] / days
            
            result_row = {
                'group_name': group_name,
                'total_imps': agg['imps'],
                'total_oppos': agg['oppos'],
                'total_bids': agg['total_bids'],
                'total_requests': agg['requests'],
                'total_real_demand_returned': agg['real_demand_returned'],
                'total_loss': agg['Loss_Totals'],
                'total_candidates': agg['candidates'],
                'fill_rate': fill_rate,
                'render_rate': render_rate,
                'win_rate': win_rate,
                'avg_daily_imps': avg_daily_imps,
                'avg_daily_oppos': avg_daily_oppos,
                'avg_daily_requests': avg_daily_requests,
                'days_with_data': agg['days_with_data']
            }
            
            # Add group-specific metadata
            if group_by == "deal":
                result_row['date_range'] = f"{agg['date_range']['min']} to {agg['date_range']['max']}"
            elif group_by in ["provider_type", "total"]:
                result_row['deal_count'] = len(agg['deals'])
                result_row['deals'] = list(agg['deals'])
            
            result_data.append(result_row)
        
        # Sort results
        if group_by == "deal":
            result_data.sort(key=lambda x: x['total_imps'], reverse=True)
        elif group_by == "provider_type":
            result_data.sort(key=lambda x: x['total_imps'], reverse=True)
        
        return jsonify({
            "status": "success",
            "data": result_data,
            "group_by": group_by,
            "date_range": f"{date_from} to {date_to}",
            "deal_names": deal_names,
            "total_rows_processed": len(cached_rows),
            "cached": True
        })
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/debug_query", methods=["POST"])
def debug_query():
    deal_name = request.form.get("deal_name", "Example Deal").strip()
    
    debug_sql = f"""
    SELECT 
        f.date_key,
        d AS deal_id,
        h.name AS deal_name,
        h.id AS history_id,
        f.deal_id AS original_deal_id_array,
        TYPEOF(d) AS deal_id_type,
        TYPEOF(h.id) AS history_id_type,
        CAST(d AS VARCHAR) AS deal_id_as_string
    FROM
        advertising.demand_funnel f
    CROSS JOIN UNNEST(f.deal_id) AS t(d)
    LEFT JOIN ads.dim_rams_deals_history h
        ON CAST(d AS VARCHAR) = CAST(h.id AS VARCHAR)
        AND f.date_key = h.date_key
    WHERE 
        f.date_key >= '2025-07-01'
        AND CONTAINS(f.demand_systems, 'PARTNER_AD_SERVER_VIDEO')
        AND h.name LIKE '%{deal_name}%'
    """
    
    payload = {
        "database_id": SUPERSET_DB_ID,
        "schema": "advertising", 
        "sql": debug_sql,
        "runAsync": False
    }
    
    try:
        resp = requests.post(
            SUPERSET_EXECUTE_URL,
            headers=SUPERSET_HEADERS,
            data=json.dumps(payload),
            timeout=60
        )
        
        if resp.status_code == 200:
            data = resp.json()
            return jsonify({
                "status": "success",
                "data": data.get("data", []),
                "columns": data.get("columns", []),
                "sql": debug_sql
            })
        else:
            return jsonify({"status": "error", "message": resp.text})
            
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/manual_data_pull", methods=["POST"])
def manual_data_pull():
    """Manually trigger the daily data pull process"""
    try:
        # Run the data pull in a background thread to avoid blocking the response
        threading.Thread(target=pull_previous_day_data, daemon=True).start()
        
        return jsonify({
            "status": "success",
            "message": "Manual data pull started in background. Check the console logs for progress."
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Failed to start manual data pull: {str(e)}"
        })

@app.route("/check_cache_status", methods=["GET"])
def check_cache_status():
    """Check cache status and yesterday's data availability"""
    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        today = datetime.now().strftime('%Y-%m-%d')
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Check total deals in cache
        cursor.execute("""
            SELECT COUNT(DISTINCT deal_name) as total_deals
            FROM query_results 
            WHERE deal_name IS NOT NULL AND deal_name != ''
        """)
        total_deals = cursor.fetchone()[0]
        
        # Check yesterday's data
        cursor.execute("""
            SELECT COUNT(DISTINCT deal_name) as deals_with_yesterday_data,
                   COUNT(*) as total_rows_yesterday
            FROM query_results 
            WHERE date_key = ? AND deal_name IS NOT NULL AND deal_name != ''
        """, (yesterday,))
        yesterday_data = cursor.fetchone()
        
        # Check today's data (if any)
        cursor.execute("""
            SELECT COUNT(DISTINCT deal_name) as deals_with_today_data,
                   COUNT(*) as total_rows_today
            FROM query_results 
            WHERE date_key = ? AND deal_name IS NOT NULL AND deal_name != ''
        """, (today,))
        today_data = cursor.fetchone()
        
        # Get latest date in cache
        cursor.execute("""
            SELECT MAX(date_key) as latest_date,
                   MIN(date_key) as earliest_date
            FROM query_results 
            WHERE deal_name IS NOT NULL AND deal_name != ''
        """)
        date_range = cursor.fetchone()
        
        # Get some sample deals for yesterday
        cursor.execute("""
            SELECT deal_name, imps, oppos, fill_rate
            FROM query_results 
            WHERE date_key = ? AND deal_name IS NOT NULL AND deal_name != ''
            ORDER BY imps DESC
        """, (yesterday,))
        sample_yesterday = cursor.fetchall()
        
        conn.close()
        
        return jsonify({
            "status": "success",
            "cache_summary": {
                "total_deals_in_cache": total_deals,
                "date_range": f"{date_range[1]} to {date_range[0]}" if date_range[0] else "No data",
                "yesterday": {
                    "date": yesterday,
                    "deals_with_data": yesterday_data[0],
                    "total_rows": yesterday_data[1],
                    "sample_deals": [
                        {
                            "deal_name": row[0],
                            "imps": row[1],
                            "oppos": row[2], 
                            "fill_rate": f"{row[3]:.1f}%"
                        } for row in sample_yesterday
                    ]
                },
                "today": {
                    "date": today,
                    "deals_with_data": today_data[0],
                    "total_rows": today_data[1]
                }
            }
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": f"Failed to check cache status: {str(e)}"
        })

@app.route("/check_status/<job_id>", methods=["GET"])
def check_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"status": "error", "message": "Invalid job_id"}), 404
    return jsonify(job)

@app.route("/loss-reason-query", methods=["POST"])
def loss_reason_query_api():
    try:
        deal_name = request.form.get("deal_name", "").strip()
        date_from = request.form.get("date_from", "").strip()
        date_to = request.form.get("date_to", "").strip()
        print(f"Received: deal_name={deal_name}, date_from={date_from}, date_to={date_to}")

        # Check cache coverage first
        conn = sqlite3.connect(LOSS_REASON_DB_PATH)
        c = conn.cursor()
        
        # Check what date range we have cached
        c.execute("""
            SELECT MIN(date_key) as cached_min, MAX(date_key) as cached_max, COUNT(*) as cached_count
            FROM loss_reason_results 
            WHERE deal_name LIKE ?
        """, (f'%{deal_name}%',))
        
        cache_info = c.fetchone()
        cached_min, cached_max, cached_count = cache_info if cache_info else (None, None, 0)
        
        if cached_count == 0:
            # No cached data - query full range
            print(f"[LOSS_REASON_CACHE] No cached data found for '{deal_name}'")
            conn.close()
            return query_loss_reason_superset(deal_name, date_from, date_to)
        
        print(f"[LOSS_REASON_CACHE] Cache coverage: {cached_min} to {cached_max} ({cached_count} rows)")
        
        # Check if cache covers the full requested range
        cache_covers_request = (cached_min <= date_from and cached_max >= date_to)
        
        if cache_covers_request:
            # Full cache coverage - return cached data
            print(f"[LOSS_REASON_CACHE] Full cache coverage available")
            c.execute("""
                SELECT date_key, deal_id, deal_name, filter_reason, imps, real_demand_returned, 
                       oppos, total_bids, fill_rate, Requests, render_rate, win_rate, candidates
                FROM loss_reason_results 
                WHERE deal_name LIKE ? AND date_key >= ? AND date_key <= ?
                ORDER BY date_key ASC
            """, (f'%{deal_name}%', date_from, date_to))
            
            cached_rows = c.fetchall()
            conn.close()
            
            # Convert cached rows to dict format
            rows = []
            for row in cached_rows:
                rows.append({
                    'date_key': row[0],
                    'deal_id': row[1],
                    'deal_name': row[2],
                    'filter_reason': row[3],
                    'imps': float(row[4] or 0),
                    'real_demand_returned': float(row[5] or 0),
                    'oppos': float(row[6] or 0),
                    'total_bids': float(row[7] or 0),
                    'fill_rate': float(row[8] or 0),
                    'Requests': float(row[9] or 0),
                    'render_rate': float(row[10] or 0),
                    'win_rate': float(row[11] or 0),
                    'candidates': float(row[12] or 0)
                })
            
            return jsonify({
                "status": "success",
                "rows": rows,
                "columns": list(rows[0].keys()) if rows else [],
                "cached": True
            })
        
        else:
            # Partial cache coverage - use incremental approach
            print(f"[LOSS_REASON_CACHE] Partial cache coverage - using incremental query")
            
            # Get overlapping cached data
            overlap_start = max(date_from, cached_min)
            overlap_end = min(date_to, cached_max)
            
            c.execute("""
                SELECT date_key, deal_id, deal_name, filter_reason, imps, real_demand_returned, 
                       oppos, total_bids, fill_rate, Requests, render_rate, win_rate, candidates
                FROM loss_reason_results 
                WHERE deal_name LIKE ? AND date_key >= ? AND date_key <= ?
                ORDER BY date_key ASC
            """, (f'%{deal_name}%', overlap_start, overlap_end))
            
            cached_rows = c.fetchall()
            conn.close()
            
            # Convert cached rows to dict format
            cached_data = []
            for row in cached_rows:
                cached_data.append({
                    'date_key': row[0],
                    'deal_id': row[1],
                    'deal_name': row[2],
                    'filter_reason': row[3],
                    'imps': float(row[4] or 0),
                    'real_demand_returned': float(row[5] or 0),
                    'oppos': float(row[6] or 0),
                    'total_bids': float(row[7] or 0),
                    'fill_rate': float(row[8] or 0),
                    'Requests': float(row[9] or 0),
                    'render_rate': float(row[10] or 0),
                    'win_rate': float(row[11] or 0),
                    'candidates': float(row[12] or 0)
                })
            
            # Determine missing date ranges to query
            missing_ranges = []
            
            if date_from < cached_min:
                # Need data before cached range
                end_before = (datetime.strptime(cached_min, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
                missing_ranges.append({
                    'date_from': date_from,
                    'date_to': end_before,
                    'reason': 'before_cache'
                })
            
            if date_to > cached_max:
                # Need data after cached range
                start_after = (datetime.strptime(cached_max, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
                missing_ranges.append({
                    'date_from': start_after,
                    'date_to': date_to,
                    'reason': 'after_cache'
                })
            
            print(f"[LOSS_REASON_CACHE] Missing ranges: {missing_ranges}")
            
            # Query missing ranges and combine with cached data
            all_rows = cached_data.copy()
            
            for missing_range in missing_ranges:
                print(f"[LOSS_REASON_CACHE] Querying missing range: {missing_range['date_from']} to {missing_range['date_to']}")
                new_rows = query_loss_reason_superset(deal_name, missing_range['date_from'], missing_range['date_to'])
                
                if new_rows and isinstance(new_rows, list):
                    all_rows.extend(new_rows)
            
            # Sort by date for consistent ordering
            all_rows.sort(key=lambda x: (x.get('date_key', ''), x.get('deal_name', '')))
            
            return jsonify({
                "status": "success",
                "rows": all_rows,
                "columns": list(all_rows[0].keys()) if all_rows else [],
                "cached": False,
                "incremental": True
            })

    except Exception as e:
        print("Error in /loss_reason_query:", e)
        return jsonify({"status": "error", "message": str(e)})

def query_loss_reason_superset(deal_name, date_from, date_to):
    """Helper function to query Superset for loss reason data"""
    try:
        like_condition = f"AND h.name LIKE '%{deal_name}%'" if deal_name else ""

        sql_query = QUERY_TEMPLATE_LOSS_REASON.format(
            date_from=date_from,
            date_to=date_to,
            deal_name_condition=like_condition
        )

        print(f"[DEBUG] Loss reason query: {sql_query}")

        payload = {
            "database_id": SUPERSET_DB_ID,
            "schema": "advertising",
            "sql": sql_query,
            "runAsync": False
        }

        resp = requests.post(
            SUPERSET_EXECUTE_URL,
            headers=SUPERSET_HEADERS,
            data=json.dumps(payload),
            timeout=1800
        )

        if resp.status_code == 200:
            resp_data = resp.json()
            rows = resp_data.get("data", [])

            # Save to loss reason cache
            save_loss_reason_results_to_db(deal_name, date_from, date_to, rows)

            return rows
        else:
            print(f"[LOSS_REASON_CACHE] Superset query failed: {resp.text}")
            return []

    except Exception as e:
        print(f"[LOSS_REASON_CACHE] Superset query exception: {e}")
        return []

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5003, debug=True)
