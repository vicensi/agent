"""
app.py — Streamlit chat interface para o Data Agent.

Arquitetura:
  Streamlit (UI) → POST /ask → FastAPI (agent_api) → Claude API + BigQuery

Como funciona:
  1. Usuário digita pergunta no chat input
  2. Streamlit envia POST /ask para FastAPI (localhost:8001)
  3. FastAPI executa o loop agentico e retorna AskResponse com:
     - answer: resposta em markdown
     - charts: lista de ChartSpec gerados pelo agente via plot_chart
     - tool_calls: trilha de auditoria (SQL, parâmetros)
  4. Streamlit renderiza:
     - Resposta como markdown
     - Gráficos Plotly inline (um por ChartSpec)
     - Fontes/SQL em seção expansível

Como rodar:
  # Terminal 1 — FastAPI
  source .venv/bin/activate
  cd agent_api && python main.py

  # Terminal 2 — Streamlit
  source .venv/bin/activate
  streamlit run streamlit_app/app.py
"""

import json

import plotly.graph_objects as go
import requests
import streamlit as st

# =============================================================================
# CONFIGURAÇÃO DA PÁGINA
# =============================================================================

st.set_page_config(
    page_title="Data Agent — Agentic Data Platform",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =============================================================================
# CONSTANTES
# =============================================================================

API_BASE_URL = "http://localhost:8001"
REQUEST_TIMEOUT = 120  # segundos — queries BigQuery podem ser lentas

# Perguntas de exemplo para onboarding
EXAMPLE_QUESTIONS = [
    "Qual a receita entregue por canal nos últimos 90 dias?",
    "Como evoluiu a receita líquida mês a mês em 2024?",
    "Qual a taxa de devolução por canal?",
    "Quais os 5 estados com maior receita entregue?",
    "Como se distribuem os clientes por segmento RFM?",
    "Qual o ticket médio por categoria de produto?",
]

# =============================================================================
# SIDEBAR
# =============================================================================

with st.sidebar:
    st.title("📊 Data Agent")
    st.caption("Agentic Data Platform — E-Commerce Analytics")

    st.divider()

    st.subheader("Perguntas de exemplo")
    for q in EXAMPLE_QUESTIONS:
        if st.button(q, use_container_width=True, key=f"ex_{q[:20]}"):
            st.session_state["pending_question"] = q
            st.rerun()

    st.divider()

    # Health check
    st.subheader("Status da API")
    if st.button("Verificar conexão", use_container_width=True):
        try:
            r = requests.get(f"{API_BASE_URL}/health", timeout=5)
            h = r.json()
            if h.get("status") == "ok":
                st.success("✅ API conectada")
                st.caption(f"BigQuery: {h.get('bigquery', '?')}")
            else:
                st.warning(f"⚠️ Degradada — BQ: {h.get('bigquery', '?')}")
        except Exception as e:
            st.error(f"❌ API offline: {e}")

    st.divider()
    st.caption("Dados: e-commerce sintético (~192k pedidos)")
    st.caption(f"API: `{API_BASE_URL}`")


# =============================================================================
# FUNÇÕES AUXILIARES
# =============================================================================

def render_chart(chart: dict) -> go.Figure | None:
    """
    Converte um ChartSpec (dict) em figura Plotly.
    Suporta: bar, bar_horizontal, line, pie.
    Retorna None se a spec for inválida.
    """
    chart_type   = chart.get("chart_type", "bar")
    title        = chart.get("title", "")
    labels       = chart.get("labels", [])
    values       = chart.get("values", [])
    series_names = chart.get("series_names")
    x_label      = chart.get("x_label", "")
    y_label      = chart.get("y_label", "")

    if not labels or not values:
        return None

    fig = go.Figure()

    # Detecta se values é lista de listas (múltiplas séries)
    multi_series = values and isinstance(values[0], list)

    if chart_type == "pie":
        vals = values[0] if multi_series else values
        fig.add_trace(go.Pie(
            labels=labels,
            values=vals,
            hole=0.3,
            textinfo="label+percent",
        ))

    elif chart_type == "bar_horizontal":
        if multi_series:
            for i, serie in enumerate(values):
                name = (series_names[i] if series_names and i < len(series_names)
                        else f"Série {i + 1}")
                fig.add_trace(go.Bar(
                    name=name, y=labels, x=serie, orientation="h",
                ))
            fig.update_layout(barmode="group")
        else:
            fig.add_trace(go.Bar(y=labels, x=values, orientation="h"))
        if x_label:
            fig.update_layout(xaxis_title=x_label)
        if y_label:
            fig.update_layout(yaxis_title=y_label)

    elif chart_type == "line":
        if multi_series:
            for i, serie in enumerate(values):
                name = (series_names[i] if series_names and i < len(series_names)
                        else f"Série {i + 1}")
                fig.add_trace(go.Scatter(
                    name=name, x=labels, y=serie, mode="lines+markers",
                ))
        else:
            fig.add_trace(go.Scatter(x=labels, y=values, mode="lines+markers"))
        if x_label:
            fig.update_layout(xaxis_title=x_label)
        if y_label:
            fig.update_layout(yaxis_title=y_label)

    else:  # bar (padrão)
        if multi_series:
            for i, serie in enumerate(values):
                name = (series_names[i] if series_names and i < len(series_names)
                        else f"Série {i + 1}")
                fig.add_trace(go.Bar(name=name, x=labels, y=serie))
            fig.update_layout(barmode="group")
        else:
            fig.add_trace(go.Bar(x=labels, y=values))
        if x_label:
            fig.update_layout(xaxis_title=x_label)
        if y_label:
            fig.update_layout(yaxis_title=y_label)

    fig.update_layout(
        title=title,
        template="plotly_white",
        margin={"t": 50, "b": 40, "l": 40, "r": 20},
        height=400,
        legend={"orientation": "h", "y": -0.2} if multi_series else {},
    )
    return fig


def call_ask_api(question: str) -> dict:
    """
    Chama POST /ask e retorna o JSON da AskResponse.
    Lança exceção em caso de erro de rede ou HTTP.
    """
    response = requests.post(
        f"{API_BASE_URL}/ask",
        json={"question": question},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def render_message(msg: dict):
    """
    Renderiza uma mensagem do histórico de chat.
    Cada mensagem tem: role, content (str), charts (list), tool_calls (list).
    """
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

        # Gráficos inline
        for chart_spec in msg.get("charts", []):
            fig = render_chart(chart_spec)
            if fig:
                st.plotly_chart(fig, use_container_width=True)

        # Fontes (expansível)
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            with st.expander(f"📋 Fontes ({len(tool_calls)} tool call{'s' if len(tool_calls) > 1 else ''})", expanded=False):
                for tc in tool_calls:
                    st.markdown(f"**Tool:** `{tc['tool_name']}`")
                    if tc.get("parameters"):
                        st.json(tc["parameters"])
                    if tc.get("sql_executed"):
                        st.code(tc["sql_executed"], language="sql")
                    if tc.get("row_count") is not None:
                        st.caption(
                            f"Linhas retornadas: {tc['row_count']}"
                            + (f" | {tc['elapsed_ms']}ms" if tc.get("elapsed_ms") else "")
                        )
                    st.divider()


# =============================================================================
# ESTADO DA SESSÃO
# =============================================================================

if "messages" not in st.session_state:
    st.session_state["messages"] = []

# =============================================================================
# CABEÇALHO
# =============================================================================

st.title("📊 Data Agent")
st.caption(
    "Faça perguntas em linguagem natural sobre os dados de e-commerce. "
    "O agente consulta o BigQuery e cita a fonte de cada número."
)

if not st.session_state["messages"]:
    st.info(
        "👋 **Bem-vindo!** Escolha uma pergunta de exemplo na barra lateral "
        "ou digite sua própria pergunta abaixo.",
        icon="💡",
    )

# =============================================================================
# HISTÓRICO DE CHAT
# =============================================================================

for msg in st.session_state["messages"]:
    render_message(msg)

# =============================================================================
# INPUT DO USUÁRIO
# =============================================================================

# Pergunta vinda do sidebar (botões de exemplo)
pending = st.session_state.pop("pending_question", None)

question = st.chat_input("Faça uma pergunta sobre os dados...") or pending

if question:
    # Adiciona mensagem do usuário ao histórico
    st.session_state["messages"].append({
        "role": "user",
        "content": question,
        "charts": [],
        "tool_calls": [],
    })

    # Renderiza a mensagem do usuário imediatamente
    with st.chat_message("user"):
        st.markdown(question)

    # Chama a API e renderiza resposta
    with st.chat_message("assistant"):
        with st.spinner("Consultando o warehouse..."):
            try:
                data = call_ask_api(question)
            except requests.exceptions.ConnectionError:
                st.error(
                    "❌ Não foi possível conectar à API. "
                    "Verifique se o servidor FastAPI está rodando em `localhost:8001`.\n\n"
                    "```bash\ncd agent_api && python main.py\n```"
                )
                st.stop()
            except requests.exceptions.Timeout:
                st.error("⏱️ Timeout — a query demorou mais de 120s. Tente uma pergunta mais simples.")
                st.stop()
            except requests.exceptions.HTTPError as e:
                st.error(f"❌ Erro da API ({e.response.status_code}): {e.response.text[:300]}")
                st.stop()

        answer     = data.get("answer", "")
        charts     = data.get("charts", [])
        tool_calls = data.get("tool_calls", [])
        elapsed    = data.get("total_elapsed_ms", 0)

        # Resposta em markdown
        st.markdown(answer)

        # Gráficos inline
        for chart_spec in charts:
            fig = render_chart(chart_spec)
            if fig:
                st.plotly_chart(fig, use_container_width=True)

        # Tempo de resposta
        st.caption(f"⏱️ {elapsed / 1000:.1f}s")

        # Fontes
        if tool_calls:
            with st.expander(
                f"📋 Fontes ({len(tool_calls)} tool call{'s' if len(tool_calls) > 1 else ''})",
                expanded=False,
            ):
                for tc in tool_calls:
                    st.markdown(f"**Tool:** `{tc['tool_name']}`")
                    if tc.get("parameters"):
                        st.json(tc["parameters"])
                    if tc.get("sql_executed"):
                        st.code(tc["sql_executed"], language="sql")
                    if tc.get("row_count") is not None:
                        st.caption(
                            f"Linhas retornadas: {tc['row_count']}"
                            + (f" | {tc['elapsed_ms']}ms" if tc.get("elapsed_ms") else "")
                        )
                    st.divider()

    # Persiste no histórico
    st.session_state["messages"].append({
        "role": "assistant",
        "content": answer,
        "charts": charts,
        "tool_calls": tool_calls,
    })
