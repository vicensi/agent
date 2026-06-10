"""
Gerador de dataset e-commerce sintético com armadilhas para análise de dados.

Armadilhas embutidas:
  1. NULOS — colunas com taxas distintas de missingness (MAR e MNAR)
  2. DUPLICATAS — registros completamente duplicados + duplicatas com timestamps levemente diferentes
  3. TIMEZONE — pedidos em UTC, fuso do cliente e fuso do warehouse misturados sem rótulo
  4. DEVOLUÇÕES CRUZANDO MÊS — data_devolucao cai no mês seguinte ao da compra
  5. VALORES INCONSISTENTES — desconto > valor total, quantidade negativa em devolução
  6. CATEGORIAS SUJAS — variações de case/espaço no mesmo valor ("SP", " sp", "São Paulo")
  7. IDS DUPLICADOS COM DADOS DIVERGENTES — mesmo order_id com status diferente
"""

import numpy as np
import pandas as pd
from datetime import timedelta, timezone
import pytz
import random
import hashlib

# ─────────────────────────────────────────────
# Configuração geral
# ─────────────────────────────────────────────

RNG_SEED = 42
N_ORDERS = 190_000          # pedidos base; com duplicatas e devoluções chegamos ~200k linhas
DEVOLUCAO_RATE = 0.08       # 8% dos pedidos geram devolução
DUPLICATE_RATE = 0.015      # 1,5% de duplicatas
CROSS_MONTH_RATE = 0.70     # 70% das devoluções cruzam o mês

PRODUTOS = [
    ("Notebook Gamer",       "Eletrônicos",    2_500, 8_000),
    ("Smartphone Top",       "Eletrônicos",    1_200, 5_000),
    ("Fone Bluetooth",       "Eletrônicos",      150,   800),
    ("Tênis Running",        "Moda",             200,   600),
    ("Camiseta Básica",      "Moda",              50,   150),
    ("Mochila Executiva",    "Acessórios",       180,   450),
    ("Livro Python",         "Livros",            60,   120),
    ("Cafeteira Espresso",   "Casa",             350, 1_200),
    ("Fritadeira Air Fryer", "Casa",             250,   700),
    ("Suplemento Whey",      "Saúde",            100,   350),
]

ESTADOS_SUJOS = [
    "SP", " sp", "São Paulo", "S.P.", "sp",
    "RJ", "rj", "Rio de Janeiro", " RJ",
    "MG", "mg", "Minas Gerais",
    "RS", "rs", "Rio Grande do Sul",
    "PR", "pr", "Paraná",
    "SC", "sc", "Santa Catarina",
    "BA", "ba", "Bahia",
    "GO", "go", "Goiás",
    "PE", "pe", "Pernambuco",
    "CE", "ce", "Ceará",
]

FUSOS = [
    "America/Sao_Paulo",    # UTC-3
    "America/Manaus",       # UTC-4
    "America/Belem",        # UTC-3
    "UTC",                  # warehouse / API logs
    None,                   # armadilha: sem fuso (naive datetime)
]

STATUS_VALIDOS = ["aprovado", "enviado", "entregue", "cancelado", "aguardando_pagamento"]
CANAIS = ["app_android", "app_ios", "site_desktop", "site_mobile", "marketplace"]
METODOS_PAGAMENTO = ["cartao_credito", "pix", "boleto", "cartao_debito", "vale_presente"]


# ─────────────────────────────────────────────
# Funções auxiliares
# ─────────────────────────────────────────────

def _order_id(idx: int) -> str:
    raw = f"ORD-{idx:08d}"
    return raw


def _timestamp_com_fuso(base_dt: pd.Timestamp, fuso_str: str | None) -> pd.Timestamp:
    """Retorna timestamp com ou sem fuso (armadilha timezone)."""
    if fuso_str is None:
        # naive — sem informação de fuso
        return base_dt.replace(tzinfo=None)
    tz = pytz.timezone(fuso_str)
    return base_dt.tz_localize("UTC").tz_convert(tz)


