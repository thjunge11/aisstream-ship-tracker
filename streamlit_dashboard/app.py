import os
import time

import pandas as pd
import plotly.express as px
import psycopg2
import streamlit as st
from psycopg2.extras import RealDictCursor

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AIS Ship Tracker – Analytics",
    page_icon="⚓",
    layout="wide",
)

# ── DB connection ─────────────────────────────────────────────────────────────
@st.cache_resource
def get_connection():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", 5432)),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def query(sql: str, params=None) -> pd.DataFrame:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return pd.DataFrame(rows)
    except Exception:
        # Reconnect once on stale connection
        conn.reset()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return pd.DataFrame(rows)


# ── Header ────────────────────────────────────────────────────────────────────
st.title("⚓ AIS Ship Tracker – Analytics Dashboard")
st.caption(f"Last refreshed: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")

with st.sidebar:
    st.header("Controls")
    top_n = st.slider("Top-N destinations / statuses", min_value=5, max_value=50, value=20)
    min_ships = st.slider("Min ships per ship_type (dimensions chart)", min_value=1, max_value=50, value=3)
    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.markdown(
        "Data is read directly from **PostgreSQL**.\n\n"
        "All four charts use live data — hit *Refresh* to update."
    )

st.divider()

# ════════════════════════════════════════════════════════════════════════════
# 1. Average dimensions (length / width / draught) grouped by ship_type
# ════════════════════════════════════════════════════════════════════════════
st.subheader("1 · Average Vessel Dimensions by Ship Type")
st.caption("Source: `ships_static_data`")

@st.cache_data(ttl=120)
def load_dimensions(min_ships_: int) -> pd.DataFrame:
    return query(
        """
        SELECT
            ship_type,
            ROUND(AVG(length_m)::numeric,  1) AS avg_length_m,
            ROUND(AVG(width_m)::numeric,   1) AS avg_width_m,
            ROUND(AVG(draught_m)::numeric, 2) AS avg_draught_m,
            COUNT(*)                           AS ship_count
        FROM ships_static_data
        WHERE ship_type   IS NOT NULL AND ship_type   <> ''
          AND length_m    IS NOT NULL
          AND width_m     IS NOT NULL
          AND draught_m   IS NOT NULL
        GROUP BY ship_type
        HAVING COUNT(*) >= %s
        ORDER BY ship_count DESC
        """,
        (min_ships_,),
    )


df_dim = load_dimensions(min_ships)

if df_dim.empty:
    st.info("No dimension data yet – waiting for ships_static_data to populate.")
