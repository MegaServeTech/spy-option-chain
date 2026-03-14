import os
import sys
from flask import Flask, request, render_template
import pandas as pd
from sqlalchemy import create_engine, text, inspect
from datetime import datetime, timedelta
import numpy as np
import plotly.graph_objects as go
from plotly.utils import PlotlyJSONEncoder
import json
from configure import APP_CONFIG

app = Flask(__name__)
app.json_encoder = PlotlyJSONEncoder
app.secret_key = APP_CONFIG['SECRET_KEY']

# Global dictionary for upload progress
UPLOAD_PROGRESS = {}

def get_db_inspector():
    """Get a fresh database inspector to ensure we see newly created tables"""
    try:
        return inspect(engine)
    except Exception as e:
        print(f"Error creating inspector: {e}")
        return None

@app.route('/api/progress/<task_id>')
def get_progress(task_id):
    return json.dumps(UPLOAD_PROGRESS.get(task_id, {'progress': 0, 'details': 'Waiting...'}))

# ═══════════════════════════════════════════════════════════════════
#                    DATABASE CONNECTION
# ═══════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("INITIALIZING DATABASE CONNECTION")
print("=" * 70)

# Global flags for database health
DB_CONNECTED = False
engine = None
# inspector = None # Global inspector removed in favor of fresh inspection

try:
    print("Creating database engine...")
    engine = create_engine(
        APP_CONFIG['DATABASE_URL'],
        pool_pre_ping=True,  # Verify connections before using
        pool_recycle=3600,   # Recycle connections after 1 hour
        pool_size=10,        # Connection pool size
        max_overflow=20,     # Max overflow connections
        echo=False           # Set to True for SQL query logging
    )
    
    print("Creating database inspector...")
    # inspector = inspect(engine) # Start-up inspection not strictly needed globally anymore
    
    print("Testing database connection...")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1 as test"))
        test_value = result.scalar()
        if test_value == 1:
            DB_CONNECTED = True
            print("Database connection successful!")
            print("Connection test passed!")
        else:
            print("WARNING:  Database responded but test query failed")
    
except Exception as e:
    error_type = type(e).__name__
    error_msg = str(e)
    
    print(f"Database connection failed: {error_type}", file=sys.stderr)
    print(f"Error details: {error_msg}", file=sys.stderr)
    
    if APP_CONFIG.get('IS_PRODUCTION'):
        print("\n" + "=" * 35, file=sys.stderr)
        print("WARNING:  RUNNING IN PRODUCTION WITHOUT DATABASE CONNECTION!", file=sys.stderr)
        print("WARNING:  Please configure DATABASE_URL environment variable.", file=sys.stderr)
        print("=" * 35 + "\n", file=sys.stderr)
    else:
        print("\nWARNING:  Local database not available. Some features may not work.", file=sys.stderr)
    
    # Create engine anyway to prevent import errors (queries will fail gracefully)
    if not engine:
        try:
            engine = create_engine(APP_CONFIG['DATABASE_URL'], pool_pre_ping=True)
            # inspector = inspect(engine)
        except Exception as inner_e:
            print(f"Failed to create database engine: {inner_e}", file=sys.stderr)

print("=" * 70 + "\n")



# ───────────────────────────────────────────────────────────────
#         AUTOMATIC INDEX CREATION
# ───────────────────────────────────────────────────────────────
def remove_indexes(table_name):
    """Temporarily remove indexes for faster batch uploads"""
    try:
        with engine.connect() as conn:
            if table_name == 'index_data':
                try: 
                    conn.execute(text("DROP INDEX idx_datetime_utc ON index_data"))
                except Exception: 
                    pass
            elif table_name == 'option_data':
                try: 
                    conn.execute(text("DROP INDEX idx_utc_minute_expiry ON option_data"))
                except Exception: 
                    pass
                try: 
                    conn.execute(text("DROP INDEX idx_expiry_strike ON option_data"))
                except Exception: 
                    pass
            conn.commit()
    except Exception:
        pass

def ensure_indexes():
    try:
        current_inspector = get_db_inspector()
        if not current_inspector: return

        with engine.connect() as conn:
            if current_inspector.has_table('index_data'):
                indexes = {idx['name'] for idx in current_inspector.get_indexes('index_data')}
                if 'idx_datetime_utc' not in indexes:
                    conn.execute(text("ALTER TABLE index_data ADD INDEX idx_datetime_utc (datetime_UTC)"))

            if current_inspector.has_table('option_data'):
                indexes = {idx['name'] for idx in current_inspector.get_indexes('option_data')}
                if 'idx_utc_minute_expiry' not in indexes:
                    conn.execute(text("ALTER TABLE option_data ADD INDEX idx_utc_minute_expiry (UTC_MINUTE, EXPIRY_DATE)"))
                if 'idx_expiry_strike' not in indexes:
                    conn.execute(text("ALTER TABLE option_data ADD INDEX idx_expiry_strike (EXPIRY_DATE, STRIKE)"))

            conn.commit()
    except Exception:
        pass  # silent fail - no logging

