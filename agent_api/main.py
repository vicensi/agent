"""
main.py — FastAPI REST API para o agente de dados.

Conceito — Por que FastAPI para BI tools?
  BI tools como Power BI, Metabase, Splunk e Grafana conseguem consumir dados via:
    * REST API (JSON) — padrão universal, suportado por todas
    * SQL over JDBC/ODBC — requer driver específico
    * GraphQL — menos comum, mas Power BI suporta via conector
    * WebSocket — para streaming, não relevante aqui

  Uma REST API com /ask (linguagem natural) + /query (estruturada) + /metrics
  (catálogo) cobre todos os casos de uso de BI. O Power BI usa o conector Web
  ou Python/R script. O Metabase usa a fonte de dados "API" ou plugin custom.
  O Grafana usa o plugin JSON/Infinity datasource.

Conceito — async/await no FastAPI:
  O FastAPI é ASGI (Asynchronous Server Gateway Interface). Handlers marcados
  com `async def` rodam no event loop sem bloquear outras requests.
  Como nosso trabalho é I/O-bound (chamadas de rede para Claude API + BigQuery),
  o async nos deixa servir várias requests concorrentes com uma única thread.

  Importante: o SDK da Anthropic tem versão async (AsyncAnthropic).
  O BigQuery client da Google não é nativamente async — envolvemos em
  `asyncio.to_thread()` para não bloquear o event loop.

Conceito — Camadas da API:
  [Client BI] → POST /ask → agent.py (loop) → tools.py (executor) → BigQuery
  [Client BI] → POST /query → query_builder.py → BigQuery (sem agente)
  [Client BI] → GET /metrics → catalog.py (sem I/O)
  [Client BI] → GET /health → check de dependências

[PRODUÇÃO]
  - Adicionar Bearer token auth (OAuth2 ou API key)
  - Rate limiting por API key (ex: slowapi)
  - CORS restrito aos domínios dos BI tools
  - Versionamento: /v1/ask, /v2/ask
  - Observabilidade: OpenTelemetry traces por request
"""

import asyncio
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# ── Path setup — importa mcp_server diretamente ───────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent / "mcp_server"))

from catalog import METRICS, get_metric
from bq_client import get_client
from query_builder import build_metric_query

from agent import run_agent
from models import (
    AskRequest,
    AskResponse,
    MetricInfo,
    MetricsResponse,
    QueryRequest,
    QueryResponse,
    ToolCallRecord,
)

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

# Carrega .env na raiz do projeto (GCP_PROJECT_ID, GCP_KEYFILE_PATH, ANTHROPIC_API_KEY)
load_dotenv(Path(__file__).parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


# =============================================================================
# LIFESPAN — inicialização das dependências
#
# Conceito — lifespan vs on_event:
#   O lifespan é o modo moderno (FastAPI 0.93+) de inicializar recursos.
#   Tudo antes do `yield` roda no startup; tudo depois, no shutdown.
#   É preferível a @app.on_event("startup") que foi deprecado.
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: valida env vars e pré-aquece o cliente BigQuery
    required_envs = ["GCP_PROJECT_ID", "GCP_KEYFILE_PATH", "ANTHROPIC_API_KEY"]
    missing = [e for e in required_envs if not os.environ.get(e)]
    if missing:
        raise RuntimeError(f"Variáveis de ambiente ausentes: {missing}")

    # Inicializa singleton do BigQuery (evita cold start na primeira request)
    try:
        get_client()
        logger.info("BigQuery client inicializado no startup")
    except Exception as exc:
        logger.error(f"Falha ao inicializar BigQuery client: {exc}")
        raise

    logger.info("Agent API iniciada — pronto para receber requests")
    yield
    # Shutdown: nada a limpar por ora
    logger.info("Agent API encerrada")


# =============================================================================
# APP FASTAPI
# =============================================================================

app = FastAPI(
    title="Agentic Data Platform — Agent API",
    description=(
        "API REST para o agente de dados de e-commerce. "
        "POST /ask aceita perguntas em linguagem natural e retorna respostas "
        "com citação de fonte obrigatória. POST /query executa métricas estruturadas. "
        "GET /metrics lista o catálogo disponível."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",       # Swagger UI em /docs
    redoc_url="/redoc",     # ReDoc em /redoc
    openapi_url="/openapi.json",
)

# CORS — permite que BI tools chamem a API de outros domínios
# [PRODUÇÃO] Restringir origins para os domínios dos BI tools específicos
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# =============================================================================
# HEALTH CHECK
# =============================================================================

@app.get("/health", tags=["infra"])
async def health():
    """
    Verifica se a API está operacional.
    Usado por load balancers e orquestradores (Kubernetes liveness probe).
    """
    try:
        # Testa conexão com BigQuery — query mínima, sem custo
        client = get_client()
        await asyncio.to_thread(
            client.run_query,
            "SELECT 1 AS ok",
            source="health_check",
        )
        bq_status = "ok"
    except Exception as exc:
        bq_status = f"error: {exc}"

    anthropic_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))

    return {
        "status": "ok" if bq_status == "ok" and anthropic_key_set else "degraded",
        "bigquery": bq_status,
        "anthropic_key_set": anthropic_key_set,
        "version": "0.1.0",
    }


# =============================================================================
# GET /metrics — catálogo de métricas
# =============================================================================

@app.get(
    "/metrics",
    response_model=MetricsResponse,
    tags=["catalog"],
    summary="Lista métricas disponíveis",
    description=(
        "Retorna o catálogo completo de métricas de negócio disponíveis, "
        "com descrições, unidades e dimensões suportadas. "
        "Use este endpoint para construir UIs de seleção de métricas em BI tools."
    ),
)
async def list_metrics():
    metrics_list = [
        MetricInfo(
            name=m.name,
            label=m.label,
            description=m.description,
            unit=m.unit,
            available_dimensions=m.available_dimensions,
            caveats=m.caveats,
        )
        for m in METRICS.values()
    ]
    return MetricsResponse(metrics=metrics_list, total=len(metrics_list))


