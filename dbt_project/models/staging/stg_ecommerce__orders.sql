-- IMPLEMENTAÇÃO ATUAL (Seed)
-- Limpeza e deduplicação do seed denormalizado de e-commerce.
-- Responsabilidades desta camada: dedup, casts, renames, filtros de sanidade.
-- Proibido aqui: joins, agregações, lógica de negócio.

with source as (

    select * from {{ source('seed_raw', 'seed_ecommerce_sintetico') }}

),

cleaned as (

    select
        trim(order_id)                                  as order_id,
        cast(cliente_id as int64)                       as customer_id,

        -- fonte mistura timestamps com offset ("...08:51:08-03:00") e naive;
        -- SAFE_CAST resolve ambos, assumindo UTC para os naive
        safe_cast(data_pedido as timestamp)             as created_at,

        trim(nome_produto)                              as product_name,
        lower(trim(categoria))                          as category,

        -- quantidade negativa só re-codifica devolução, já capturada em tem_devolucao
        abs(cast(quantidade as int64))                  as quantity,

        cast(preco_unitario as numeric)                 as unit_price_brl,

        -- desconto > 100% é impossível → tratado como desconhecido
        case
            when desconto_pct between 0 and 100
                then cast(desconto_pct as numeric)
        end                                             as discount_pct,

        cast(valor_bruto as numeric)                    as gross_amount_brl,
        cast(valor_total as numeric)                    as total_amount_brl,
        lower(trim(status))                             as status,
        lower(trim(canal))                              as channel,
        lower(trim(metodo_pagamento))                   as payment_method,
        {{ normalize_estado('estado_cliente') }}        as customer_state,
        trim(cep_cliente)                               as customer_zip,
        cast(review_score as int64)                     as review_score,
        cast(tem_devolucao as boolean)                  as has_return,
        safe_cast(data_devolucao as timestamp)          as returned_at,
        cast(valor_devolucao as numeric)                as return_amount_brl

    from source
    where order_id is not null

),

deduplicated as (

    select
        *,
        -- quasi-duplicatas têm o mesmo order_id com timestamp/status divergentes;
        -- mantemos o registro mais recente
        row_number() over (
            partition by order_id
            order by created_at desc
        ) as _row_num
        -- ROADMAP FUTURO (CDC/Airbyte): ordenar por _airbyte_emitted_at desc
    from cleaned

)

select * except (_row_num)
from deduplicated
where _row_num = 1
