"""
core.py — data loading, constants, Plotly chart builders.
No Streamlit imports here — pure pandas + plotly.
"""

import json
import re
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ── Color palette ─────────────────────────────────────────────────────────────
BG     = "#121214"
CARD   = "#1A1A1E"
CARD2  = "#222226"
BORDER = "#2A2A30"
TH     = "#F3F4F6"
TS     = "#9CA3AF"
TM     = "#6B7280"
FONT   = "Inter, Helvetica, Arial, sans-serif"

G = "#22D3A0"
R = "#EF4444"
B = "#5B8AF5"
P = "#A78BFA"
O = "#F59E0B"
T = "#22D3EE"
Y = "#FCD34D"

PAL = [B, G, P, O, R, T, Y, "#8AB8FF", "#96D98D", "#D9B8F1", "#FFAD73"]
DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

CLS_CLR = {
    "Специалист (Клиент)":    B,
    "Специалист (Клинер)":    "#38BDF8",
    "Специалист (Курьер)":    "#6366F1",
    "Специалист (Мульти)":    "#93C5FD",
    "Специалист":             "#94A3B8",
    "Бот (Клиент)":           G,
    "Бот (Клинер)":           "#10B981",
    "Бот":                    "#6EE7B7",
    "Рассылка (исх.)":        P,
    "Рассылка (ответ)":       "#C084FC",
    "Инициировал специалист": O,
}

_SPEC_CLASSES = [
    "Специалист", "Специалист (Клиент)", "Специалист (Клинер)",
    "Специалист (Курьер)", "Специалист (Мульти)",
]


# ── Data loading ──────────────────────────────────────────────────────────────

