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

# ═══════════════════════════════════════════════════════════════════
#                    DATABASE CONNECTION
# ═══════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("🔌 INITIALIZING DATABASE CONNECTION")
print("=" * 70)

# Global flags for database health
DB_CONNECTED = False
engine = None
inspector = None

try:
    print("🔄 Creating database engine...")
    engine = create_engine(
        APP_CONFIG['DATABASE_URL'],
        pool_pre_ping=True,  # Verify connections before using
        pool_recycle=3600,   # Recycle connections after 1 hour
        pool_size=10,        # Connection pool size
        max_overflow=20,     # Max overflow connections
        echo=False           # Set to True for SQL query logging
    )
    
    print("🔄 Creating database inspector...")
    inspector = inspect(engine)
    
    print("🔄 Testing database connection...")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1 as test"))
        test_value = result.scalar()
        if test_value == 1:
            DB_CONNECTED = True
            print("✅ Database connection successful!")
            print("✅ Connection test passed!")
        else:
            print("⚠️  Database responded but test query failed")
    
except Exception as e:
    error_type = type(e).__name__
    error_msg = str(e)
    
    print(f"❌ Database connection failed: {error_type}", file=sys.stderr)
    print(f"❌ Error details: {error_msg}", file=sys.stderr)
    
    if APP_CONFIG.get('IS_PRODUCTION'):
        print("\n" + "🚨" * 35, file=sys.stderr)
        print("⚠️  RUNNING IN PRODUCTION WITHOUT DATABASE CONNECTION!", file=sys.stderr)
        print("⚠️  Please configure DATABASE_URL environment variable.", file=sys.stderr)
        print("🚨" * 35 + "\n", file=sys.stderr)
    else:
        print("\n⚠️  Local database not available. Some features may not work.", file=sys.stderr)
    
    # Create engine anyway to prevent import errors (queries will fail gracefully)
    if not engine:
        try:
            engine = create_engine(APP_CONFIG['DATABASE_URL'], pool_pre_ping=True)
            inspector = inspect(engine)
        except Exception as inner_e:
            print(f"❌ Failed to create database engine: {inner_e}", file=sys.stderr)

print("=" * 70 + "\n")



# ───────────────────────────────────────────────────────────────
#         AUTOMATIC INDEX CREATION
# ───────────────────────────────────────────────────────────────
def ensure_indexes():
    try:
        with engine.connect() as conn:
            if inspector.has_table('index_data'):
                indexes = {idx['name'] for idx in inspector.get_indexes('index_data')}
                if 'idx_datetime_utc' not in indexes:
                    conn.execute(text("ALTER TABLE index_data ADD INDEX idx_datetime_utc (datetime_UTC)"))

            if inspector.has_table('option_data'):
                indexes = {idx['name'] for idx in inspector.get_indexes('option_data')}
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
    if not inspector.has_table(table_name):
        return
    try:
        existing_cols = {col['name'] for col in inspector.get_columns(table_name)}
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
            
            with engine.connect() as conn:
                conn.execute(text(f"CREATE TABLE IF NOT EXISTS {table_name} (id INT AUTO_INCREMENT PRIMARY KEY)"))
                conn.commit()

            for file in files:
                if not file.filename.lower().endswith('.csv'):
                    details.append(f"❌ {file.filename} - Not a CSV")
                    fail_count += 1
                    continue

                try:
                    # Read CSV directly from upload stream
                    df = pd.read_csv(file.stream)
                    if req_col not in df.columns:
                        raise ValueError(f"Missing required column: {req_col}")

                    original_count = len(df)

                    if upload_type == 'index':
                        df['datetime_UTC'] = pd.to_datetime(df['datetime_UTC'], errors='coerce')
                        df = df.dropna(subset=['datetime_UTC'])
                        df['datetime_UTC'] = df['datetime_UTC'].dt.strftime('%d-%m-%Y %H:%M')
                    else:
                        df['UTC_MINUTE'] = pd.to_datetime(df['UTC_MINUTE'], unit='s', utc=True)\
                                            .dt.strftime('%d-%m-%Y %H:%M')
                        df = df[~pd.to_datetime(df['UTC_MINUTE'], format='%d-%m-%Y %H:%M', errors='coerce').isna()]

                    processed_count = len(df)
                    if processed_count == 0:
                        raise ValueError("No valid rows after preprocessing")

                    add_missing_columns(table_name, df)
                    df.to_sql(table_name, engine, if_exists='append', index=False)

                    success_count += 1
                    dropped = original_count - processed_count
                    details.append(f"✅ {file.filename} - {original_count} → {processed_count} ({dropped} invalid)")

                except Exception:
                    fail_count += 1
                    details.append(f"❌ {file.filename} - Processing failed")

            alert_class = 'alert-success' if success_count > 0 else 'alert-danger'
            message = f"""
            <div class="alert {alert_class}">
                <strong>Upload Complete!</strong><br>
                Success: <strong>{success_count}</strong> | Failed: <strong>{fail_count}</strong><br><br>
                <small>{'<br>'.join(details)}</small>
            </div>
            """

    return render_template('index.html', message=message)


