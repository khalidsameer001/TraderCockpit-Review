import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import yfinance as yf
import plotly.graph_objects as go
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

# ==================== PAGE CONFIG ====================
st.set_page_config(page_title="Trade Review App", layout="wide")

st.markdown("""
<style>
.block-container{
    padding-top:0.4rem;
    padding-bottom:0rem;
    padding-left:1rem;
    padding-right:1rem;
}
div[data-testid="stVerticalBlock"]{
    gap:0.35rem;
}
h1,h2,h3{
    margin-top:0rem;
    margin-bottom:0.2rem;
}
</style>
""", unsafe_allow_html=True)

# ==================== GOOGLE SHEETS ====================
UNIFIED_SHEET_ID = "1DdQCte9ba4q1kaQZY7dhwPOCKx5PF3RPHzK6-rYPmfk"

scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

from google.oauth2.service_account import Credentials

creds = Credentials.from_service_account_info(
    st.secrets["gcp_service_account"],
    scopes=scope
)

client = gspread.authorize(creds)
sheet = client.open_by_key(UNIFIED_SHEET_ID).sheet1

# ==================== LOAD DATA ====================
all_values = sheet.get_all_values()
headers = all_values[0]

df = pd.DataFrame(all_values[1:], columns=headers)
df.columns = [str(c).strip() for c in df.columns]

pending = df[
    (df.get("chart_status", "").astype(str).str.lower().str.strip() == "pending") &
    (df.get("trade_id", "") != "")
]

# ==================== SESSION STATE ====================
if "mode" not in st.session_state:
    st.session_state.mode = "Pending Trades"
if "tf" not in st.session_state:
    st.session_state.tf = "1m"

# ==================== HEADER ====================
title_col, mode_col = st.columns([3, 2])

with title_col:
    st.markdown(
    """
    <div style="
        font-size:28px;
        font-weight:700;
        margin-bottom:0px;
    ">
        Trade Review
    </div>
    """,
    unsafe_allow_html=True
)

with mode_col:
    mode = st.radio(
        "",
        ["Pending Trades", "Search Old Trades"],
        horizontal=True,
        label_visibility="collapsed"
    )
    st.session_state.mode = mode

# ==================== TRADE SELECT ====================
if mode == "Pending Trades":
    use_manual = st.checkbox("Enter Trade ID manually")

    if use_manual:
        selected_trade = st.text_input("Trade ID", placeholder="e.g. TRADE123")
    else:
        if pending.empty:
            selected_trade = st.text_input("Trade ID")
        else:
            selected_trade = st.selectbox("Select Trade", pending["trade_id"].tolist())
else:
    selected_trade = st.text_input("Trade ID", placeholder="Search any historical trade")

# ==================== GET ROW ====================
matching_row = df[df["trade_id"] == selected_trade]
if matching_row.empty:
    st.stop()

row = matching_row.iloc[0]

symbol = row.get("symbol") or row.get("Symbol", "UNKNOWN")
pnl = row.get("Realized P/L", 0)

# ==================== TIME ====================
def to_ny_time(x):
    t = pd.to_datetime(x, errors="coerce")
    if pd.isna(t):
        return None
    if t.tzinfo is None:
        return t.tz_localize("America/New_York")
    return t.tz_convert("America/New_York")

entry = to_ny_time(row.get("Open Time"))
exit_time = to_ny_time(row.get("US Date/Time"))

# ==================== TIMEFRAME ====================
def get_time_settings(tf, entry):
    if tf == "1m":
        return entry - timedelta(days=2), entry + timedelta(days=2), "1m"
    if tf == "5m":
        return entry - timedelta(days=5), entry + timedelta(days=5), "5m"
    if tf == "1H":
        return entry - timedelta(days=7), entry + timedelta(days=7), "60m"
    if tf == "1D":
        return entry - timedelta(days=365*10), datetime.now(), "1d"

# ==================== DATA CLEAN ====================
def clean(df):
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

def add_vwap(df):
    df = df.copy()
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    df["VWAP"] = (tp * df["Volume"]).cumsum() / df["Volume"].cumsum()
    return df

def format_large_number(value):
    try:
        value = float(value)

        if value >= 1_000_000_000:
            return f"{value/1_000_000_000:.1f}B"

        elif value >= 1_000_000:
            return f"{value/1_000_000:.1f}M"

        elif value >= 1_000:
            return f"{value/1_000:.1f}K"

        else:
            return f"{value:.0f}"

    except:
        return ""



# ==================== STOCK ENRICHMENT ====================