# =============================================================================
# POST /query — query estruturada sem agente
#
# Conceito — Por que ter /query além de /ask?
#   /ask é para análise ad-hoc: linguagem natural, flexível, usa o agente LLM.
#   /query é para integração programática: BI tools sabem exatamente o que querem
#   e não precisam do overhead do LLM. É mais rápido, mais barato, mais previsível.
#   Power BI Dataflow, pipelines de dados, dashboards fixos usam /query.
# =============================================================================

@app.post(
    "/query",
    response_model=QueryResponse,
    tags=["data"],
    summary="Consulta métrica estruturada",
    description=(
        "Executa uma consulta de métrica diretamente no BigQuery sem passar pelo agente. "
        "Mais rápido e barato que /ask. Ideal para integrações com BI tools "
        "que já sabem qual métrica querem (Power BI, Metabase, Grafana)."
    ),
)
async def query_metric(req: QueryRequest):
    metric_def = get_metric(req.metric)
    if metric_def is None:
        raise HTTPException(
            status_code=404,
            detail=f"Métrica '{req.metric}' não encontrada. Use GET /metrics.",
        )

    project_id = os.environ["GCP_PROJECT_ID"]
    try:
        sql = build_metric_query(
            metric_def=metric_def,
            project_id=project_id,
            dimensions=req.dimensions,
            period=req.period,
            filters=req.filters,
            limit=req.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    client = get_client()
    try:
        result = await asyncio.to_thread(
            client.run_query,
            sql,
            source="api.query",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return QueryResponse(
        metric=req.metric,
        label=metric_def.label,
        rows=result["rows"],
        row_count=result["row_count"],
        columns=result["columns"],
        sql_executed=result["sql_executed"],
        elapsed_ms=result["elapsed_ms"],
        truncated=result["truncated"],
        period=req.period,
        dimensions=req.dimensions,
    )


# =============================================================================
# POST /ask — pergunta em linguagem natural com agente
#
# Conceito — Fluxo completo:
#   1. Recebe pergunta → gera session_id se ausente
#   2. Chama run_agent() em thread separada (evita bloquear event loop)
#   3. Empacota resultado em AskResponse com trilha de auditoria
#   4. Retorna JSON com answer + tool_calls (citações de fonte)
# =============================================================================

@app.post(
    "/ask",
    response_model=AskResponse,
    tags=["agent"],
    summary="Pergunta em linguagem natural",
    description=(
        "Envia uma pergunta em linguagem natural. O agente decide quais ferramentas "
        "chamar, executa queries no BigQuery e retorna resposta formatada "
        "com citação obrigatória de fonte (ferramenta + SQL executado)."
    ),
)
async def ask(req: AskRequest, request: Request):
    session_id = req.session_id or str(uuid.uuid4())

    logger.info(
        "ask_request",
        extra={
            "question": req.question[:100],
            "session_id": session_id,
            "client_ip": request.client.host if request.client else "unknown",
        },
    )

    try:
        # asyncio.to_thread: executa função síncrona em thread pool
        # Necessário porque o loop agentico usa SDKs síncronos (anthropic, google-cloud-bigquery)
        result = await asyncio.to_thread(
            run_agent,
            req.question,
            session_id,
        )
    except Exception as exc:
        logger.error(
            "ask_error",
            extra={"error": str(exc), "session_id": session_id},
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno do agente: {exc}",
        )

    # Converte tool_calls_log (dict) para ToolCallRecord (Pydantic)
    tool_calls = [
        ToolCallRecord(
            tool_name=tc["tool_name"],
            parameters=tc["parameters"],
            sql_executed=tc.get("sql_executed"),
            row_count=tc.get("row_count"),
            elapsed_ms=tc.get("elapsed_ms"),
        )
        for tc in result["tool_calls_log"]
    ]

    return AskResponse(
        answer=result["answer"],
        tool_calls=tool_calls,
        question=req.question,
        session_id=session_id,
        total_elapsed_ms=result["total_elapsed_ms"],
        refused=result["refused"],
    )


# =============================================================================
# POST /dashboard — stub para Fase 5
#
# Conceito — Por que stub agora?
#   Adicionar o endpoint na fase 4 (mesmo vazio) permite que o contrato da API
#   seja documentado no OpenAPI desde o início. Integrações de BI tools podem
#   já apontar para /dashboard e receber 501 explicativo em vez de 404 genérico.
# =============================================================================

@app.post(
    "/dashboard",
    tags=["agent"],
    summary="[WIP] Gera especificação de dashboard",
    description=(
        "WORK IN PROGRESS — será implementado na Fase 5. "
        "Receberá uma descrição em linguagem natural e retornará uma "
        "especificação JSON de dashboard (cards, gráficos, filtros) "
        "compatível com Metabase, Grafana JSON Model e Looker Studio."
    ),
)
async def create_dashboard(req: AskRequest):
    raise HTTPException(
        status_code=501,
        detail=(
            "Endpoint /dashboard ainda não implementado (Fase 5). "
            "Use /ask para análises em linguagem natural."
        ),
    )


# =============================================================================
# HANDLER DE ERROS GLOBAL
# =============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        "unhandled_exception",
        extra={"path": request.url.path, "error": str(exc)},
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Erro interno. Tente novamente."},
    )


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    # Porta 8001 para não conflitar com dbt docs serve (8080) ou MCP servers
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,
        reload=True,  # hot reload para desenvolvimento
        log_level="info",
    )
