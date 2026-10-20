import streamlit as st
import pandas as pd
from supabase import create_client, Client
import time

# --- НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="Whale Tracker", page_icon="🐋", layout="wide")

# Скрываем стандартный хедер Streamlit
hide_streamlit_style = """
            <style>
            #MainMenu {visibility: hidden;}
            footer {visibility: hidden;}
            header {visibility: hidden;}
            .block-container {padding-top: 2rem;}
            </style>
            """
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# --- ПОДКЛЮЧЕНИЕ К БАЗЕ ---
SUPABASE_URL = "https://bbzynftosxppxxsnlozo.supabase.co"
SUPABASE_KEY = "sb_publishable_4dpzEKOxERFrNDU-hgqeiQ_GvAKtTcd"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- ЗАГОЛОВОК И ФИЛЬТРЫ ---
col1, col2 = st.columns([3, 1])
with col1:
    st.title("🐋 ПЕРЕВОДЫ")
with col2:
    # Можно позже заменить на реальный подсчет
    st.metric(label="Всего записей", value="...")

f_col1, f_col2, f_col3 = st.columns([1, 1, 1])
with f_col1:
    direction_filter = st.selectbox("Направление", ["ВСЕ", "ПРИТОК", "ОТТОК"])
with f_col2:
    min_usd = st.number_input("Мин. сумма ($)", value=10_000_000, step=1_000_000)
with f_col3:
    if st.button("Очистить"):
        st.rerun()


# --- ЗАГРУЗКА ДАННЫХ ---
@st.cache_data(ttl=30)
def load_whales():
    # Используем 'timestamp' вместо 'created_at'
    response = supabase.table("whale_transactions").select("*").order("timestamp", desc=True).limit(100).execute()
    return pd.DataFrame(response.data)


df = load_whales()

if not df.empty:
    df['timestamp'] = pd.to_datetime(df['timestamp'])

    # Фильтрация
    if direction_filter == "ПРИТОК":
        df = df[df['direction'] == 'IN']
    elif direction_filter == "ОТТОК":
        df = df[df['direction'] == 'OUT']

    df = df[df['amount_usd'] >= min_usd]


    # --- ФОРМАТИРОВАНИЕ ТАБЛИЦЫ ---
    def format_row(row):
        direction_badge = f"<span style='color: {'#00ff00' if row['direction'] == 'IN' else '#ff4d4d'}; font-weight: bold;'>{'▼ ПРИТОК' if row['direction'] == 'IN' else '▲ ОТТОК'}</span>"
        short_addr = f"{row['wallet_address'][:6]}...{row['wallet_address'][-4:]}"
        time_str = row['timestamp'].strftime('%H:%M')

        return {
            "Время": time_str,
            "Направление": direction_badge,
            "Кошелек": short_addr,
            "BTC": f"{row['amount_crypto']:,.4f}",
            "USD": f"${row['amount_usd']:,.0f}",
            "Tx Hash": f"[Link](https://mempool.space/tx/{row['tx_hash']})"
        }


    styled_data = df.apply(format_row, axis=1).tolist()
    display_df = pd.DataFrame(styled_data)

    # --- ОТРИСОВКА ---
    st.markdown("""
    <style>
    .dataframe th {background-color: #1a1a1a; color: #888; font-size: 12px;}
    .dataframe td {background-color: #0e0e0e; color: #fff; border-bottom: 1px solid #333;}
    </style>
    """, unsafe_allow_html=True)

    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Время": st.column_config.TextColumn("ВРЕМЯ"),
            "Направление": st.column_config.TextColumn("ТИП"),
            "Кошелек": st.column_config.TextColumn("АДРЕС"),
            "BTC": st.column_config.TextColumn("BTC"),
            "USD": st.column_config.TextColumn("USD VALUE"),
            "Tx Hash": st.column_config.LinkColumn("LINK")
        }
    )
else:
    st.warning("Нет транзакций, соответствующих фильтру.")

# Автообновление
time.sleep(60)
st.rerun()