ensure_indexes()

# Safe column addition
def add_missing_columns(table_name, df):
    current_inspector = get_db_inspector()
    if not current_inspector or not current_inspector.has_table(table_name):
        return
    try:
        existing_cols = {col['name'] for col in current_inspector.get_columns(table_name)}
    except:
        existing_cols = set()
    new_cols = set(df.columns) - existing_cols
    if new_cols:
        with engine.connect() as conn:
            for col in new_cols:
                dtype = df[col].dtype
                sql_type = 'TEXT' if dtype == 'object' else \
                           'DOUBLE' if dtype == 'float64' else \
                           'BIGINT' if dtype == 'int64' else \
                           'DATETIME' if 'datetime' in str(dtype) else 'TEXT'
                try:
                    conn.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `{col}` {sql_type}"))
                    conn.commit()
                except Exception:
                    pass  # silent fail
        # no logging

# ───────────────────────────────────────────────────────────────
#                        ROUTES
# ───────────────────────────────────────────────────────────────

@app.route('/', methods=['GET', 'POST'])
def home():
    message = None
    if request.method == 'POST':
        upload_type = request.form.get('upload_type')
        files = request.files.getlist('file')
        if not files or all(f.filename == '' for f in files):
            message = '<div class="alert alert-danger">No files selected!</div>'
        else:
            success_count = fail_count = 0
            details = []
            table_name = 'index_data' if upload_type == 'index' else 'option_data'
            req_col = 'datetime_UTC' if upload_type == 'index' else 'UTC_MINUTE'
            
            # --- Drop Indexes for Performance ---
            current_inspector = get_db_inspector()
            if current_inspector and current_inspector.has_table(table_name):
                remove_indexes(table_name)

            with engine.connect() as conn:
                conn.execute(text(f"CREATE TABLE IF NOT EXISTS {table_name} (id INT AUTO_INCREMENT PRIMARY KEY)"))
                conn.commit()

            for file in files:
                if not file.filename.lower().endswith('.csv'):
                    details.append(f"❌ {file.filename} - Not a CSV")
                    fail_count += 1
                    continue

                try:
                    # Count total lines for progress
                    file.stream.seek(0)
                    total_lines = sum(1 for line in file.stream)
                    file.stream.seek(0)
                    
                    # Read CSV in chunks (Batches)
                    chunk_size = 5000
                    chunks = pd.read_csv(file.stream, chunksize=chunk_size)
                    
                    file_total_rows = 0
                    first_chunk = True
                    
                    task_id = request.form.get('task_id')

                    for df_chunk in chunks:
                        # Validate required column existence in the first chunk
                        if first_chunk:
                            if req_col not in df_chunk.columns:
                                raise ValueError(f"Missing required column: {req_col}")
                        
                        # Process Chunk
                        start_rows = len(df_chunk)
                        
                        if upload_type == 'index':
                            df_chunk['datetime_UTC'] = pd.to_datetime(df_chunk['datetime_UTC'], errors='coerce')
                            df_chunk = df_chunk.dropna(subset=['datetime_UTC'])
                            # Ensure format matches MySQL DATETIME (YYYY-MM-DD HH:MM:SS)
                            df_chunk['datetime_UTC'] = df_chunk['datetime_UTC'].dt.strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            # Ensure format matches MySQL DATETIME (YYYY-MM-DD HH:MM:SS)
                            df_chunk['UTC_MINUTE'] = pd.to_datetime(df_chunk['UTC_MINUTE'], unit='s', utc=True) \
                                                    .dt.strftime('%Y-%m-%d %H:%M:%S')
                            df_chunk = df_chunk[~pd.to_datetime(df_chunk['UTC_MINUTE'], format='%Y-%m-%d %H:%M:%S', errors='coerce').isna()]

                        if len(df_chunk) == 0:
                            continue

                        # Add valid missing columns (safe to call repeatedly as it checks existence)
                        add_missing_columns(table_name, df_chunk)
                        
                        # Insert Chunk
                        df_chunk.to_sql(table_name, engine, if_exists='append', index=False)
                        
                        file_total_rows += len(df_chunk)
                        first_chunk = False

                        # Update Progress
                        if task_id:
                            progress_pct = int((file_total_rows / total_lines) * 100)
                            UPLOAD_PROGRESS[task_id] = {
                                'progress': progress_pct,
                                'details': f"Processing {file.filename}: {file_total_rows}/{total_lines} rows"
                            }

                    success_count += 1
                    details.append(f"✅ {file.filename} - Uploaded {file_total_rows} rows in batches")

                except Exception as e:
                    fail_count += 1
                    details.append(f"❌ {file.filename} - Failed: {str(e)}")

            # --- Restore Indexes ---
            ensure_indexes()

            alert_class = 'alert-success' if success_count > 0 else 'alert-danger'
            message = f"""
            <div class="alert {alert_class}">
                <strong>Upload Complete!</strong><br>
                Success: <strong>{success_count}</strong> | Failed: <strong>{fail_count}</strong><br><br>
                <small>{'<br>'.join(details)}</small>
            </div>
            """
            
            # Clear progress
            task_id = request.form.get('task_id')
            if task_id and task_id in UPLOAD_PROGRESS:
                del UPLOAD_PROGRESS[task_id]

    return render_template('index.html', message=message)