else:
    df_dim_melted = df_dim.melt(
        id_vars=["ship_type", "ship_count"],
        value_vars=["avg_length_m", "avg_width_m", "avg_draught_m"],
        var_name="Dimension",
        value_name="Metres",
    )
    label_map = {
        "avg_length_m":  "Avg Length (m)",
        "avg_width_m":   "Avg Width (m)",
        "avg_draught_m": "Avg Draught (m)",
    }
    df_dim_melted["Dimension"] = df_dim_melted["Dimension"].map(label_map)

    fig1 = px.bar(
        df_dim_melted,
        x="ship_type",
        y="Metres",
        color="Dimension",
        barmode="group",
        labels={"ship_type": "Ship Type"},
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig1.update_layout(xaxis_tickangle=-35, margin=dict(b=120))
    st.plotly_chart(fig1, use_container_width=True)

    with st.expander("View table"):
        st.dataframe(
            df_dim.rename(columns={
                "ship_type":    "Ship Type",
                "avg_length_m": "Avg Length (m)",
                "avg_width_m":  "Avg Width (m)",
                "avg_draught_m":"Avg Draught (m)",
                "ship_count":   "# Ships",
            }),
            use_container_width=True,
            hide_index=True,
        )

st.divider()

# ════════════════════════════════════════════════════════════════════════════
# 2. Max dimensions (length / width / draught) grouped by ship_type
# ════════════════════════════════════════════════════════════════════════════
st.subheader("2 · Max Vessel Dimensions by Ship Type")
st.caption("Source: `ships_static_data`")

@st.cache_data(ttl=120)
def load_max_dimensions(min_ships_: int) -> pd.DataFrame:
    return query(
        """
        WITH ranked AS (
            SELECT
                ship_id,
                ship_type,
                length_m,
                width_m,
                draught_m,
                COUNT(*) OVER (PARTITION BY ship_type) AS ship_count,
                ROW_NUMBER() OVER (
                    PARTITION BY ship_type ORDER BY length_m DESC
                ) AS rn
            FROM ships_static_data
            WHERE ship_type  IS NOT NULL AND ship_type  <> ''
              AND length_m   IS NOT NULL
              AND width_m    IS NOT NULL
              AND draught_m  IS NOT NULL
        )
        SELECT
            ship_id,
            ship_type,
            ROUND(length_m::numeric,  1) AS max_length_m,
            ROUND(width_m::numeric,   1) AS max_width_m,
            ROUND(draught_m::numeric, 2) AS max_draught_m,
            ship_count
        FROM ranked
        WHERE rn = 1 AND ship_count >= %s
        ORDER BY ship_count DESC
        """,
        (min_ships_,),
    )


df_dim = load_max_dimensions(min_ships)

if df_dim.empty:
    st.info("No dimension data yet – waiting for ships_static_data to populate.")
else:
    df_dim_melted = df_dim.melt(
        id_vars=["ship_type", "ship_count"],
        value_vars=["max_length_m", "max_width_m", "max_draught_m"],
        var_name="Dimension",
        value_name="Metres",
    )
    label_map = {
        "max_length_m":  "Max Length (m)",
        "max_width_m":   "Max Width (m)",
        "max_draught_m": "Max Draught (m)",
    }
    df_dim_melted["Dimension"] = df_dim_melted["Dimension"].map(label_map)

    fig1 = px.bar(
        df_dim_melted,
        x="ship_type",
        y="Metres",
        color="Dimension",
        barmode="group",
        labels={"ship_type": "Ship Type"},
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig1.update_layout(xaxis_tickangle=-35, margin=dict(b=120))
    st.plotly_chart(fig1, use_container_width=True)

    with st.expander("View table"):
        st.dataframe(
            df_dim.rename(columns={
                "ship_id":      "Ship ID",
                "ship_type":    "Ship Type",
                "max_length_m": "Max Length (m)",
                "max_width_m":  "Max Width (m)",
                "max_draught_m":"Max Draught (m)",
                "ship_count":   "# Ships",
            }),
            use_container_width=True,
            hide_index=True,
        )

st.divider()

# ════════════════════════════════════════════════════════════════════════════
# 3. Ship count grouped by destination
# ════════════════════════════════════════════════════════════════════════════
st.subheader("3 · Ships by Destination (Top N)")
st.caption("Source: `ships_static_data`")

@st.cache_data(ttl=120)
def load_destinations(top_n_: int) -> pd.DataFrame:
    return query(
        """
        SELECT
            destination,
            COUNT(*) AS ship_count
        FROM ships_static_data
        WHERE destination IS NOT NULL AND destination <> ''
        GROUP BY destination
        ORDER BY ship_count DESC
        LIMIT %s
        """,
        (top_n_,),
    )


df_dest = load_destinations(top_n)

if df_dest.empty:
    st.info("No destination data yet.")
else:
    fig2 = px.bar(
        df_dest,
        x="ship_count",
        y="destination",
        orientation="h",
        labels={"ship_count": "Number of Ships", "destination": "Destination"},
        color="ship_count",
        color_continuous_scale="Blues",
    )
    fig2.update_layout(yaxis={"categoryorder": "total ascending"}, margin=dict(l=160))
    st.plotly_chart(fig2, use_container_width=True)

    with st.expander("View table"):
        st.dataframe(
            df_dest.rename(columns={"destination": "Destination", "ship_count": "# Ships"}),
            use_container_width=True,
            hide_index=True,
        )

st.divider()

# ════════════════════════════════════════════════════════════════════════════
# 4. Ship count grouped by navigational_status
# ════════════════════════════════════════════════════════════════════════════
st.subheader("4 · Ships by Navigational Status")
st.caption("Source: `ships_live_data`")

@st.cache_data(ttl=60)
def load_nav_status() -> pd.DataFrame:
    return query(
        """
        SELECT
            navigational_status,
            COUNT(*) AS ship_count
        FROM ships_live_data
        WHERE navigational_status IS NOT NULL AND navigational_status <> ''
        GROUP BY navigational_status
        ORDER BY ship_count DESC
        """
    )


df_nav = load_nav_status()

if df_nav.empty:
    st.info("No live data yet.")
else:
    col_bar, col_pie = st.columns(2)

    fig3a = px.bar(
        df_nav,
        x="navigational_status",
        y="ship_count",
        labels={"navigational_status": "Status", "ship_count": "Number of Ships"},
        color="ship_count",
        color_continuous_scale="Teal",
    )
    fig3a.update_layout(xaxis_tickangle=-30, margin=dict(b=140))
    col_bar.plotly_chart(fig3a, use_container_width=True)

    fig3b = px.pie(
        df_nav,
        names="navigational_status",
        values="ship_count",
        hole=0.4,
    )
    fig3b.update_traces(textposition="inside", textinfo="percent+label")
    col_pie.plotly_chart(fig3b, use_container_width=True)

    with st.expander("View table"):
        st.dataframe(
            df_nav.rename(columns={
                "navigational_status": "Navigational Status",
                "ship_count": "# Ships",
            }),
            use_container_width=True,
            hide_index=True,
        )

st.divider()

# ════════════════════════════════════════════════════════════════════════════
# 5. Average speed_over_ground (> 0) grouped by ship_type  [join]
# ════════════════════════════════════════════════════════════════════════════
st.subheader("5 · Average Speed by Ship Type  (moving ships only)")
st.caption(
    "Source: `ships_live_data` ⋈ `ships_static_data` on `ship_id` — "
    "rows where `speed_over_ground = 0` are excluded."
)

@st.cache_data(ttl=60)
def load_speed(min_ships_: int) -> pd.DataFrame:
    return query(
        """
        SELECT
            s.ship_type,
            ROUND(AVG(l.speed_over_ground)::numeric, 2) AS avg_speed_knots,
            COUNT(*)                                     AS ship_count
        FROM ships_live_data    l
        JOIN ships_static_data  s ON l.ship_id = s.ship_id
        WHERE l.speed_over_ground > 0
          AND s.ship_type IS NOT NULL AND s.ship_type <> ''
        GROUP BY s.ship_type
        HAVING COUNT(*) >= %s
        ORDER BY avg_speed_knots DESC
        """,
        (min_ships_,),
    )


df_speed = load_speed(min_ships)

if df_speed.empty:
    st.info("No speed data yet (join returned no rows).")
else:
    fig4 = px.bar(
        df_speed,
        x="ship_type",
        y="avg_speed_knots",
        labels={"ship_type": "Ship Type", "avg_speed_knots": "Avg Speed (knots)"},
        color="avg_speed_knots",
        color_continuous_scale="Sunset",
        text="avg_speed_knots",
    )
    fig4.update_traces(texttemplate="%{text:.1f} kn", textposition="outside")
    fig4.update_layout(xaxis_tickangle=-35, margin=dict(b=120))
    st.plotly_chart(fig4, use_container_width=True)

    with st.expander("View table"):
        st.dataframe(
            df_speed.rename(columns={
                "ship_type":        "Ship Type",
                "avg_speed_knots":  "Avg Speed (knots)",
                "ship_count":       "# Ships",
            }),
            use_container_width=True,
            hide_index=True,
        )
    
st.divider()

# ════════════════════════════════════════════════════════════════════════════
# 6. Max speed_over_ground (> 0) grouped by ship_type  [join]
# ════════════════════════════════════════════════════════════════════════════
st.subheader("6 · Max Speed by Ship Type  (moving ships only)")
st.caption(
    "Source: `ships_live_data` ⋈ `ships_static_data` on `ship_id`"    
)

@st.cache_data(ttl=60)
def load_max_speed(min_ships_: int) -> pd.DataFrame:
    return query(
        """
        WITH ranked AS (
            SELECT
                l.ship_id,
                s.ship_type,
                l.speed_over_ground,
                COUNT(*) OVER (PARTITION BY s.ship_type) AS ship_count,
                ROW_NUMBER() OVER (
                    PARTITION BY s.ship_type ORDER BY l.speed_over_ground DESC
                ) AS rn
            FROM ships_live_data   l
            JOIN ships_static_data s ON l.ship_id = s.ship_id
            WHERE s.ship_type IS NOT NULL AND s.ship_type <> ''
        )
        SELECT
            ship_id,
            ship_type,
            ROUND(speed_over_ground::numeric, 2) AS max_speed_knots,
            ship_count
        FROM ranked
        WHERE rn = 1 AND ship_count >= %s
        ORDER BY max_speed_knots DESC
        """,
        (min_ships_,),
    )


df_speed = load_max_speed(min_ships)

if df_speed.empty:
    st.info("No speed data yet (join returned no rows).")
else:
    fig4 = px.bar(
        df_speed,
        x="ship_type",
        y="max_speed_knots",
        labels={"ship_type": "Ship Type", "max_speed_knots": "Max Speed (knots)"},
        color="max_speed_knots",
        color_continuous_scale="Sunset",
        text="max_speed_knots",
    )
    fig4.update_traces(texttemplate="%{text:.1f} kn", textposition="outside")
    fig4.update_layout(xaxis_tickangle=-35, margin=dict(b=120))
    st.plotly_chart(fig4, use_container_width=True)

    with st.expander("View table"):
        st.dataframe(
            df_speed.rename(columns={
                "ship_id":         "Ship ID",
                "ship_type":       "Ship Type",
                "max_speed_knots": "Max Speed (knots)",
                "ship_count":      "# Ships",
            }),
            use_container_width=True,
            hide_index=True,
        )