def load_data(url: str) -> pd.DataFrame:
    """Load from URL or local path (kept for local HDE/analytics.py usage)."""
    if url.startswith("http"):
        df = pd.read_csv(url, dtype=str, encoding="utf-8").fillna("")
    else:
        df = pd.read_csv(url, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
    return _classify(df)


def load_data_from_df(raw: pd.DataFrame) -> pd.DataFrame:
    """Classify a raw DataFrame returned by fetcher.fetch_all()."""
    df = raw.copy().fillna("").astype(str)
    # numeric columns should stay numeric after classification
    return _classify(df)


def _classify(df: pd.DataFrame) -> pd.DataFrame:
    """Add all computed classification columns to a raw tickets DataFrame."""

    df["date_created_dt"] = pd.to_datetime(df["date_created"], errors="coerce")
    df["hour"]    = df["date_created_dt"].dt.hour.astype("Int64")
    df["weekday"] = df["date_created_dt"].dt.dayofweek.astype("Int64")

    TYPE = "tcf_4_Тип обращения"
    INIT = "tcf_13_Инициировали"

    def _clean_ut(s: pd.Series) -> pd.Series:
        return s.replace({"0": "", "nan": "", "None": ""}).str.strip()

    _tcf1_col = next((c for c in df.columns if c.startswith("tcf_1_Тип юзера")), None)
    ut1 = _clean_ut(df[_tcf1_col].copy()) if _tcf1_col else pd.Series("", index=df.index)
    ut2 = _clean_ut(df["ucf_6_Тип юзера"].copy()) if "ucf_6_Тип юзера" in df.columns else pd.Series("", index=df.index)
    df["_ut"] = ut1.where(ut1 != "", ut2)

    def has_tag(t: str, tag: str) -> bool:
        try:    return tag in json.loads(t or "[]")
        except: return tag in str(t)

    df["has_wikibot"] = df["tags"].apply(lambda t: has_tag(t, "wikibot-close"))
    df["is_rass"] = df[TYPE].str.startswith("Рассылка:", na=False) if TYPE in df.columns else False
    df["is_init"] = df[INIT].str.strip() == "1" if INIT in df.columns else False
    df["is_bot"]  = (
        (df["has_wikibot"] | (df["owner_name"].str.strip() == "Бот"))
        & ~df["is_init"] & ~df["is_rass"]
    )
    df["ticket_class"] = "Специалист"
    df.loc[df["is_bot"] & (df["_ut"] == "Клиент"), "ticket_class"] = "Бот (Клиент)"
    df.loc[df["is_bot"] & (df["_ut"] == "Клинер"), "ticket_class"] = "Бот (Клинер)"
    df.loc[df["is_bot"] & ~df["_ut"].isin(["Клиент", "Клинер"]), "ticket_class"] = "Бот"
    df.loc[df["is_rass"] &  df["is_init"], "ticket_class"] = "Рассылка (исх.)"
    df.loc[df["is_rass"] & ~df["is_init"], "ticket_class"] = "Рассылка (ответ)"
    df.loc[~df["is_rass"] & df["is_init"] & ~df["is_bot"], "ticket_class"] = "Инициировал специалист"
    for _ut_val in ["Клиент", "Клинер", "Курьер", "Мульти"]:
        _m = (df["ticket_class"] == "Специалист") & (df["_ut"] == _ut_val)
        df.loc[_m, "ticket_class"] = f"Специалист ({_ut_val})"

    if TYPE in df.columns:
        df["campaign_name"] = df[TYPE].apply(
            lambda s: re.sub(r"\s*\[\d+\]$", "", s).strip() if s.startswith("Рассылка:") else ""
        )
    else:
        df["campaign_name"] = ""

    df["rate_num"] = pd.to_numeric(df.get("rate", pd.Series(dtype=str)), errors="coerce")
    df["first_response_sec"] = pd.to_numeric(
        df["first_response_sec"] if "first_response_sec" in df.columns else pd.Series(dtype=float),
        errors="coerce",
    )
    df["avg_response_sec"] = pd.to_numeric(
        df["avg_response_sec"] if "avg_response_sec" in df.columns else pd.Series(dtype=float),
        errors="coerce",
    )

    TRANS = "tcf_5_Причина перевода"
    if TRANS in df.columns:
        def _cat_trans(v) -> str | type(pd.NA):
            if pd.isna(v) or str(v).strip() == "":
                return pd.NA
            v = str(v).strip().lower()
            if "не зна" in v:  return "Не знает ответ"
            if "лимит" in v:   return "Лимит сообщений"
            if "сценари" in v: return "Требует сценарий"
            return "Другое"
        df["_trans"] = df[TRANS].apply(_cat_trans)
    else:
        df["_trans"] = pd.NA

    df["_human_trans"] = df["_trans"].notna() & ~df["is_bot"]
    return df


# ── Plotly helpers ────────────────────────────────────────────────────────────

def _base(fig: go.Figure, *, h: int | None = None, no_xgrid=False,
          no_ygrid=False, legend_bottom=False) -> go.Figure:
    lo: dict = dict(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=12, color=TS),
        title=dict(font=dict(size=13, color=TS, weight=600),
                   x=0, xanchor="left", pad=dict(l=14, t=6)),
        margin=dict(t=46, b=14, l=10, r=12),
        legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)",
                    font=dict(size=11, color=TS)),
        hoverlabel=dict(bgcolor=CARD2, font_color=TH, font_family=FONT, font_size=12,
                        bordercolor=BORDER),
    )
    if h:
        lo["height"] = h
    if legend_bottom:
        lo["legend"].update(orientation="h", x=0, y=-0.22)
    fig.update_layout(**lo)
    grid = BORDER
    fig.update_xaxes(gridcolor=grid if not no_xgrid else "rgba(0,0,0,0)",
                     zerolinecolor=BORDER, zeroline=False, showline=False,
                     tickfont=dict(size=11, color=TM), automargin=True)
    fig.update_yaxes(gridcolor=grid if not no_ygrid else "rgba(0,0,0,0)",
                     zerolinecolor=BORDER, zeroline=False, showline=False,
                     tickfont=dict(size=11, color=TS), automargin=True)
    return fig


def _fmt_sec(v) -> str:
    if pd.isna(v) or v is None:
        return "—"
    v = float(v)
    if v < 60:   return f"{v:.0f}с"
    if v < 3600: return f"{v/60:.1f}м"
    return f"{v/3600:.1f}ч"


def _daily(df: pd.DataFrame, mask=None, bins: int = 21) -> list:
    sub = df if mask is None else df.loc[mask]
    sub = sub.dropna(subset=["date_created_dt"])
    if sub.empty:
        return []
    cnt = sub.resample("D", on="date_created_dt").size()
    v = cnt.tail(bins).tolist()
    return v if len(v) >= 2 else []


# ── Chart builders ─────────────────────────────────────────────────────────────

def hour_bar(df: pd.DataFrame, title: str = "По часам", color: str | None = None) -> go.Figure:
    color = color or B
    valid = df.dropna(subset=["hour"]).copy()
    valid["hour"] = valid["hour"].astype(int)
    counts = (pd.DataFrame({"hour": range(24)})
              .merge(valid.groupby("hour").size().reset_index(name="n"), on="hour", how="left")
              .fillna(0).astype({"n": int}))
    fig = px.bar(counts, x="hour", y="n", color_discrete_sequence=[color],
                 title=title, labels={"hour": "Час", "n": ""})
    fig.update_traces(marker_line_width=0)
    return _base(fig, h=260, no_xgrid=True)