@app.route('/view')
def view_data():
    index_table = '<p>No index data yet.</p>'
    option_table = '<p>No option data yet.</p>'
    
    current_inspector = get_db_inspector()

    try:
        if current_inspector and current_inspector.has_table('index_data'):
            df = pd.read_sql('SELECT * FROM index_data ORDER BY id DESC LIMIT 10', engine)
            if not df.empty:
                if 'id' in df.columns:
                    df = df.drop(columns=['id'])
                df.columns = [c.upper() for c in df.columns]
                index_table = df.to_html(classes='table table-striped table-bordered', index=False)
    except Exception:
        index_table = '<p>Error reading preview</p>'

    try:
        if current_inspector and current_inspector.has_table('option_data'):
            df = pd.read_sql('SELECT * FROM option_data ORDER BY id DESC LIMIT 10', engine)
            if not df.empty:
                if 'id' in df.columns:
                    df = df.drop(columns=['id'])
                df.columns = [c.upper() for c in df.columns]
                option_table = df.to_html(classes='table table-striped table-bordered', index=False)
    except Exception:
        option_table = '<p>Error reading preview</p>'

    return render_template('view.html', index_table=index_table, option_table=option_table)


@app.route('/options_chain', methods=['GET'])
def options_chain():
    print("DEBUG: Fetching available dates (Direct SQL Method)...")
    
    # helper to get dates from a query
    def fetch_dates_from_query(query, label):
        try:
            with engine.connect() as conn:
                res = conn.execute(text(query))
                rows = [r[0] for r in res.fetchall() if r[0]]
                print(f"DEBUG: {label} returned {len(rows)} raw dates")
                return rows
        except Exception as e:
            # Table likely doesn't exist or other DB error
            print(f"DEBUG: {label} query failed (Table might be missing): {e}")
            return []

    # Try index_data
    # Use simple string slicing for max compatibility if stored as text
    i_dates = set(fetch_dates_from_query(
        "SELECT DISTINCT SUBSTR(datetime_UTC, 1, 10) FROM index_data WHERE datetime_UTC IS NOT NULL", 
        "index_data"
    ))

    # Try option_data
    o_dates = set(fetch_dates_from_query(
        "SELECT DISTINCT SUBSTR(UTC_MINUTE, 1, 10) FROM option_data WHERE UTC_MINUTE IS NOT NULL", 
        "option_data"
    ))
    
    # Filter dates present in BOTH tables (Intersection)
    if i_dates and o_dates:
        raw_dates = i_dates.intersection(o_dates)
        print(f"DEBUG: Found {len(raw_dates)} dates common to both tables")
    else:
        raw_dates = set()
        print("DEBUG: One or both tables missing data, intersection is empty")
    
    # Process and Sort
    available_dates = []
    formatted_dates = []
    try:
        if raw_dates:
            # Convert to pandas for easy handling
            df_dates = pd.DataFrame(list(raw_dates), columns=['trade_date'])
            
            # Robust parsing
            unique_dates = pd.to_datetime(df_dates['trade_date'],  errors='coerce').dropna()
            unique_dates = unique_dates.sort_values(ascending=False)
            
            available_dates = unique_dates.dt.strftime('%Y-%m-%d').tolist()
            formatted_dates = unique_dates.dt.strftime('%d %b %Y').tolist()
            print(f"DEBUG: Final available_dates: {available_dates}")
        else:
            print("DEBUG: No dates found in either table.")
            
    except Exception as e:
        print(f"ERROR processing dates: {e}")
        import traceback
        traceback.print_exc()

    selected_date = request.args.get('date') or (available_dates[0] if available_dates else None)
    print(f"DEBUG: Selected date: {selected_date}")
    
    selected_time = request.args.get('time')
    selected_expiry = request.args.get('expiry', '0DTE')
    strike_steps = int(request.args.get('steps', 20))
    selected_comparison = request.args.get('compare', 'all')

    available_times = []
    filtered_times = []
    atm_strike = None
    volume_val = None
    chain_html = "<p class='text-center text-muted py-5 lead'>Select a date and time to view the option chain.</p>"
    selected_expiry_display = None
    price_chart = None
    atm_straddle_chart = None
    straddle_comparison_chart = None
    slider_value = 0
    open_price = None

    if not selected_date or selected_date not in available_dates:
        chain_html = "<p class='text-danger'>Invalid or no date selected.</p>"
        return render_template(
            'options_chain.html',
            dates=available_dates, formatted_dates=formatted_dates,
            selected_date=selected_date, times=available_times, filtered_times=filtered_times,
            selected_time=selected_time, selected_expiry=selected_expiry, strike_steps=strike_steps,
            atm_strike=atm_strike, chain=chain_html, selected_expiry_display=selected_expiry_display,
            open_price=open_price, price_chart=price_chart,
            atm_straddle_chart=atm_straddle_chart, straddle_comparison_chart=straddle_comparison_chart,
            selected_comparison=selected_comparison, slider_value=slider_value
        )

    trading_date_obj = datetime.strptime(selected_date, "%Y-%m-%d")
    # Database stores dates as YYYY-MM-DD
    utc_minute_date = trading_date_obj.strftime("%Y-%m-%d")

    # Load full day index data
    # Load full day index data for times, or fallback to option data
    index_df = pd.DataFrame()
    try:
        from sqlalchemy import inspect
        current_inspector = inspect(engine)
        if current_inspector and current_inspector.has_table('index_data'):
            index_df = pd.read_sql(
                text("SELECT datetime_UTC, `open`, `volume` FROM index_data WHERE datetime_UTC LIKE :day ORDER BY datetime_UTC"),
                engine, params={"day": f"{utc_minute_date}%"}
            )
        
        # If no index data, try to get times from option data
        if index_df.empty and current_inspector and current_inspector.has_table('option_data'):
             time_query = text("SELECT DISTINCT UTC_MINUTE as datetime_UTC FROM option_data WHERE UTC_MINUTE LIKE :day ORDER BY UTC_MINUTE")
             index_df = pd.read_sql(time_query, engine, params={"day": f"{utc_minute_date}%"})
             index_df['open'] = None # No price available

        if index_df.empty:
            chain_html = "<p class='text-danger'>No data found for this date.</p>"
            return render_template('options_chain.html',
                                 dates=available_dates, formatted_dates=formatted_dates,
                                 selected_date=selected_date, times=available_times, filtered_times=filtered_times,
                                 selected_time=selected_time, selected_expiry=selected_expiry, strike_steps=strike_steps,
                                 atm_strike=atm_strike, chain=chain_html, selected_expiry_display=selected_expiry_display,
                                 open_price=open_price, price_chart=price_chart,
                                 atm_straddle_chart=atm_straddle_chart, straddle_comparison_chart=straddle_comparison_chart,
                                 selected_comparison=selected_comparison, slider_value=slider_value)

        # Ensure datetime strings
        index_df['datetime_UTC'] = index_df['datetime_UTC'].astype(str)
        available_times = [t for t in index_df['datetime_UTC'].tolist() if t and t != 'None' and t != 'nan']

        if not selected_time and available_times:
            selected_time = available_times[0]

        # Filter times to be within 13:30 and 20:15
        filtered_times = []
        for t in available_times:
            # Handle different time format string lengths (with or without seconds)
            try:
                # Extract just the HH:MM part from 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD HH:MM'
                time_part = t.split(' ')[1][:5]
                if '13:30' <= time_part <= '20:15':
                    filtered_times.append(t)
            except IndexError:
                pass
        
        filtered_times.sort()
        
        # If no times in trading window, just show all valid times
        if not filtered_times and available_times:
            filtered_times = available_times

        if filtered_times:
            if selected_time not in filtered_times:
                selected_time = filtered_times[0]
            try:
                slider_value = filtered_times.index(selected_time)
            except ValueError:
                slider_value = 0

        # Get open price and ATM strike if available
        if 'open' in index_df.columns:
            current_row = index_df[index_df['datetime_UTC'] == selected_time]
            if not current_row.empty:
                if pd.notna(current_row.iloc[0]['open']):
                    open_price = float(current_row.iloc[0]['open'])
                    frac = open_price % 1
                    atm_strike = int(np.floor(open_price)) if frac < 0.5 else int(np.ceil(open_price))
                if 'volume' in current_row.columns and pd.notna(current_row.iloc[0]['volume']):
                    volume_val = float(current_row.iloc[0]['volume'])

    except Exception as e:
        chain_html = f"<p class='text-danger'>Error loading data: {str(e)}</p>"

    # Fast Option Chain
    import traceback
    try:
        exp_offset = {'0DTE': 0, '1DTE': 1, '2DTE': 2}.get(selected_expiry, 0)
        expiry_date_obj = trading_date_obj + timedelta(days=exp_offset)
        expiry_str = expiry_date_obj.strftime("%Y-%m-%d")
        selected_expiry_display = f"{expiry_str} ({selected_expiry})"

        # Use loose matching for time (first 16 chars: YYYY-MM-DD HH:MM)
        # This handles cases where one has seconds and other doesn't
        if selected_time:
            time_pattern = selected_time[:16] + '%' if len(selected_time) >= 16 else selected_time + '%'
        else:
            time_pattern = '%' # Should not happen if filtered correctly? or return empty

        opt_df_chain = pd.read_sql(
            text("""
                SELECT STRIKE, OPTION_TYPE, bid_open, ask_open
                FROM option_data
                WHERE UTC_MINUTE LIKE :tm AND EXPIRY_DATE = :exp
                ORDER BY STRIKE
            """),
            engine, params={"tm": time_pattern, "exp": expiry_str}
        )
        
        # Calculate ATM strike if not already known
        if atm_strike is None and not opt_df_chain.empty:
             opt_df_chain['STRIKE'] = pd.to_numeric(opt_df_chain['STRIKE'], errors='coerce')
             valid_strikes = opt_df_chain['STRIKE'].dropna()
             if not valid_strikes.empty:
                 atm_strike = int(valid_strikes.median())

        if opt_df_chain.empty:
            time_display = selected_time[11:16] if selected_time and len(selected_time) >= 16 else "N/A"
            chain_html = f"""
            <div class="text-center py-5">
                <p class="lead text-muted">No options found for this minute</p>
                <p>Time: {time_display} | Expiry: {selected_expiry_display}</p>
                <p>ATM: {atm_strike if atm_strike else 'Unknown'}</p>
            </div>
            """
        elif atm_strike is not None:
            opt_df_chain['STRIKE'] = pd.to_numeric(opt_df_chain['STRIKE'], errors='coerce')
            opt_df_chain['bid_open'] = pd.to_numeric(opt_df_chain['bid_open'], errors='coerce')
            opt_df_chain['ask_open'] = pd.to_numeric(opt_df_chain['ask_open'], errors='coerce')

            lower = atm_strike - strike_steps
            upper = atm_strike + strike_steps

            df_option = opt_df_chain[opt_df_chain['STRIKE'].between(lower, upper)]

            if df_option.empty:
                # If range is empty (maybe huge gap), just show what we have near median
                 df_option = opt_df_chain.iloc[max(0, len(opt_df_chain)//2 - strike_steps) : min(len(opt_df_chain), len(opt_df_chain)//2 + strike_steps)]


            pivot = df_option.pivot_table(
                index='STRIKE', columns='OPTION_TYPE',
                values=['bid_open', 'ask_open'], aggfunc='first'
            )
            # Safe join for columns
            pivot.columns = ['_'.join(str(c) for c in col).strip() for col in pivot.columns.values]
            pivot = pivot.reset_index()

            pivot.rename(columns={
                'bid_open_C': 'Call_Bid', 'ask_open_C': 'Call_Ask',
                'bid_open_P': 'Put_Bid', 'ask_open_P': 'Put_Ask'
            }, inplace=True)

            all_strikes = pd.DataFrame({'STRIKE': range(int(df_option['STRIKE'].min()), int(df_option['STRIKE'].max()) + 1)})
            chain = pd.merge(all_strikes, pivot, on='STRIKE', how='left')

            # Ensure all expected columns exist to prevent KeyError
            expected_cols = ['Call_Bid', 'Call_Ask', 'STRIKE', 'Put_Bid', 'Put_Ask']
            for col in expected_cols:
                if col not in chain.columns:
                    chain[col] = pd.NA

            chain = chain[expected_cols]

            for col in ['Call_Bid', 'Call_Ask', 'Put_Bid', 'Put_Ask']:
                if col in chain.columns:
                     chain[col] = pd.to_numeric(chain[col], errors='coerce')

            def style_option_chain(row):
                styles = [''] * len(row)
                strike = row['STRIKE']
                
                # Default background for all cells in the row if it's ATM
                if strike == atm_strike:
                    styles = ['background-color: #fff3cd; font-weight: bold; border-top: 2px solid #ffc107; border-bottom: 2px solid #ffc107; color: #000 !important;'] * len(row)
                    return styles

                # ITM / OTM Coloring
                # Calls (Columns: Call_Bid, Call_Ask)
                #   ITM if Strike < ATM (Yellowish like image)
                #   OTM if Strike > ATM (White)
                # Puts (Columns: Put_Bid, Put_Ask)
                #   ITM if Strike > ATM (Yellowish like image)
                #   OTM if Strike < ATM (White)
                
                for i, col in enumerate(row.index):
                    if col in ['Call_Bid', 'Call_Ask']:
                        if strike < atm_strike:
                            styles[i] = 'background-color: #fdf5e6;' # Subtle yellow for Call ITM
                        elif strike > atm_strike:
                            styles[i] = 'background-color: #ffffff;' # White for Call OTM
                    elif col in ['Put_Bid', 'Put_Ask']:
                        if strike > atm_strike:
                            styles[i] = 'background-color: #fdf5e6;' # Subtle yellow for Put ITM
                        elif strike < atm_strike:
                            styles[i] = 'background-color: #ffffff;' # White for Put OTM
                    elif col == 'STRIKE':
                        styles[i] = 'font-weight: bold; background-color: #e9ecef;' # Light grey for Strike column
                        
                return styles
            
            # Helper to handle missing columns gracefully
            format_dict = {'STRIKE': '{:.0f}'}
            for col in ['Call_Bid', 'Call_Ask', 'Put_Bid', 'Put_Ask']:
                if col in chain.columns:
                    format_dict[col] = '{:.2f}'

            styled = chain.style.apply(style_option_chain, axis=1).format(format_dict, na_rep='—').hide(axis='index')

            price_display = f"{open_price:.2f}" if open_price else "N/A"
            time_display = selected_time[11:16] if selected_time else "N/A"
            volume_display = f"{int(volume_val):,}" if 'volume_val' in locals() and pd.notna(volume_val) else "N/A"

            header = f"""
            <div class="text-center mb-4">
                <h4 class="text-primary fw-bold">ATM Strike: {atm_strike}</h4>
                <p class="text-muted">
                    Open Price: <span class="badge bg-primary fs-6">{price_display}</span> | 
                    Volume: <strong>{volume_display}</strong> | 
                    Time: <strong>{time_display}</strong> | 
                    Expiry: <strong>{selected_expiry_display}</strong>
                </p>
            </div>
            """
            chain_html = header + styled.set_table_attributes(
                'class="table table-bordered table-hover text-center table-option-chain w-100"'
            ).to_html()

    except Exception as e:
        traceback.print_exc()
        chain_html = f"<p class='text-danger'>Error building option chain: {str(e)}</p>"

    # ── Charts ───────────────────────────────────────────────────────
    try:
        print(f"DEBUG: Starting chart generation for {selected_date}")
        if index_df.empty:
            print("DEBUG: index_df is empty, skipping charts")
        else:
            price_df = index_df.copy()
            # Normalize timestamps for matching: YYYY-MM-DD HH:MM
            price_df['dt'] = pd.to_datetime(price_df['datetime_UTC'], errors='coerce')
            price_df = price_df.dropna(subset=['dt']).sort_values('dt')
            price_df['match_time'] = price_df['dt'].dt.strftime('%Y-%m-%d %H:%M')

            session_mask = (
                (price_df['dt'].dt.time >= pd.to_datetime('13:30').time()) &
                (price_df['dt'].dt.time <= pd.to_datetime('20:15').time())
            )
            filtered_df = price_df[session_mask]

            if filtered_df.empty:
                print("DEBUG: filtered_df (13:30-20:15) is empty")
            else:
                times = filtered_df['dt'].dt.strftime('%H:%M').tolist()
                prices = filtered_df['open'].astype(float).tolist()

                # Price chart
                try:
                    fig_price = go.Figure()
                    fig_price.add_trace(go.Scatter(
                        x=times, y=prices,
                        mode='lines',
                        line=dict(color='#0066ff', width=2, shape='spline', smoothing=1.3),
                        name='SPY Price'
                    ))
                    fig_price.update_layout(
                        title=f"SPY Price – {trading_date_obj.strftime('%d %b %Y')} (13:30–20:15 UTC)",
                        xaxis_title="Time (UTC)", yaxis_title="Price ($)",
                        template="simple_white", height=600, hovermode="x unified"
                    )
                    fig_price.update_xaxes(tickmode='array',
                                          tickvals=['13:00','14:00','15:00','16:00','17:00','18:00','19:00','20:00'],
                                          ticktext=['13:00','14:00','15:00','16:00','17:00','18:00','19:00','20:00'])
                    price_chart = json.dumps(fig_price, cls=PlotlyJSONEncoder)
                except Exception as e:
                    print(f"ERROR: Price chart failed: {e}")

                # Straddle charts
                try:
                    opt_df = pd.read_sql(
                        text("""
                            SELECT UTC_MINUTE, STRIKE, OPTION_TYPE, bid_open, ask_open, EXPIRY_DATE
                            FROM option_data
                            WHERE UTC_MINUTE LIKE :day_prefix
                        """),
                        engine, params={"day_prefix": f"{utc_minute_date}%"}
                    )

                    if opt_df.empty:
                        print(f"DEBUG: No option data found for {utc_minute_date}")
                    else:
                        opt_df['STRIKE'] = pd.to_numeric(opt_df['STRIKE'], errors='coerce')
                        opt_df['bid_open'] = pd.to_numeric(opt_df['bid_open'], errors='coerce')
                        opt_df['ask_open'] = pd.to_numeric(opt_df['ask_open'], errors='coerce')
                        opt_df = opt_df.dropna(subset=['STRIKE', 'bid_open', 'ask_open', 'EXPIRY_DATE'])
                        
                        # Normalize timestamps for matching: YYYY-MM-DD HH:MM
                        opt_df['dt'] = pd.to_datetime(opt_df['UTC_MINUTE'], errors='coerce')
                        opt_df['match_time'] = opt_df['dt'].dt.strftime('%Y-%m-%d %H:%M')
                        opt_df = opt_df.drop(columns=['dt']) # clean up

                        opt_by_time = opt_df.groupby('match_time')

                        dte_list = [
                            ('0DTE', trading_date_obj),
                            ('1DTE', trading_date_obj + timedelta(days=1)),
                            ('2DTE', trading_date_obj + timedelta(days=2))
                        ]

                        color_list = ["#ea170c", "#2e21e0", "#2edb2e"]
                        fig_straddle = go.Figure()
                        all_straddle_data = {}

                        for i, (dte_label, exp_date) in enumerate(dte_list):
                            exp_str = exp_date.strftime("%Y-%m-%d")
                            straddle_prices = []
                            used_strikes = []

                            for _, row in filtered_df.iterrows():
                                underlying = float(row['open']) if pd.notna(row['open']) else 0
                                match_time = row['match_time']

                                if match_time not in opt_by_time.groups:
                                    straddle_prices.append(np.nan)
                                    used_strikes.append(None)
                                    continue

                                time_group = opt_by_time.get_group(match_time)
                                exp_group = time_group[time_group['EXPIRY_DATE'] == exp_str]

                                if exp_group.empty:
                                    straddle_prices.append(np.nan)
                                    used_strikes.append(None)
                                    continue

                                avail_strikes = exp_group['STRIKE'].unique()
                                if len(avail_strikes) == 0:
                                    straddle_prices.append(np.nan)
                                    used_strikes.append(None)
                                    continue

                                closest = avail_strikes[np.argmin(np.abs(avail_strikes - underlying))]
                                strike_data = exp_group[exp_group['STRIKE'] == closest]

                                call = strike_data[strike_data['OPTION_TYPE'] == 'C']
                                put = strike_data[strike_data['OPTION_TYPE'] == 'P']

                                if not call.empty and not put.empty:
                                    mid_call = (call.iloc[0]['bid_open'] + call.iloc[0]['ask_open']) / 2
                                    mid_put = (put.iloc[0]['bid_open'] + put.iloc[0]['ask_open']) / 2
                                    straddle_prices.append(mid_call + mid_put)
                                    used_strikes.append(closest)
                                else:
                                    straddle_prices.append(np.nan)
                                    used_strikes.append(closest)

                            all_straddle_data[dte_label] = (straddle_prices, used_strikes)

                            fig_straddle.add_trace(go.Scatter(
                                x=times,
                                y=straddle_prices,
                                mode='lines',
                                line=dict(color=color_list[i], width=2, shape='spline', smoothing=1.3),
                                name=f'{dte_label} ATM Premium',
                                visible=(dte_label == selected_expiry),
                                hovertemplate=(
                                    '<b>Time:</b> %{x}<br>'
                                    '<b>Premium:</b> $%{y:.2f}<br>'
                                    '<b>Underlying:</b> $%{customdata[0]:.2f}<br>'
                                    '<b>Strike:</b> %{customdata[1]}<extra></extra>'
                                ),
                                customdata=list(zip(prices, used_strikes))
                            ))

                        fig_straddle.update_layout(
                            title=f"ATM Premium – {trading_date_obj.strftime('%d %b %Y')}",
                            xaxis_title="Time (UTC)", yaxis_title="Price ($)",
                            template="simple_white", height=650,
                            hovermode="x unified", showlegend=True,
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                        )
                        fig_straddle.update_xaxes(
                            tickmode='array',
                            tickvals=['13:00','14:00','15:00','16:00','17:00','18:00','19:00','20:00'],
                            ticktext=['13:00','14:00','15:00','16:00','17:00','18:00','19:00','20:00']
                        )
                        atm_straddle_chart = json.dumps(fig_straddle, cls=PlotlyJSONEncoder)

                        # Straddle Comparison Chart
                        try:
                            fig_compare = go.Figure()
                            colors_compare = ["#d83813", "#1934cd", "#52ff33"]
                            dte_options = ['0DTE', '1DTE', '2DTE']

                            if selected_comparison == '0v1':
                                show_dtes = ['0DTE', '1DTE']
                                title_suffix = "0DTE vs 1DTE"
                            elif selected_comparison == '0v2':
                                show_dtes = ['0DTE', '2DTE']
                                title_suffix = "0DTE vs 2DTE"
                            elif selected_comparison == '1v2':
                                show_dtes = ['1DTE', '2DTE']
                                title_suffix = "1DTE vs 2DTE"
                            else:
                                show_dtes = ['0DTE', '1DTE', '2DTE']
                                title_suffix = "0DTE vs 1DTE vs 2DTE"

                            for i, label in enumerate(dte_options):
                                if label in show_dtes and label in all_straddle_data:
                                    s_prices, u_strikes = all_straddle_data[label]
                                    fig_compare.add_trace(go.Scatter(
                                        x=times,
                                        y=s_prices,
                                        mode='lines',
                                        line=dict(color=colors_compare[i], width=2, shape='spline', smoothing=1.3),
                                        name=f'{label} ATM Premium',
                                        hovertemplate=(
                                            '<b>Time:</b> %{x}<br>'
                                            '<b>Price:</b> $%{y:.2f}<br>'
                                            '<b>Underlying:</b> $%{customdata[0]:.2f}<br>'
                                            '<b>Strike:</b> %{customdata[1]}<extra></extra>'
                                        ),
                                        customdata=list(zip(prices, u_strikes))
                                    ))

                            fig_compare.update_layout(
                                title=f"ATM Premium Comparison ({title_suffix}) – {trading_date_obj.strftime('%d %b %Y')}",
                                xaxis_title="Time (UTC)", yaxis_title="Premium ($)",
                                template="simple_white", height=680,
                                hovermode="x unified", showlegend=True,
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                            )
                            fig_compare.update_xaxes(
                                tickmode='array',
                                tickvals=['13:00','14:00','15:00','16:00','17:00','18:00','19:00','20:00'],
                                ticktext=['13:00','14:00','15:00','16:00','17:00','18:00','19:00','20:00']
                            )
                            straddle_comparison_chart = json.dumps(fig_compare, cls=PlotlyJSONEncoder)
                        except Exception as e:
                            print(f"ERROR: Comparison chart failed: {e}")

                except Exception as e:
                    print(f"ERROR: Straddle/Comparison charts failed: {e}")
                    import traceback
                    traceback.print_exc()

    except Exception as e:
        print(f"ERROR: General chart generation block failed: {e}")
        import traceback
        traceback.print_exc()

    return render_template(
        'options_chain.html',
        dates=available_dates,
        formatted_dates=formatted_dates,
        selected_date=selected_date,
        times=available_times,
        filtered_times=filtered_times,
        selected_time=selected_time,
        selected_expiry=selected_expiry,
        strike_steps=strike_steps,
        atm_strike=atm_strike,
        chain=chain_html,
        selected_expiry_display=selected_expiry_display,
        open_price=open_price,
        price_chart=price_chart,
        atm_straddle_chart=atm_straddle_chart,
        straddle_comparison_chart=straddle_comparison_chart,
        selected_comparison=selected_comparison,
        slider_value=slider_value
    )


if __name__ == '__main__':
    app.run(
        host=APP_CONFIG['HOST'],
        port=APP_CONFIG['PORT'],
        debug=APP_CONFIG['DEBUG']
    )