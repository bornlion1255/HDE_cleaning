"""
app.py — HDE Analytics Streamlit dashboard.
Data is fetched directly from HDE API (no Google Sheets).
Run: streamlit run app.py
"""

import re
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import core
import fetcher

# ── Page config (must be first st call) ──────────────────────────────────────
st.set_page_config(
    page_title="HDE Analytics · qlean2",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
CSS = f"""
<style>
  .stApp {{ background-color: {core.BG}; }}
  section[data-testid="stSidebar"] {{
    background-color: {core.CARD};
    border-right: 1px solid {core.BORDER};
  }}

  /* Metric cards */
  [data-testid="metric-container"] {{
    background: {core.CARD};
    border: 1px solid {core.BORDER};
    border-radius: 10px;
    padding: 14px 18px;
  }}
  [data-testid="stMetricLabel"] {{ color: {core.TS} !important; font-size: 11px !important; text-transform: uppercase; letter-spacing: .05em; }}
  [data-testid="stMetricValue"] {{ color: {core.TH} !important; font-size: 26px !important; font-weight: 800 !important; }}

  /* Tabs */
  [data-testid="stTabs"] [data-baseweb="tab-list"] {{
    background: {core.CARD};
    border-bottom: 1px solid {core.BORDER};
    gap: 0; padding: 0 8px;
  }}
  [data-testid="stTabs"] [data-baseweb="tab"] {{
    color: {core.TM}; font-size: 13px; font-weight: 600;
    padding: 10px 18px;
    border-bottom: 2px solid transparent;
    background: transparent;
  }}
  [data-testid="stTabs"] [aria-selected="true"] {{
    color: {core.T} !important;
    border-bottom-color: {core.T} !important;
    background: transparent !important;
  }}
  [data-testid="stTabPanel"] {{ padding-top: 16px; }}

  /* Inputs */
  [data-testid="stTextInput"] input {{
    background: {core.CARD2} !important; border: 1px solid {core.BORDER} !important;
    color: {core.TH} !important; border-radius: 6px !important;
  }}
  [data-testid="stSelectbox"] > div > div {{
    background: {core.CARD2} !important; border-color: {core.BORDER} !important;
  }}

  /* Sidebar elements */
  [data-testid="stSidebar"] label {{ color: {core.TS} !important; font-size: 12px !important; }}
  [data-testid="stSidebar"] .stDateInput > label {{ color: {core.TS} !important; }}

  /* Section divider */
  .section-header {{
    font-size: 11px; font-weight: 700; letter-spacing: .08em;
    text-transform: uppercase; color: {core.TM};
    border-bottom: 1px solid {core.BORDER};
    padding-bottom: 6px; margin: 20px 0 12px;
  }}

  /* Dashboard header */
  .dash-header {{
    background: {core.CARD}; border: 1px solid {core.BORDER};
    border-radius: 10px; padding: 14px 20px;
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 16px;
  }}
  .dash-header-title {{ font-size: 17px; font-weight: 800; color: {core.TH}; }}
  .dash-header-period {{ font-size: 12px; color: {core.TM}; margin-top: 2px; }}
  .dash-header-count {{ font-size: 13px; color: {core.TS}; font-weight: 600; }}

  /* Password page */
  .auth-card {{
    max-width: 380px; margin: 8vh auto;
    background: {core.CARD}; border: 1px solid {core.BORDER};
    border-radius: 12px; padding: 36px 32px;
  }}
  .auth-title {{ font-size: 22px; font-weight: 800; color: {core.TH}; margin-bottom: 4px; }}
  .auth-sub   {{ font-size: 13px; color: {core.TM}; margin-bottom: 24px; }}

  /* Empty state */
  .empty-state {{
    text-align: center; padding: 80px 20px;
    color: {core.TM}; font-size: 14px;
  }}
  .empty-state-icon {{ font-size: 48px; margin-bottom: 16px; }}
  .empty-state-title {{ font-size: 18px; font-weight: 700; color: {core.TS}; margin-bottom: 8px; }}
</style>
"""

st.markdown(CSS, unsafe_allow_html=True)


# ── Auth ──────────────────────────────────────────────────────────────────────

def check_password() -> bool:
    if st.session_state.get("authenticated"):
        return True
    st.markdown("""
    <div class="auth-card">
      <div class="auth-title">HDE Analytics</div>
      <div class="auth-sub">qlean2 · введи пароль для входа</div>
    </div>
    """, unsafe_allow_html=True)
    pwd = st.text_input("Пароль", type="password", placeholder="Пароль...",
                        label_visibility="collapsed")
    if st.button("Войти", type="primary", use_container_width=True):
        if pwd == st.secrets["PASSWORD"]:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Неверный пароль")
    return False


# ── Sidebar: fetch controls ───────────────────────────────────────────────────

def render_sidebar() -> None:
    with st.sidebar:
        st.markdown(
            f'<div style="font-size:16px;font-weight:800;color:{core.TH};margin-bottom:4px">'
            f'HDE Analytics</div>'
            f'<div style="font-size:11px;color:{core.TM};margin-bottom:20px">qlean2</div>',
            unsafe_allow_html=True,
        )

        st.markdown(
            f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:.07em;color:{core.TM};margin-bottom:10px">Период выгрузки</div>',
            unsafe_allow_html=True,
        )

        today = date.today()
        col1, col2 = st.columns(2)
        with col1:
            date_from = st.date_input("От", value=today.replace(day=1),
                                      max_value=today, key="sb_from",
                                      label_visibility="visible")
        with col2:
            date_to = st.date_input("До", value=today,
                                    max_value=today, key="sb_to",
                                    label_visibility="visible")

        date_field = st.selectbox(
            "Дата фильтрации",
            options=["created", "updated", "closed"],
            format_func=lambda x: {"created": "По созданию", "updated": "По изменению",
                                    "closed": "По закрытию"}[x],
            key="sb_field",
        )

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        load_btn = st.button("⬇ Загрузить данные", type="primary",
                             use_container_width=True, key="load_btn")

        # Info about cached data
        if "df" in st.session_state:
            df_cached = st.session_state.df
            d_min = df_cached["date_created_dt"].min()
            d_max = df_cached["date_created_dt"].max()
            period_str = (f"{d_min.strftime('%d.%m.%Y')} — {d_max.strftime('%d.%m.%Y')}"
                          if pd.notna(d_min) else "—")
            st.markdown(
                f'<div style="margin-top:16px;padding:12px;background:{core.CARD2};'
                f'border:1px solid {core.BORDER};border-radius:8px;font-size:12px">'
                f'<div style="color:{core.TM}">Загружено:</div>'
                f'<div style="color:{core.TH};font-weight:700;margin-top:2px">'
                f'{len(df_cached):,} заявок</div>'
                f'<div style="color:{core.TM};font-size:11px;margin-top:2px">{period_str}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        if load_btn:
            if date_from > date_to:
                st.error("Дата «От» не может быть позже «До»")
                return

            messages: list[str] = []

            with st.status("Загружаю данные из HDE API...", expanded=True) as status:
                def on_status(msg: str) -> None:
                    messages.append(msg)
                    st.write(msg)

                try:
                    df_raw = fetcher.fetch_all(
                        email=st.secrets["HDE_EMAIL"],
                        api_key=st.secrets["HDE_API_KEY"],
                        date_from=date_from.strftime("%Y-%m-%d"),
                        date_to=date_to.strftime("%Y-%m-%d"),
                        date_field=date_field,
                        on_status=on_status,
                    )
                    df_processed = core.load_data_from_df(df_raw)
                    st.session_state.df = df_processed
                    status.update(label=f"Готово — {len(df_processed):,} заявок",
                                  state="complete", expanded=False)
                    st.rerun()
                except Exception as e:
                    status.update(label="Ошибка загрузки", state="error")
                    st.error(str(e))


# ── Tab renderers ─────────────────────────────────────────────────────────────

def render_kpi(df: pd.DataFrame) -> None:
    total   = len(df)
    bot_n   = int(df["is_bot"].sum())
    camp_n  = int(df["is_rass"].sum())
    human_n = int(df["ticket_class"].isin(core._SPEC_CLASSES).sum())
    init_n  = int((df["ticket_class"] == "Инициировал специалист").sum())
    spec_u  = (df[df["ticket_class"].isin(core._SPEC_CLASSES + ["Инициировал специалист"])
                  & (df["owner_name"].str.strip() != "Бот")]
               ["owner_name"].replace("", pd.NA).dropna().nunique())
    rated   = df["rate_num"].dropna()
    avg_r   = rated.mean()

    def pct(n: int) -> str:
        return f"{n/total*100:.1f}%" if total else "—"

    cols = st.columns(7)
    for col, (label, val, delta) in zip(cols, [
        ("Заявок",          f"{total:,}",                                 None),
        ("Бот",             str(bot_n),                                   pct(bot_n)),
        ("Рассылки",        str(camp_n),                                  pct(camp_n)),
        ("Человек",         str(human_n),                                 pct(human_n)),
        ("Инициировал спец.", str(init_n),                                None),
        ("CSAT",            f"{avg_r:.2f}" if pd.notna(avg_r) else "—",  f"{len(rated)} оценок"),
        ("Специалистов",    str(spec_u),                                  None),
    ]):
        col.metric(label, val, delta)

    st.markdown('<div class="section-header">Структура обращений</div>', unsafe_allow_html=True)

    cls_counts = df["ticket_class"].value_counts()
    order = [k for k in core.CLS_CLR if k in cls_counts.index]
    if order:
        fig_cls = go.Figure()
        for cls in order:
            n = int(cls_counts.get(cls, 0))
            if n == 0: continue
            fig_cls.add_trace(go.Bar(
                name=cls, x=[n], y=[""], orientation="h",
                marker_color=core.CLS_CLR[cls], marker_line_width=0,
                text=f"{n/total*100:.0f}%" if n / total >= 0.04 else "",
                textposition="inside",
                hovertemplate=f"{cls}: {n} ({n/total*100:.1f}%)<extra></extra>",
            ))
        fig_cls.update_layout(
            barmode="stack", height=56,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(t=0, b=0, l=0, r=0), showlegend=False,
            xaxis=dict(showticklabels=False, showgrid=False, zeroline=False),
            yaxis=dict(showticklabels=False, showgrid=False, zeroline=False),
        )
        st.plotly_chart(fig_cls, use_container_width=True, config={"displayModeBar": False})
        leg_cols = st.columns(min(len(order), 6))
        for i, cls in enumerate(order):
            n = int(cls_counts.get(cls, 0))
            if n == 0: continue
            leg_cols[i % len(leg_cols)].markdown(
                f'<span style="color:{core.CLS_CLR[cls]};font-size:10px">●</span> '
                f'<span style="color:{core.TS};font-size:11px">{cls} — '
                f'<b style="color:{core.TH}">{n}</b> '
                f'<span style="color:{core.TM}">({n/total*100:.1f}%)</span></span>',
                unsafe_allow_html=True,
            )

    st.markdown('<div class="section-header">Аналитика</div>', unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)

    with col1:
        fig_c = core.csat_gauge_fig(df[df["_ut"] == "Клиент"], "Клиент")
        fig_l = core.csat_gauge_fig(df[df["_ut"] == "Клинер"], "Клинер")
        if fig_c: st.plotly_chart(fig_c, use_container_width=True, config={"displayModeBar": False})
        if fig_l: st.plotly_chart(fig_l, use_container_width=True, config={"displayModeBar": False})
        if not fig_c and not fig_l: st.caption("Нет оценок")

    with col2:
        fig_d = core.donut_fig(df)
        if fig_d:
            st.plotly_chart(fig_d, use_container_width=True, config={"displayModeBar": False})
        else:
            st.caption("Нет данных по типу юзера")

    with col3:
        st.markdown(
            f'<div style="font-size:11px;font-weight:700;color:{core.TM};'
            f'text-transform:uppercase;letter-spacing:.08em;margin-bottom:10px">'
            f'Причина перевода</div>',
            unsafe_allow_html=True,
        )
        vc_tr = df["_trans"].dropna().value_counts()
        tr_total = vc_tr.sum() or 1
        any_tr = False
        for cat in ["Требует сценарий", "Не знает ответ", "Лимит сообщений", "Другое"]:
            n = int(vc_tr.get(cat, 0))
            if n == 0: continue
            any_tr = True
            pct_v = n / tr_total * 100
            color = core.R if pct_v >= 50 else (core.O if pct_v >= 20 else core.B)
            st.markdown(
                f'<div style="margin-bottom:10px">'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:3px">'
                f'<span style="font-size:12px;color:{core.TS}">{cat}</span>'
                f'<span style="font-size:12px;color:{core.TH};font-weight:700">{n}</span>'
                f'</div>'
                f'<div style="background:{core.CARD2};border-radius:3px;height:5px">'
                f'<div style="background:{color};width:{pct_v:.0f}%;height:100%;border-radius:3px">'
                f'</div></div></div>',
                unsafe_allow_html=True,
            )
        if not any_tr:
            st.caption("Нет данных")

    st.markdown('<div class="section-header">Нагрузка</div>', unsafe_allow_html=True)
    fig_dh = core.heatmap_dept_hour(df)
    if fig_dh:
        st.plotly_chart(fig_dh, use_container_width=True, config={"displayModeBar": False})

    st.markdown('<div class="section-header">Тематика обращений</div>', unsafe_allow_html=True)
    TYPE = "tcf_4_Тип обращения"
    seg_opt = st.radio("Сегмент", ["Клинеры", "Клиенты", "Рассылки"],
                       horizontal=True, key="kpi_seg")
    seg_df = {
        "Клинеры":  (df[df["_ut"] == "Клинер"], TYPE, True, "Тематика · Клинеры"),
        "Клиенты":  (df[df["_ut"] == "Клиент"], TYPE, True, "Тематика · Клиенты"),
        "Рассылки": (df[df["is_rass"]], "campaign_name", False, "Тематика · Рассылки"),
    }[seg_opt]
    fig_th = core.topic_heatmap_fig(*seg_df) if TYPE in df.columns else None
    if fig_th:
        st.plotly_chart(fig_th, use_container_width=True, config={"displayModeBar": False})
    else:
        st.caption("Нет данных")


def render_dept(df: pd.DataFrame) -> None:
    dc = "department_name"
    depts = sorted(df[df[dc].str.strip() != ""][dc].unique().tolist())
    if not depts:
        st.info("Нет данных по отделам")
        return

    dept = st.selectbox("Отдел", depts, key="dept_sel")
    df_d = df[df[dc] == dept]
    total = len(df_d)
    human_d = df_d[
        df_d["ticket_class"].isin(core._SPEC_CLASSES + ["Инициировал специалист"])
        & df_d["owner_name"].str.strip().ne("Бот")
        & df_d["owner_name"].ne("")
    ]
    n_ag   = human_d["owner_name"].nunique()
    rated  = df_d["rate_num"].dropna()
    avg_c  = rated.mean()
    wl     = round(total / max(n_ag, 1), 1) if n_ag > 0 else 0

    cols = st.columns(6)
    cols[0].metric("Заявок", f"{total:,}")
    cols[1].metric("Специалистов", str(n_ag))
    cols[2].metric("Нагрузка / спец.", f"{wl:.1f}" if n_ag > 0 else "—")
    cols[3].metric("CSAT", f"{avg_c:.2f}" if len(rated) else "—", f"{len(rated)} оценок")
    cols[4].metric("Ср. 1-й ответ", core._fmt_sec(human_d["first_response_sec"].dropna().mean()))
    cols[5].metric("Ср. скорость",  core._fmt_sec(human_d["avg_response_sec"].dropna().mean()))

    st.markdown('<div class="section-header">Ежедневная нагрузка</div>', unsafe_allow_html=True)
    dv = df_d.dropna(subset=["date_created_dt"]).copy()
    dv["_date"] = dv["date_created_dt"].dt.date
    daily = (dv.groupby("_date").agg(
        Заявок=("id", "count"),
        Специалистов=("owner_name",
                      lambda x: x[x.str.strip().ne("Бот") & x.ne("")].nunique()),
    ).reset_index().sort_values("_date", ascending=False).head(20))
    daily.columns = ["Дата", "Заявок", "Специалистов"]
    daily["Нагрузка"] = (daily["Заявок"] / daily["Специалистов"].clip(1)).round(1)
    st.dataframe(daily, use_container_width=True, hide_index=True)

    st.markdown('<div class="section-header">Производительность специалистов</div>',
                unsafe_allow_html=True)
    if not human_d.empty:
        ag = (human_d.groupby("owner_name").agg(
            Заявок=("id", "count"),
            csat=("rate_num", "mean"),
            Оценок=("rate_num", lambda x: x.notna().sum()),
            fr=("first_response_sec", "mean"),
            sp=("avg_response_sec", "mean"),
        ).reset_index().sort_values("Заявок", ascending=False))
        ag["CSAT"]          = ag["csat"].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
        ag["Ср. 1-й ответ"] = ag["fr"].apply(core._fmt_sec)
        ag["Ср. скорость"]  = ag["sp"].apply(core._fmt_sec)
        ag = ag.rename(columns={"owner_name": "Специалист"})
        st.dataframe(
            ag[["Специалист", "Заявок", "CSAT", "Оценок", "Ср. 1-й ответ", "Ср. скорость"]],
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption("Нет данных по специалистам")

    st.markdown('<div class="section-header">Тематика обращений</div>', unsafe_allow_html=True)
    TYPE = "tcf_4_Тип обращения"
    if TYPE not in df.columns:
        st.caption("Нет данных")
        return
    seg_opt = st.radio("Сегмент", ["Клинеры", "Клиенты", "Рассылки"],
                       horizontal=True, key="dept_seg")
    seg_df = {
        "Клинеры":  (df_d[df_d["_ut"] == "Клинер"], TYPE, True, f"{dept} · Клинеры"),
        "Клиенты":  (df_d[df_d["_ut"] == "Клиент"], TYPE, True, f"{dept} · Клиенты"),
        "Рассылки": (df_d[df_d["is_rass"]], "campaign_name", False, f"{dept} · Рассылки"),
    }[seg_opt]
    fig_th = core.topic_heatmap_fig(*seg_df)
    if fig_th:
        st.plotly_chart(fig_th, use_container_width=True, config={"displayModeBar": False})
    else:
        st.caption("Нет данных")


def render_cats(df: pd.DataFrame) -> None:
    TYPE = "tcf_4_Тип обращения"
    if TYPE not in df.columns:
        st.info("Нет данных по тематикам")
        return

    base = df[df[TYPE].ne("")].copy()
    base[TYPE] = base[TYPE].str.replace(r'\s*\[\d+\]$', '', regex=True).str.strip()

    seg_map = {
        "Все обращения":   base,
        "Без рассылок":    base[~base["is_rass"]],
        "Только рассылки": base[base["is_rass"]],
    }
    seg = st.radio("Фильтр", list(seg_map.keys()), horizontal=True, key="cats_seg")
    df_s = seg_map[seg]
    if df_s.empty:
        st.info("Нет данных")
        return

    g = core.cats_agg(df_s, TYPE)
    total = len(df_s)
    bot_pct  = g["bot_n"].sum() / total * 100 if total else 0
    avg_csat = df_s["rate_num"].dropna().mean()
    avg_fr   = df_s[~df_s["is_bot"]]["first_response_sec"].dropna().mean()

    cols = st.columns(5)
    cols[0].metric("Тематик", str(len(g)))
    cols[1].metric("% Бот", f"{bot_pct:.1f}%")
    cols[2].metric("% Ручной", f"{100-bot_pct:.1f}%")
    cols[3].metric("Ср. CSAT", f"{avg_csat:.2f}" if pd.notna(avg_csat) else "—")
    cols[4].metric("Ср. 1-й ответ", core._fmt_sec(avg_fr))

    col1, col2 = st.columns(2)
    with col1:
        fig_tm = core.cats_treemap_fig(g, TYPE)
        if fig_tm:
            st.plotly_chart(fig_tm, use_container_width=True, config={"displayModeBar": False})
    with col2:
        fig_bar = core.cats_bar_fig(g, TYPE)
        if fig_bar:
            st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar": False})

    st.markdown('<div class="section-header">Детальная таблица</div>', unsafe_allow_html=True)
    tbl = g.copy()
    tbl["Ср. CSAT"]      = tbl["avg_csat"].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
    tbl["Ср. 1-й ответ"] = tbl["avg_first"].apply(core._fmt_sec)
    tbl["Ср. скорость"]  = tbl["avg_speed"].apply(core._fmt_sec)
    tbl["Пик (час)"]     = tbl["peak_h"].apply(
        lambda v: f"{int(v)}:00" if pd.notna(v) and v is not None else "—"
    )
    tbl = tbl.rename(columns={TYPE: "Тематика", "count": "Заявок",
                               "bot_pct": "% Бот", "trans_pct": "% Перевод"})
    st.dataframe(
        tbl[["Тематика", "Заявок", "% Бот", "% Перевод",
             "Ср. CSAT", "Ср. 1-й ответ", "Ср. скорость", "Пик (час)"]],
        use_container_width=True, hide_index=True,
    )


def render_trend(df: pd.DataFrame) -> None:
    rdf = core.trend_df(df)
    if rdf.empty:
        st.info("Нет данных")
        return

    d_max = rdf["date"].max().date()
    d_min = rdf["date"].min().date()

    st.markdown('<div class="section-header">Настройка периодов</div>', unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns([3, 3, 1.5, 1.5])
    with col1:
        period_a = st.date_input("Период А (сравниваемый)",
                                 value=(d_max - timedelta(days=6), d_max),
                                 min_value=d_min, max_value=d_max, key="trend_a")
    with col2:
        period_b = st.date_input("Период Б (базовый)",
                                 value=(max(d_min, d_max - timedelta(days=13)),
                                        d_max - timedelta(days=7)),
                                 min_value=d_min, max_value=d_max, key="trend_b")
    with col3:
        gran = st.radio("Детализация", ["День", "Неделя"], key="trend_gran")
    with col4:
        flt = st.radio("Фильтр", ["Все", "Без рассылок", "Только рассылки"], key="trend_flt")

    a_from = period_a[0] if isinstance(period_a, tuple) else period_a
    a_to   = period_a[-1] if isinstance(period_a, tuple) else period_a
    b_from = period_b[0] if isinstance(period_b, tuple) else period_b
    b_to   = period_b[-1] if isinstance(period_b, tuple) else period_b

    rdf_f = rdf
    if flt == "Без рассылок":   rdf_f = rdf[~rdf["is_rass"]]
    elif flt == "Только рассылки": rdf_f = rdf[rdf["is_rass"]]

    freq = "D" if gran == "День" else "W-MON"

    def period_counts(from_d: date, to_d: date) -> pd.DataFrame:
        mask = (rdf_f["date"].dt.date >= from_d) & (rdf_f["date"].dt.date <= to_d)
        sub  = rdf_f[mask]
        if sub.empty:
            return pd.DataFrame(columns=["date", "count", "bot"])
        return (sub.set_index("date").resample(freq)
                .agg(count=("topic", "count"), bot=("is_bot", "sum"))
                .reset_index())

    cnt_a = period_counts(a_from, a_to)
    cnt_b = period_counts(b_from, b_to)
    total_a = int(cnt_a["count"].sum()) if not cnt_a.empty else 0
    total_b = int(cnt_b["count"].sum()) if not cnt_b.empty else 0

    st.markdown('<div class="section-header">Итоги сравнения</div>', unsafe_allow_html=True)
    cols = st.columns(4)
    cols[0].metric("Период А · заявок", f"{total_a:,}")
    delta = total_a - total_b
    cols[1].metric("Период Б · заявок", f"{total_b:,}",
                   delta=f"{delta:+d}" if total_b > 0 else None, delta_color="inverse")
    pct = (total_a / total_b - 1) * 100 if total_b > 0 else 0
    cols[2].metric("Изменение", f"{pct:+.1f}%" if total_b > 0 else "—", delta_color="inverse")
    bot_pct_a = cnt_a["bot"].sum() / total_a * 100 if total_a > 0 else 0
    cols[3].metric("% Бот (период А)", f"{bot_pct_a:.1f}%")

    st.markdown('<div class="section-header">Динамика</div>', unsafe_allow_html=True)
    fig = go.Figure()
    if not cnt_a.empty:
        fig.add_trace(go.Scatter(
            x=cnt_a["date"], y=cnt_a["count"], mode="lines+markers", name="Период А",
            line=dict(color=core.T, width=2.5), marker=dict(size=5),
            fill="tozeroy", fillcolor="rgba(34,211,238,0.07)",
        ))
        if cnt_a["bot"].sum() > 0:
            fig.add_trace(go.Scatter(
                x=cnt_a["date"], y=cnt_a["bot"], mode="lines", name="Период А · бот",
                line=dict(color=core.G, width=1.5, dash="dot"),
            ))
    if not cnt_b.empty:
        fig.add_trace(go.Scatter(
            x=cnt_b["date"], y=cnt_b["count"], mode="lines+markers", name="Период Б",
            line=dict(color=core.B, width=2, dash="dash"), marker=dict(size=4),
        ))
    core._base(fig, h=360, legend_bottom=True)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    st.markdown('<div class="section-header">Топ тематик · Период А</div>', unsafe_allow_html=True)
    type_flt = st.radio("Тип", ["Все", "Бот", "Специалист"], horizontal=True, key="trend_type")

    a_mask = (rdf_f["date"].dt.date >= a_from) & (rdf_f["date"].dt.date <= a_to)
    b_mask = (rdf_f["date"].dt.date >= b_from) & (rdf_f["date"].dt.date <= b_to)
    a_sub, b_sub = rdf_f[a_mask], rdf_f[b_mask]
    if type_flt == "Бот":
        a_sub, b_sub = a_sub[a_sub["is_bot"]], b_sub[b_sub["is_bot"]]
    elif type_flt == "Специалист":
        a_sub, b_sub = a_sub[~a_sub["is_bot"]], b_sub[~b_sub["is_bot"]]

    a_cnt = a_sub.groupby("topic")["topic"].count().rename("А")
    b_cnt = b_sub.groupby("topic")["topic"].count().rename("Б")
    tbl   = pd.concat([a_cnt, b_cnt], axis=1).fillna(0).astype(int)
    tbl   = tbl.sort_values("А", ascending=False).head(30).reset_index()
    tbl.columns = ["Тематика", "Период А", "Период Б"]
    tbl["Δ"] = tbl["Период А"] - tbl["Период Б"]
    st.dataframe(tbl, use_container_width=True, hide_index=True)


def render_db(df: pd.DataFrame) -> None:
    COMPUTED = {
        "date_created_dt", "hour", "weekday", "_ut", "has_wikibot",
        "is_rass", "is_init", "is_bot", "ticket_class", "campaign_name",
        "rate_num", "_trans", "_human_trans", "first_response_sec", "avg_response_sec",
    }
    orig_cols = [c for c in df.columns if c not in COMPUTED]

    def _disp(col: str) -> str:
        return re.sub(r'^[tu]cf_\d+_', '', col)

    raw = df[orig_cols].copy()
    raw.columns = [_disp(c) for c in orig_cols]

    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_input("Поиск", placeholder="Введи текст...", key="db_q",
                              label_visibility="collapsed")
    with col2:
        st.markdown(
            f'<div style="color:{core.TM};font-size:12px;padding-top:8px">'
            f'{len(raw):,} строк · {len(raw.columns)} колонок</div>',
            unsafe_allow_html=True,
        )

    display = raw
    if query:
        mask    = raw.apply(lambda c: c.astype(str).str.contains(query, case=False, na=False)).any(axis=1)
        display = raw[mask]

    st.dataframe(display, use_container_width=True, height=540)

    csv_bytes = display.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        label=f"⬇ Скачать CSV ({len(display):,} строк)",
        data=csv_bytes,
        file_name="tickets_export.csv",
        mime="text/csv",
        type="primary",
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not check_password():
        st.stop()

    render_sidebar()

    if "df" not in st.session_state:
        st.markdown("""
        <div class="empty-state">
          <div class="empty-state-icon">📊</div>
          <div class="empty-state-title">Данные не загружены</div>
          <div>Выбери период в панели слева и нажми «Загрузить данные»</div>
        </div>
        """, unsafe_allow_html=True)
        return

    df = st.session_state.df

    d_min = df["date_created_dt"].min()
    d_max = df["date_created_dt"].max()
    period = (f"{d_min.strftime('%d.%m.%Y')} — {d_max.strftime('%d.%m.%Y')}"
              if pd.notna(d_min) else "—")

    st.markdown(
        f'<div class="dash-header">'
        f'<div><div class="dash-header-title">HDE Analytics · qlean2</div>'
        f'<div class="dash-header-period">{period}</div></div>'
        f'<div class="dash-header-count">{len(df):,} заявок</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    tabs = st.tabs(["Общий анализ", "По отделу", "Категории", "Динамика", "База данных"])
    with tabs[0]: render_kpi(df)
    with tabs[1]: render_dept(df)
    with tabs[2]: render_cats(df)
    with tabs[3]: render_trend(df)
    with tabs[4]: render_db(df)


main()