def get_stock_enrichment(symbol, chart_data):

    enrichment = {
        "Day High": "",
        "Day Low": "",
        "Day Close": "",
        "Float": "",
        "Market Cap": "",
        "Sector": ""
    }

    # -------- Day statistics from chart --------
    if chart_data is not None and not chart_data.empty:

        enrichment["Day High"] = round(
            float(chart_data["High"].max()),
            2
        )

        enrichment["Day Low"] = round(
            float(chart_data["Low"].min()),
            2
        )

        # Last candle close = day close
        enrichment["Day Close"] = round(
            float(chart_data["Close"].iloc[-1]),
            2
        )


    # -------- Yahoo Finance data --------
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        enrichment["Float"] = format_large_number(
            info.get("floatShares", "")
        )

        enrichment["Market Cap"] = format_large_number(
            info.get("marketCap", "")
        )

        enrichment["Sector"] = info.get(
            "sector",
            ""
        )

    except Exception as e:
        print("Yahoo enrichment error:", e)


    return enrichment


# ==================== DOWNLOAD DATA ====================
start_date, end_date, interval = get_time_settings(st.session_state.tf, entry)

main_data = yf.download(
    symbol,
    start=start_date,
    end=end_date,
    interval=interval,
    prepost=True,
    progress=False
)

main_data = clean(main_data)
main_data = add_vwap(main_data)


stock_enrichment = get_stock_enrichment(
    symbol,
    main_data
)

# ==================== SESSION SHADING ====================
def add_sessions(fig, df):
    start = df.index.min().normalize()
    end = df.index.max().normalize()
    days = pd.date_range(start, end, freq="D")

    for d in days:
        fig.add_vrect(
            x0=d + pd.Timedelta(hours=4),
            x1=d + pd.Timedelta(hours=9, minutes=30),
            fillcolor="rgba(255,165,0,0.08)",
            layer="below",
            line_width=0
        )

        fig.add_vrect(
            x0=d + pd.Timedelta(hours=9, minutes=30),
            x1=d + pd.Timedelta(hours=16),
            fillcolor="rgba(0,255,0,0.05)",
            layer="below",
            line_width=0
        )

        fig.add_vrect(
            x0=d + pd.Timedelta(hours=16),
            x1=d + pd.Timedelta(hours=20),
            fillcolor="rgba(255,0,0,0.05)",
            layer="below",
            line_width=0
        )

    return fig

# ==================== HEADER ====================
try:
    pnl_float = float(pnl)
except:
    pnl_float = 0

pnl_color = "#0B6623" if pnl_float > 0 else "#8B0000" if pnl_float < 0 else "white"

# ==================== HEADER ====================

try:
    pnl_float = float(pnl)
except:
    pnl_float = 0

pnl_color = "#0B6623" if pnl_float > 0 else "#8B0000" if pnl_float < 0 else "white"

# Get Long / Short
direction = row.get("Short/Long", "")

st.markdown(
    f"<h3>{symbol} | "
    f"<span style='color:{pnl_color}'>P/L: ${pnl_float:,.2f}</span> | "
    f"{direction}</h3>",
    unsafe_allow_html=True
)

# ==================== TIMEFRAME UI (ABOVE CHART) ====================
st.session_state.tf = st.radio(
    "Timeframe",
    ["1m", "5m", "1H", "1D"],
    horizontal=True
)

# ==================== CENTER LAYOUT ====================
left_pad, chart_col, right_pad = st.columns([0.2, 3.6, 0.2])