def _inject_nulls(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """
    Injeta nulos com padrões diferentes por coluna (MAR / MNAR).
    - review_score: MNAR — maior chance de nulo quando status == 'cancelado'
    - cep_cliente: MAR — nulo aleatório, ~12%
    - nome_produto: MAR raríssimo — ~0.3%
    - metodo_pagamento: MAR ~5%
    - desconto_pct: MAR ~8%
    """
    n = len(df)

    # MNAR: cancelados raramente deixam avaliação
    cancelado_mask = df["status"] == "cancelado"
    prob_nulo_review = np.where(cancelado_mask, 0.85, 0.20)
    nulo_review = rng.random(n) < prob_nulo_review
    df.loc[nulo_review, "review_score"] = np.nan

    # MAR simples
    df.loc[rng.random(n) < 0.12, "cep_cliente"] = np.nan
    df.loc[rng.random(n) < 0.003, "nome_produto"] = np.nan
    df.loc[rng.random(n) < 0.05, "metodo_pagamento"] = np.nan
    df.loc[rng.random(n) < 0.08, "desconto_pct"] = np.nan

    return df


def _inject_duplicates(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """
    Dois tipos de duplicata:
    A) Duplicata exata (copiar linha inteira).
    B) Quasi-duplicata: mesmo order_id mas timestamp diferente em poucos segundos
       e/ou status divergente — simula double-insert de webhook.
    """
    n_dup = int(len(df) * DUPLICATE_RATE)
    idx_exatos = rng.choice(df.index, size=n_dup // 2, replace=False)
    exact_dups = df.loc[idx_exatos].copy()

    idx_quasi = rng.choice(df.index, size=n_dup // 2, replace=False)
    quasi_dups = df.loc[idx_quasi].copy()
    # deslocar timestamp em 1-5 segundos
    delta_s = rng.integers(1, 6, size=len(quasi_dups))
    quasi_dups["data_pedido"] = [
        str(pd.Timestamp(dt) + timedelta(seconds=int(d)))
        for dt, d in zip(quasi_dups["data_pedido"].tolist(), delta_s)
    ]
    # alguns com status diferente
    flip_mask = rng.random(len(quasi_dups)) < 0.4
    quasi_dups.loc[flip_mask, "status"] = rng.choice(
        ["enviado", "aprovado"], size=flip_mask.sum()
    )

    df = pd.concat([df, exact_dups, quasi_dups], ignore_index=True)
    return df


def _inject_cross_month_returns(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """
    Para pedidos com devolução:
    - CROSS_MONTH_RATE deles têm data_devolucao no mês seguinte.
    - Restante dentro do mesmo mês.
    Também injeta algumas devoluções com valor_devolucao > valor_total (armadilha numérica).
    """
    dev_mask = df["tem_devolucao"] == True
    dev_idx = df.index[dev_mask]

    cross = rng.random(len(dev_idx)) < CROSS_MONTH_RATE
    dias_cross = rng.integers(28, 45, size=cross.sum())      # cruza o mês
    dias_mesmo = rng.integers(1, 27, size=(~cross).sum())    # dentro do mês

    # Trabalhar com strings para evitar conflito de dtype timezone-aware vs naive
    base_dts = df.loc[dev_idx, "data_pedido"].tolist()

    result_dts = []
    cross_iter = iter(dias_cross)
    mesmo_iter = iter(dias_mesmo)
    for dt, is_cross in zip(base_dts, cross):
        try:
            ts = pd.Timestamp(dt)
        except Exception:
            ts = pd.Timestamp("2023-06-15")
        delta = int(next(cross_iter) if is_cross else next(mesmo_iter))
        result_dts.append(str(ts + timedelta(days=delta)))

    df.loc[dev_idx, "data_devolucao"] = result_dts

    # valor_devolucao > valor_total em ~2% das devoluções (armadilha)
    over_idx = dev_idx[rng.random(len(dev_idx)) < 0.02]
    df.loc[over_idx, "valor_devolucao"] = (
        df.loc[over_idx, "valor_total"] * rng.uniform(1.01, 1.30, size=len(over_idx))
    )

    return df


# ─────────────────────────────────────────────
# Função principal
# ─────────────────────────────────────────────

def gerar_dataset_ecommerce(
    n_pedidos: int = N_ORDERS,
    seed: int = RNG_SEED,
    data_inicio: str = "2023-01-01",
    data_fim: str = "2024-12-31",
) -> pd.DataFrame:
    """
    Gera um DataFrame de e-commerce sintético com armadilhas embutidas.

    Parâmetros
    ----------
    n_pedidos : int
        Quantidade de pedidos base. Linhas finais ~= n_pedidos * 1.02 (duplicatas + devoluções).
    seed : int
        Semente para reprodutibilidade.
    data_inicio : str
        Data mais antiga dos pedidos (formato YYYY-MM-DD).
    data_fim : str
        Data mais recente dos pedidos (formato YYYY-MM-DD).

    Retorna
    -------
    pd.DataFrame
        Dataset com ~200k linhas e as seguintes armadilhas:
        1. Nulos (MAR e MNAR)
        2. Duplicatas exatas e quasi-duplicatas
        3. Timestamps com fusos misturados (e alguns naive)
        4. Devoluções cruzando o mês
        5. valor_devolucao > valor_total (~2% das devoluções)
        6. Estados com variações de grafia
        7. Categorias com variações de case
    """
    rng = np.random.default_rng(seed)
    random.seed(seed)

    # ── 1. Gerar pedidos base ──────────────────────────────────────────────

    inicio = pd.Timestamp(data_inicio)
    fim = pd.Timestamp(data_fim)
    periodo_segundos = int((fim - inicio).total_seconds())

    # timestamps base em UTC
    segundos_offset = rng.integers(0, periodo_segundos, size=n_pedidos)
    datas_pedido = [inicio + timedelta(seconds=int(s)) for s in segundos_offset]

    # aplicar fusos misturados (armadilha timezone)
    fusos_escolhidos = rng.choice(len(FUSOS), size=n_pedidos)
    datas_com_fuso = []
    for dt, fi in zip(datas_pedido, fusos_escolhidos):
        ts = pd.Timestamp(dt)
        fuso = FUSOS[fi]
        datas_com_fuso.append(_timestamp_com_fuso(ts, fuso))

    # produtos
    prod_idx = rng.integers(0, len(PRODUTOS), size=n_pedidos)
    nomes_prod   = [PRODUTOS[i][0] for i in prod_idx]
    categorias   = [PRODUTOS[i][1] for i in prod_idx]
    preco_min    = np.array([PRODUTOS[i][2] for i in prod_idx], dtype=float)
    preco_max    = np.array([PRODUTOS[i][3] for i in prod_idx], dtype=float)

    preco_unit = preco_min + rng.random(n_pedidos) * (preco_max - preco_min)
    preco_unit = np.round(preco_unit, 2)
    quantidade = rng.integers(1, 6, size=n_pedidos)

    desconto_pct = np.round(rng.choice(
        [0, 5, 10, 15, 20, 25, 30], size=n_pedidos,
        p=[0.45, 0.15, 0.15, 0.10, 0.08, 0.04, 0.03]
    ), 1)
    valor_bruto  = np.round(preco_unit * quantidade, 2)
    valor_total  = np.round(valor_bruto * (1 - desconto_pct / 100), 2)

    # clientes
    n_clientes = n_pedidos // 3
    cliente_ids = rng.integers(1, n_clientes + 1, size=n_pedidos)
    estados_escolhidos = [
        ESTADOS_SUJOS[rng.integers(0, len(ESTADOS_SUJOS))] for _ in range(n_pedidos)
    ]

    # devoluções
    tem_devolucao = rng.random(n_pedidos) < DEVOLUCAO_RATE
    valor_devolucao = np.where(
        tem_devolucao,
        np.round(valor_total * rng.uniform(0.5, 1.0, size=n_pedidos), 2),
        np.nan,
    )
    data_devolucao = np.where(tem_devolucao, pd.NaT, pd.NaT)

    df = pd.DataFrame({
        "order_id":          [_order_id(i) for i in range(1, n_pedidos + 1)],
        "cliente_id":        cliente_ids,
        "data_pedido":       datas_com_fuso,
        "nome_produto":      nomes_prod,
        "categoria":         categorias,
        "quantidade":        quantidade,
        "preco_unitario":    preco_unit,
        "desconto_pct":      desconto_pct,
        "valor_bruto":       valor_bruto,
        "valor_total":       valor_total,
        "status":            rng.choice(STATUS_VALIDOS, size=n_pedidos,
                                         p=[0.15, 0.20, 0.50, 0.10, 0.05]),
        "canal":             rng.choice(CANAIS, size=n_pedidos),
        "metodo_pagamento":  rng.choice(METODOS_PAGAMENTO, size=n_pedidos),
        "estado_cliente":    estados_escolhidos,
        "cep_cliente":       [f"{rng.integers(10000, 99999):05d}-{rng.integers(100, 999):03d}"
                              for _ in range(n_pedidos)],
        "review_score":      np.where(rng.random(n_pedidos) < 0.35, np.nan,
                                       rng.integers(1, 6, size=n_pedidos).astype(float)),
        "tem_devolucao":     tem_devolucao,
        "data_devolucao":    pd.array(data_devolucao, dtype="object"),
        "valor_devolucao":   valor_devolucao,
    })

    # ── 2. Injetar devoluções cruzando o mês ─────────────────────────────
    df = _inject_cross_month_returns(df, rng)

    # ── 3. Injetar nulos ──────────────────────────────────────────────────
    df = _inject_nulls(df, rng)

    # ── 4. Injetar duplicatas ─────────────────────────────────────────────
    df = _inject_duplicates(df, rng)

    # ── 5. Armadilha extra: desconto_pct > 100 em poucos registros ────────
    bad_desc_idx = rng.choice(df.index[df["desconto_pct"].notna()], size=50, replace=False)
    df.loc[bad_desc_idx, "desconto_pct"] = rng.integers(101, 150, size=50).astype(float)

    # ── 6. Armadilha: quantidade negativa em devoluções ───────────────────
    dev_idx = df.index[df["tem_devolucao"] == True]
    neg_qty_idx = rng.choice(dev_idx, size=min(200, len(dev_idx)), replace=False)
    df.loc[neg_qty_idx, "quantidade"] = -df.loc[neg_qty_idx, "quantidade"]

    # ── 7. Resetar índice e ordenar ───────────────────────────────────────
    df = df.sample(frac=1, random_state=seed).reset_index(drop=True)

    return df


# ─────────────────────────────────────────────
# Diagnóstico rápido (sanidade)
# ─────────────────────────────────────────────

def diagnostico(df: pd.DataFrame) -> None:
    """Imprime um resumo das armadilhas presentes no dataset."""
    sep = "─" * 55
    print(sep)
    print(f"{'DIAGNÓSTICO DO DATASET':^55}")
    print(sep)
    print(f"  Linhas totais          : {len(df):>10,}")
    print(f"  Colunas                : {df.shape[1]:>10}")
    print()

    # Duplicatas
    n_dup_exatas = df.duplicated().sum()
    n_dup_order  = df.duplicated(subset=["order_id"]).sum()
    print(f"  Duplicatas exatas      : {n_dup_exatas:>10,}")
    print(f"  order_id duplicado     : {n_dup_order:>10,}")

    # Nulos
    print()
    print("  Taxa de nulos por coluna:")
    nulos = df.isnull().mean().mul(100).round(2)
    for col, pct in nulos[nulos > 0].items():
        print(f"    {col:<25} {pct:>6.2f}%")

    # Timezone
    print()
    tz_counts = df["data_pedido"].apply(
        lambda x: str(getattr(x, "tzinfo", None))
    ).value_counts()
    print("  Distribuição de fusos (data_pedido):")
    for tz, cnt in tz_counts.items():
        print(f"    {tz:<40} {cnt:>7,}")

    # Devoluções cruzando o mês
    dev = df[df["tem_devolucao"] == True].copy()
    if len(dev) > 0:
        dev["data_pedido_ts"] = pd.to_datetime(dev["data_pedido"].astype(str), utc=True, errors="coerce")
        dev["data_dev_ts"]    = pd.to_datetime(dev["data_devolucao"].astype(str), utc=True, errors="coerce")
        cross = (
            dev["data_dev_ts"].dt.month != dev["data_pedido_ts"].dt.month
        ).sum()
        print()
        print(f"  Devoluções totais      : {len(dev):>10,}")
        print(f"  Devoluções cross-month : {cross:>10,}  ({100*cross/len(dev):.1f}%)")

    # Valor inconsistente
    val_inc = (df["valor_devolucao"] > df["valor_total"]).sum()
    desc_inc = (df["desconto_pct"] > 100).sum()
    qty_neg  = (df["quantidade"] < 0).sum()
    print()
    print(f"  valor_devolucao > total: {val_inc:>10,}")
    print(f"  desconto_pct > 100     : {desc_inc:>10,}")
    print(f"  quantidade negativa    : {qty_neg:>10,}")
    print(sep)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")

    print("Gerando dataset... (pode levar ~15s para 200k linhas)")
    df = gerar_dataset_ecommerce(n_pedidos=190_000)
    diagnostico(df)

    out = "ecommerce_sintetico.csv"
    df.to_csv(out, index=False)
    mem_mb = df.memory_usage(deep=True).sum() / 1e6
    print(f"\nArquivo salvo : {out}")
    print(f"Memória       : {mem_mb:.1f} MB")
    print("Dica          : pip install pyarrow → troque to_csv por to_parquet para arquivo ~10x menor.")