def weekday_bar(df: pd.DataFrame, title: str = "По дням", color: str | None = None) -> go.Figure:
    color = color or T
    valid = df.dropna(subset=["weekday"]).copy()
    valid["weekday"] = valid["weekday"].astype(int)
    counts = (pd.DataFrame({"weekday": range(7), "day": DAY_NAMES})
              .merge(valid.groupby("weekday").size().reset_index(name="n"), on="weekday", how="left")
              .fillna(0).astype({"n": int}))
    fig = px.bar(counts, x="day", y="n", color_discrete_sequence=[color],
                 title=title, labels={"day": "", "n": ""},
                 category_orders={"day": DAY_NAMES})
    fig.update_traces(marker_line_width=0)
    return _base(fig, h=260, no_xgrid=True)


def heatmap_day_hour(df: pd.DataFrame, title: str = "Нагрузка: день × час") -> go.Figure | None:
    valid = df.dropna(subset=["weekday", "hour"]).copy()
    if valid.empty:
        return None
    valid["weekday"] = valid["weekday"].astype(int)
    valid["hour"]    = valid["hour"].astype(int)
    pivot = (valid.groupby(["weekday", "hour"]).size()
             .unstack(fill_value=0)
             .reindex(index=range(7), columns=range(24), fill_value=0))
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=list(range(24)), y=DAY_NAMES,
        colorscale=[[0, CARD], [0.5, "rgba(91,138,245,0.5)"], [1, B]],
        showscale=False,
        hovertemplate="%{y} %{x}:00 — %{z} заявок<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=13, color=TS, weight=600),
                   x=0, xanchor="left", pad=dict(l=14, t=6)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=TS),
        height=260, margin=dict(t=46, b=14, l=10, r=12),
        xaxis=dict(tickfont=dict(size=11, color=TM)),
        yaxis=dict(tickfont=dict(size=11, color=TS)),
        hoverlabel=dict(bgcolor=CARD2, font_color=TH, font_family=FONT, font_size=12),
    )
    return fig


def heatmap_dept_hour(df: pd.DataFrame) -> go.Figure | None:
    valid = df.dropna(subset=["hour"]).copy()
    valid = valid[valid["department_name"].str.strip() != ""]
    if valid.empty:
        return None
    valid["hour"] = valid["hour"].astype(int)
    n_days = max(1, df["date_created_dt"].dt.date.nunique())
    pivot  = (valid.groupby(["department_name", "hour"]).size()
              .unstack(fill_value=0).reindex(columns=range(24), fill_value=0))
    avg = (pivot / n_days).round(1)
    fig = go.Figure(go.Heatmap(
        z=avg.values, x=list(range(24)), y=avg.index.tolist(),
        colorscale=[[0, CARD2], [0.3, "#1A2A3A"], [0.7, "#1A4A6A"], [1, T]],
        showscale=False,
        hovertemplate="Отдел: %{y}<br>Час: %{x}:00<br>Ср/день: %{z}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text="Нагрузка по отделам · среднее в день",
                   font=dict(size=13, color=TS, weight=600),
                   x=0, xanchor="left", pad=dict(l=14, t=6)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=TS),
        height=max(180, len(avg) * 44 + 80),
        margin=dict(t=46, b=14, l=140, r=12),
        xaxis=dict(tickfont=dict(size=11, color=TM)),
        yaxis=dict(tickfont=dict(size=11, color=TS), autorange="reversed"),
        hoverlabel=dict(bgcolor=CARD2, font_color=TH, font_family=FONT, font_size=12),
    )
    return fig


def hbar(series: pd.Series, title: str, color: str | None = None, limit: int = 25) -> go.Figure:
    color = color or B
    vc = series.replace("", pd.NA).dropna().value_counts().head(limit).reset_index()
    vc.columns = ["x", "n"]
    vc = vc.sort_values("n", ascending=True)
    fig = px.bar(vc, y="x", x="n", orientation="h", title=title,
                 color_discrete_sequence=[color], labels={"x": "", "n": ""})
    fig.update_traces(marker_line_width=0)
    return _base(fig, h=max(260, len(vc) * 28 + 50), no_ygrid=True)


