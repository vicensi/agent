{#
    Override do comportamento padrão: quando um modelo declara +schema,
    o dataset no BigQuery recebe exatamente esse nome (raw / staging / marts),
    sem o prefixo do dataset do target.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}

    {%- set default_schema = target.schema -%}
    {%- if custom_schema_name is none -%}

        {{ default_schema }}

    {%- else -%}

        {{ custom_schema_name | trim }}

    {%- endif -%}

{%- endmacro %}
