{#
    Normaliza as variações de grafia de estado presentes na fonte
    ("SP", " sp", "São Paulo", "S.P."...) para a sigla UF de 2 letras.
#}
{% macro normalize_estado(column_name) %}
    case replace(upper(trim({{ column_name }})), '.', '')
        when 'SÃO PAULO'          then 'SP'
        when 'RIO DE JANEIRO'     then 'RJ'
        when 'MINAS GERAIS'       then 'MG'
        when 'RIO GRANDE DO SUL'  then 'RS'
        when 'PARANÁ'             then 'PR'
        when 'SANTA CATARINA'     then 'SC'
        when 'BAHIA'              then 'BA'
        when 'GOIÁS'              then 'GO'
        when 'PERNAMBUCO'         then 'PE'
        when 'CEARÁ'              then 'CE'
        else replace(upper(trim({{ column_name }})), '.', '')
    end
{% endmacro %}