def donut_fig(df: pd.DataFrame) -> go.Figure | None:
    seg = df["_ut"].replace("", pd.NA).dropna()
    if seg.empty:
        return None
    vc = seg.value_counts().reset_index()
    vc.columns = ["s", "n"]
    total_s = int(vc["n"].sum())
    colors = [B, G, O, P, T, Y]
    legend_labels = [
        f"{s} — {n} ({n/total_s*100:.0f}%)"
        for s, n in zip(vc["s"], vc["n"])
    ]
    fig = go.Figure(go.Pie(
        labels=legend_labels, values=vc["n"].tolist(),
        sort=False, hole=0.78,
        marker=dict(colors=colors[:len(vc)], line=dict(color=CARD, width=2)),
        textinfo="none",
        hovertemplate="%{label}<extra></extra>",
    ))
    fig.add_annotation(text=f"<b>{total_s}</b>", x=0.5, y=0.56,
                       font=dict(size=20, color=TH, family=FONT), showarrow=False)
    fig.add_annotation(text="заявок", x=0.5, y=0.41,
                       font=dict(size=11, color=TM, family=FONT), showarrow=False)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=TS),
        margin=dict(t=40, b=10, l=10, r=180),
        height=240, showlegend=True,
        legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)",
                    font=dict(size=11, color=TS), orientation="v",
                    x=1.02, y=0.5, xanchor="left", yanchor="middle"),
        title=dict(text="Тип юзера", font=dict(size=13, color=TS, weight=600),
                   x=0, xanchor="left", pad=dict(l=14, t=4)),
        hoverlabel=dict(bgcolor=CARD2, font_color=TH, font_family=FONT, font_size=12),
    )
    return fig


def csat_gauge_fig(df_sub: pd.DataFrame, label: str = "") -> go.Figure | None:
    rated = df_sub["rate_num"].dropna()
    n = len(rated)
    if n == 0:
        return None
    avg   = rated.mean()
    color = G if avg >= 4.0 else (Y if avg >= 2.5 else R)
    stars = "★" * round(avg) + "☆" * (5 - round(avg))
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=avg,
        number=dict(font=dict(color=color, size=40, family=FONT), valueformat=".2f"),
        title=dict(
            text=f"<b>{label}</b><br><span style='font-size:18px;color:{Y}'>{stars}</span>"
                 f"<br><span style='font-size:11px;color:{TM}'>{n} оценок</span>",
            font=dict(color=TS, size=13, family=FONT),
        ),
        gauge=dict(
            axis=dict(range=[1, 5], tickwidth=1, tickcolor=TM,
                      tickfont=dict(size=10, color=TM), nticks=5),
            bar=dict(color=color, thickness=0.25),
            bgcolor=CARD2,
            bordercolor=BORDER,
            steps=[
                dict(range=[1, 2.5], color=CARD),
                dict(range=[2.5, 4.0], color=CARD2),
                dict(range=[4.0, 5.0], color="rgba(34,211,160,0.06)"),
            ],
        ),
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=TS),
        height=230,
        margin=dict(t=60, b=10, l=20, r=20),
    )
    return fig


def topic_heatmap_fig(df: pd.DataFrame, topic_col: str,
                      filter_rass: bool = True, title: str = "") -> go.Figure | None:
    if filter_rass:
        sub = df[~df["is_rass"] & df[topic_col].ne("")]
    else:
        sub = df[df[topic_col].ne("")]
    if sub.empty:
        return None
    sv = sub.dropna(subset=["hour"]).copy()
    sv["hour"] = sv["hour"].astype(int)
    top_t = sv[topic_col].value_counts().head(10).index.tolist()
    sv = sv[sv[topic_col].isin(top_t)]
    pivot = (sv.groupby([topic_col, "hour"]).size()
             .unstack(fill_value=0).reindex(columns=range(24), fill_value=0))
    pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]
    y_labels = [t[:30] + "…" if len(t) > 30 else t for t in pivot.index]
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=list(range(24)), y=y_labels,
        colorscale=[[0, CARD], [0.3, "#1A2A3A"], [0.65, "#1A4A6A"], [1, T]],
        showscale=False,
        hovertemplate="Тема: %{y}<br>Час: %{x}:00<br>Заявок: %{z}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text=title or "Тематика × час",
                   font=dict(size=13, color=TS, weight=600),
                   x=0, xanchor="left", pad=dict(l=14, t=6)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=TS),
        height=max(200, len(pivot) * 38 + 80),
        margin=dict(t=46, b=14, l=10, r=12),
        xaxis=dict(tickfont=dict(size=11, color=TM)),
        yaxis=dict(tickfont=dict(size=10, color=TS), autorange="reversed"),
        hoverlabel=dict(bgcolor=CARD2, font_color=TH, font_family=FONT, font_size=12),
    )
    return fig


