{#
  Generic category-name normalization: uppercase, trim, and collapse runs of
  whitespace to a single space. This alone merges pure case/spacing duplicates
  (e.g. 'Beer' and 'BEER', 'Chips ' and 'CHIPS'). Typos and singular/plural
  variants are handled on top of this by the optional category_overrides map.
#}
{% macro normalize_category(col) %}
    nullif(upper(trim({{ regexp_replace_all(col, '\\s+', ' ') }})), '')
{% endmacro %}