@app.route('/view')
def view_data():
    index_table = '<p>No index data yet.</p>'
    option_table = '<p>No option data yet.</p>'

    try:
        if inspector.has_table('index_data'):
            df = pd.read_sql('SELECT * FROM index_data ORDER BY id DESC LIMIT 10', engine)
            if not df.empty:
                index_table = df.to_html(classes='table table-striped table-bordered', index=False)
    except Exception:
        index_table = '<p>Error reading preview</p>'

    try:
        if inspector.has_table('option_data'):
            df = pd.read_sql('SELECT * FROM option_data ORDER BY id DESC LIMIT 10', engine)
            if not df.empty:
                option_table = df.to_html(classes='table table-striped table-bordered', index=False)
    except Exception:
        option_table = '<p>Error reading preview</p>'

    return render_template('view.html', index_table=index_table, option_table=option_table)


@app.route('/options_chain', methods=['GET'])
def options_chain():
    available_dates = []
    formatted_dates = []

    try:
        date_query = """
            SELECT DISTINCT SUBSTR(datetime_UTC, 1, 10) AS trade_date
            FROM index_data
            WHERE datetime_UTC IS NOT NULL
            ORDER BY trade_date DESC
        """
        df_dates = pd.read_sql(date_query, engine)
        if not df_dates.empty:
            unique_dates = pd.to_datetime(df_dates['trade_date'], format='%d-%m-%Y', errors='coerce').dropna()
            available_dates = unique_dates.dt.strftime('%Y-%m-%d').tolist()
            formatted_dates = unique_dates.dt.strftime('%d %b %Y').tolist()
    except Exception:
        pass

    selected_date = request.args.get('date') or (available_dates[0] if available_dates else None)
    selected_time = request.args.get('time')
    selected_expiry = request.args.get('expiry', '0DTE')
    strike_steps = int(request.args.get('steps', 10))
    selected_comparison = request.args.get('compare', 'all')

    available_times = []
    filtered_times = []
    atm_strike = None
    open_price = None
    chain_html = "<p class='text-center text-muted py-5 lead'>Select a date and time to view the option chain.</p>"
    selected_expiry_display = None
    price_chart = None
    atm_straddle_chart = None
    straddle_comparison_chart = None
    slider_value = 0

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
    utc_minute_date = trading_date_obj.strftime("%d-%m-%Y")

    # Load full day index data
    try:
        index_df = pd.read_sql(
            text("SELECT datetime_UTC, `open` FROM index_data WHERE datetime_UTC LIKE :day ORDER BY datetime_UTC"),
            engine, params={"day": f"{utc_minute_date} %"}
        )

        if index_df.empty:
            chain_html = "<p class='text-danger'>No index data found for this date.</p>"
            return render_template('options_chain.html',
                                 dates=available_dates, formatted_dates=formatted_dates,
                                 selected_date=selected_date, times=available_times, filtered_times=filtered_times,
                                 selected_time=selected_time, selected_expiry=selected_expiry, strike_steps=strike_steps,
                                 atm_strike=atm_strike, chain=chain_html, selected_expiry_display=selected_expiry_display,
                                 open_price=open_price, price_chart=price_chart,
                                 atm_straddle_chart=atm_straddle_chart, straddle_comparison_chart=straddle_comparison_chart,
                                 selected_comparison=selected_comparison, slider_value=slider_value)

        available_times = index_df['datetime_UTC'].tolist()

        if not selected_time and available_times:
            selected_time = available_times[0]

        filtered_times = [t for t in available_times if '13:30' <= t[11:16] <= '20:15']
        filtered_times.sort()

        if filtered_times:
            if selected_time not in filtered_times:
                selected_time = filtered_times[-1]
            slider_value = filtered_times.index(selected_time)

        current_row = index_df[index_df['datetime_UTC'] == selected_time]
        if not current_row.empty:
            open_price = float(current_row.iloc[0]['open'])
            frac = open_price % 1
            atm_strike = int(np.floor(open_price)) if frac < 0.5 else int(np.ceil(open_price))

    except Exception:
        chain_html = "<p class='text-danger'>Error loading price data</p>"

    # Fast Option Chain
    if atm_strike is not None:
        try:
            exp_offset = {'0DTE': 0, '1DTE': 1, '2DTE': 2}.get(selected_expiry, 0)
            expiry_date_obj = trading_date_obj + timedelta(days=exp_offset)
            expiry_str = expiry_date_obj.strftime("%Y-%m-%d")
            selected_expiry_display = f"{expiry_str} ({selected_expiry})"

            opt_df_chain = pd.read_sql(
                text("""
                    SELECT STRIKE, OPTION_TYPE, bid_open, ask_open
                    FROM option_data
                    WHERE UTC_MINUTE = :tm AND EXPIRY_DATE = :exp
                    ORDER BY STRIKE
                """),
                engine, params={"tm": selected_time, "exp": expiry_str}
            )

            if opt_df_chain.empty:
                chain_html = f"""
                <div class="text-center py-5">
                    <p class="lead text-muted">No options found for this minute</p>
                    <p>Time: {selected_time[11:16]} | Expiry: {selected_expiry_display}</p>
                    <p>ATM: {atm_strike}</p>
                </div>
                """
            else:
                opt_df_chain['STRIKE'] = pd.to_numeric(opt_df_chain['STRIKE'], errors='coerce')
                opt_df_chain['bid_open'] = pd.to_numeric(opt_df_chain['bid_open'], errors='coerce')
                opt_df_chain['ask_open'] = pd.to_numeric(opt_df_chain['ask_open'], errors='coerce')

                lower = atm_strike - strike_steps
                upper = atm_strike + strike_steps

                df_option = opt_df_chain[opt_df_chain['STRIKE'].between(lower, upper)]

                if df_option.empty:
                    chain_html = "<p class='text-center'>No options in selected strike range</p>"
                else:
                    pivot = df_option.pivot_table(
                        index='STRIKE', columns='OPTION_TYPE',
                        values=['bid_open', 'ask_open'], aggfunc='first'
                    )
                    pivot.columns = ['_'.join(col).strip() for col in pivot.columns.values]
                    pivot = pivot.reset_index()

                    pivot.rename(columns={
                        'bid_open_C': 'Call_Bid', 'ask_open_C': 'Call_Ask',
                        'bid_open_P': 'Put_Bid', 'ask_open_P': 'Put_Ask'
                    }, inplace=True)

                    all_strikes = pd.DataFrame({'STRIKE': range(lower, upper + 1)})
                    chain = pd.merge(all_strikes, pivot, on='STRIKE', how='left')
                    chain = chain[['Call_Bid', 'Call_Ask', 'STRIKE', 'Put_Bid', 'Put_Ask']]

                    for col in ['Call_Bid', 'Call_Ask', 'Put_Bid', 'Put_Ask']:
                        chain[col] = pd.to_numeric(chain[col], errors='coerce')

                    def highlight_atm(row):
                        return ['background-color: #fffbe6; font-weight: bold;' if row['STRIKE'] == atm_strike else '' for _ in row]

                    styled = chain.style.apply(highlight_atm, axis=1).format({
                        'Call_Bid': '{:.2f}', 'Call_Ask': '{:.2f}',
                        'Put_Bid': '{:.2f}', 'Put_Ask': '{:.2f}',
                        'STRIKE': '{:.0f}'
                    }, na_rep='—')

                    header = f"""
                    <div class="text-center mb-4">
                        <h4 class="text-primary fw-bold">ATM Strike: {atm_strike}</h4>
                        <p class="text-muted">
                            Open Price: <strong>{open_price:.2f}</strong><br>
                            Time: <strong>{selected_time[11:16]}</strong> | 
                            Expiry: <strong>{selected_expiry_display}</strong> | 
                            ±{strike_steps} strikes
                        </p>
                    </div>
                    """
                    chain_html = header + styled.to_html(
                        classes='table table-sm table-hover text-center w-75 mx-auto',
                        index=False
                    )

        except Exception:
            chain_html = "<p class='text-danger'>Error building option chain</p>"

    # ── Charts ───────────────────────────────────────────────────────
    try:
        price_df = index_df.copy()
        price_df['dt'] = pd.to_datetime(price_df['datetime_UTC'], format='%d-%m-%Y %H:%M', errors='coerce')
        price_df = price_df.dropna(subset=['dt']).sort_values('dt')

        session_mask = (
            (price_df['dt'].dt.time >= pd.to_datetime('13:30').time()) &
            (price_df['dt'].dt.time <= pd.to_datetime('20:15').time())
        )
        filtered_df = price_df[session_mask]

        times = filtered_df['dt'].dt.strftime('%H:%M').tolist()
        prices = filtered_df['open'].astype(float).tolist()

        # Price chart
        fig_price = go.Figure()
        fig_price.add_trace(go.Scatter(
            x=times, y=prices,
            mode='lines',
            line=dict(color='#0066ff', width=3, shape='spline', smoothing=1.3),
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

        # Straddle charts
        if not filtered_df.empty:
            opt_df = pd.read_sql(
                text("""
                    SELECT UTC_MINUTE, STRIKE, OPTION_TYPE, bid_open, ask_open, EXPIRY_DATE
                    FROM option_data
                    WHERE UTC_MINUTE LIKE :day_prefix
                """),
                engine, params={"day_prefix": f"{utc_minute_date} %"}
            )

            if not opt_df.empty:
                opt_df['STRIKE'] = pd.to_numeric(opt_df['STRIKE'], errors='coerce')
                opt_df['bid_open'] = pd.to_numeric(opt_df['bid_open'], errors='coerce')
                opt_df['ask_open'] = pd.to_numeric(opt_df['ask_open'], errors='coerce')
                opt_df = opt_df.dropna(subset=['STRIKE', 'bid_open', 'ask_open', 'EXPIRY_DATE'])

                opt_by_time = opt_df.groupby('UTC_MINUTE')

                dte_list = [
                    ('0DTE', trading_date_obj),
                    ('1DTE', trading_date_obj + timedelta(days=1)),
                    ('2DTE', trading_date_obj + timedelta(days=2))
                ]

                color_list = ["#ea170c", "#2e21e0", "#2edb2e"]

                # ATM Straddle Chart
                fig_straddle = go.Figure()
                all_straddle_data = {}

                for i, (dte_label, exp_date) in enumerate(dte_list):
                    exp_str = exp_date.strftime("%Y-%m-%d")
                    straddle_prices = []
                    used_strikes = []

                    for _, row in filtered_df.iterrows():
                        underlying = float(row['open'])
                        utc_time = row['datetime_UTC']

                        if utc_time not in opt_by_time.groups:
                            straddle_prices.append(np.nan)
                            used_strikes.append(None)
                            continue

                        time_group = opt_by_time.get_group(utc_time)
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
                        line=dict(color=color_list[i], width=3, shape='spline', smoothing=1.3),
                        name=f'{dte_label} ATM Straddle',
                        visible=(dte_label == '0DTE'),
                        hovertemplate=(
                            '<b>Time:</b> %{x}<br>'
                            '<b>Straddle:</b> $%{y:.2f}<br>'
                            '<b>Underlying:</b> $%{customdata[0]:.2f}<br>'
                            '<b>Strike:</b> %{customdata[1]}<extra></extra>'
                        ),
                        customdata=list(zip(prices, used_strikes))
                    ))

                fig_straddle.update_layout(
                    title=f"ATM Straddle Mid Price – {trading_date_obj.strftime('%d %b %Y')}",
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
                        straddle_prices, used_strikes = all_straddle_data[label]
                        fig_compare.add_trace(go.Scatter(
                            x=times,
                            y=straddle_prices,
                            mode='lines',
                            line=dict(color=colors_compare[i], width=4, shape='spline', smoothing=1.3),
                            name=f'{label} ATM Straddle',
                            hovertemplate=(
                                '<b>Time:</b> %{x}<br>'
                                '<b>Price:</b> $%{y:.2f}<br>'
                                '<b>Underlying:</b> $%{customdata[0]:.2f}<br>'
                                '<b>Strike:</b> %{customdata[1]}<extra></extra>'
                            ),
                            customdata=list(zip(prices, used_strikes))
                        ))

                fig_compare.update_layout(
                    title=f"ATM Straddle Comparison ({title_suffix}) – {trading_date_obj.strftime('%d %b %Y')}",
                    xaxis_title="Time (UTC)", yaxis_title="Straddle Price ($)",
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

    except Exception:
        price_chart = atm_straddle_chart = straddle_comparison_chart = None

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