# ── Aggregations ──────────────────────────────────────────────────────────────

def cats_agg(df_s: pd.DataFrame, type_col: str) -> pd.DataFrame:
    """Aggregate per-category metrics for the Категории tab."""
    if df_s.empty:
        return pd.DataFrame()
    g = df_s.groupby(type_col).agg(
        count=("id", "count"),
        bot_n=("is_bot", "sum"),
        trans_n=("_human_trans", "sum"),
        avg_csat=("rate_num", "mean"),
        avg_first=("first_response_sec", "mean"),
        avg_speed=("avg_response_sec", "mean"),
        peak_h=("hour", lambda x: int(x.dropna().astype(int).value_counts().idxmax())
                if x.dropna().size > 0 else None),
    ).reset_index()
    g["bot_pct"]   = (g["bot_n"]   / g["count"] * 100).round(1)
    g["trans_pct"] = (g["trans_n"] / g["count"] * 100).round(1)
    return g.sort_values("count", ascending=False)


def cats_treemap_fig(g: pd.DataFrame, type_col: str) -> go.Figure | None:
    g30 = g.head(30).copy()
    if g30.empty:
        return None
    g30["label"] = g30[type_col].apply(lambda s: (s[:30] + "…") if len(s) > 30 else s)
    fig = px.treemap(
        g30, path=["label"], values="count",
        color="trans_pct",
        color_continuous_scale=[[0, "#1e3a5c"], [0.35, "#5B8AF5"], [0.65, O], [1, R]],
        labels={"count": "Заявок", "trans_pct": "% перевод"},
    )
    fig.update_traces(
        textfont=dict(family=FONT, size=10),
        hovertemplate="<b>%{label}</b><br>Заявок: %{value}<br>Перевод: %{color:.1f}%<extra></extra>",
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=TS),
        height=340,
        margin=dict(t=4, b=4, l=4, r=4),
        coloraxis_showscale=False,
    )
    return fig


def cats_bar_fig(g: pd.DataFrame, type_col: str, n: int = 15) -> go.Figure | None:
    g_n = g.head(n).copy()
    if g_n.empty:
        return None
    g_n = g_n.iloc[::-1]  # ascending order (largest at top)
    g_n["label"] = g_n[type_col].apply(lambda s: (s[:30] + "…") if len(s) > 30 else s)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Специалист", y=g_n["label"],
        x=(g_n["count"] - g_n["bot_n"]).clip(lower=0),
        orientation="h", marker_color=B, marker_line_width=0,
    ))
    fig.add_trace(go.Bar(
        name="Бот", y=g_n["label"], x=g_n["bot_n"],
        orientation="h", marker_color=G, marker_line_width=0,
    ))
    fig.update_layout(barmode="stack",
                      title=dict(text="Топ-15 тематик", font=dict(size=13, color=TS, weight=600),
                                 x=0, xanchor="left", pad=dict(l=14, t=6)))
    return _base(fig, h=max(280, n * 30 + 60), no_ygrid=True, legend_bottom=True)


def trend_df(df: pd.DataFrame) -> pd.DataFrame:
    """Build flat records DataFrame for the Динамика tab."""
    TYPE = "tcf_4_Тип обращения"
    rows = []
    for _, row in df.iterrows():
        dt = row.get("date_created_dt")
        if pd.isna(dt):
            continue
        topic = str(row.get(TYPE, "") or "").strip()
        if not topic or topic.lower() in ("nan", "none", ""):
            topic = "—"
        topic = re.sub(r'\s*\[\d+\]$', '', topic).strip()
        rows.append({
            "date":   dt,
            "topic":  topic,
            "is_bot": bool(row.get("is_bot", False)),
            "is_rass": bool(row.get("is_rass", False)),
            "ut":     str(row.get("_ut", "") or ""),
            "dept":   str(row.get("department_name", "") or ""),
        })
    if not rows:
        return pd.DataFrame(columns=["date", "topic", "is_bot", "is_rass", "ut", "dept"])
    rdf = pd.DataFrame(rows)
    rdf["date"] = pd.to_datetime(rdf["date"])
    return rdf