# ==================== CHART ====================
if entry and symbol and not main_data.empty:

    main_data.index = pd.to_datetime(main_data.index)

    if main_data.index.tz is None:
        main_data.index = main_data.index.tz_localize("America/New_York")
    else:
        main_data.index = main_data.index.tz_convert("America/New_York")

    # CREATE FIG FIRST (IMPORTANT FIX)
    fig = go.Figure()

    # ==================== CANDLES ====================
    fig.add_trace(go.Candlestick(
        x=main_data.index,
        open=main_data["Open"],
        high=main_data["High"],
        low=main_data["Low"],
        close=main_data["Close"],
        increasing_line_color="#00ff88",
        decreasing_line_color="#ff4d4d"
    ))

    # ==================== VWAP ====================
    fig.add_trace(go.Scatter(
        x=main_data.index,
        y=main_data["VWAP"],
        line=dict(color="#ffb347", width=2),
        name="VWAP"
    ))

    # ==================== VOLUME ====================
    fig.add_trace(go.Bar(
        x=main_data.index,
        y=main_data["Volume"],
        marker_color="rgba(120,120,120,0.35)",
        yaxis="y2"
    ))

    # ==================== ENTRY / EXIT ====================
    fig.add_vline(x=entry, line_color="green")
    if exit_time:
        fig.add_vline(x=exit_time, line_color="red")

    # ADD SESSION BACKGROUND (AFTER FIG EXISTS)
    fig = add_sessions(fig, main_data)

    # ==================== LAYOUT ====================
    fig.update_layout(
        template="plotly_dark",
        height=600,
        margin=dict(l=10, r=10, t=10, b=10),

        xaxis=dict(
            rangeslider=dict(visible=True)
        ),

        yaxis=dict(domain=[0.25, 1]),
        yaxis2=dict(domain=[0, 0.22], showgrid=False),

        showlegend=False,
        paper_bgcolor="#0E1117",
        plot_bgcolor="#0E1117"
    )

    with chart_col:
        st.plotly_chart(fig, use_container_width=True, config={
    "displayModeBar": True,
    "scrollZoom": True
})

# ==================== JOURNAL ====================

# ==================== JOURNAL ====================

st.markdown("---")

left, divider, right = st.columns([1, 0.02, 1])

with left:
    st.subheader("🧾 Trade Journal")

    before_trade = st.text_area(
        "1) Before Trade — My Thesis",
        value=row.get("Before Trade", ""),
        height=160,
        placeholder=(
            "Why did I take this trade? What was I expecting to happen?\n\n"
            "Example:\n"
            "Yesterday a Chinese small cap ran 1000%. Today multiple sympathy "
            "names are spiking. FOMO is high. This stock has weak news, "
            "expecting early squeeze then failure."
        )
    )

    after_trade = st.text_area(
        "2) After Trade — What Happened",
        value=row.get("After Trade", ""),
        height=160,
        placeholder=(
            "What actually happened compared to my expectation?\n\n"
            "Example:\n"
            "The stock spiked higher than expected and held longer. "
            "Direction was correct but entry was too early."
        )
    )

    grade_options = ["A", "B", "C", "D", "F"]

    current_grade = row.get("Grade", "")

    grade = st.selectbox(
        "3) Execution Grade",
        grade_options,
        index=grade_options.index(current_grade)
        if current_grade in grade_options
        else 0
    )

    lesson = st.text_area(
        "4) Lesson",
        value=row.get("Lesson", ""),
        height=160,
        placeholder=(
            "What is the biggest lesson from this trade?\n\n"
            "Example:\n"
            "Wait for confirmation. Do not short the first spike."
        )
    )


with right:
    st.subheader("📊 Stock Enrichment")

    float_val = st.text_input(
        "Float",
        stock_enrichment.get("Float", "")
    )

    market_cap = st.text_input(
        "Market Cap",
        stock_enrichment.get("Market Cap", "")
    )

    day_high = st.text_input(
        "Day High",
        stock_enrichment.get("Day High", "")
    )

    day_low = st.text_input(
        "Day Low",
        stock_enrichment.get("Day Low", "")
    )

    day_close = st.text_input(
        "Day Close",
        stock_enrichment.get("Day Close", "")
    )

    sector = st.text_input(
        "Sector",
        stock_enrichment.get("Sector", "")
    )


# ==================== SAVE ====================

save = st.button(
    "💾 Save Trade Update",
    use_container_width=True
)


if save:

    sheet_values = sheet.get_all_values()
    headers = sheet_values[0]

    trade_id_idx = headers.index("trade_id")

    row_index = None

    for i, r in enumerate(sheet_values[1:], start=2):
        if r[trade_id_idx] == selected_trade:
            row_index = i
            break


    if row_index:

        updates = {
            "Before Trade": before_trade,
            "After Trade": after_trade,
            "Grade": grade,
            "Lesson": lesson,

            "Float": float_val,
            "Market Cap": market_cap,
            "Day High": day_high,
            "Day Low": day_low,
            "Day Close": day_close,
            "Sector": sector,
            "chart_status": "complete"
        }


        col_map = {
            h: i + 1
            for i, h in enumerate(headers)
        }


        cells = [
            gspread.Cell(
                row_index,
                col_map[k],
                v
            )
            for k, v in updates.items()
            if k in col_map
        ]


        sheet.update_cells(cells)

        st.success("Updated successfully!")

        st.rerun()
# ==================== RAW ====================
with st.expander("Raw Row Data"):
    st.dataframe(row.to_frame())