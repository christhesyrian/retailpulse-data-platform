-- A fact's item_key must point at the item the fact is actually about.
--
-- This is not the same assertion as the `relationships` test on item_key, and
-- the difference is the whole reason this file exists. A relationships test
-- asks "does this key exist in the dimension?" — referential integrity. It
-- cannot ask "does it still mean the same thing?"
--
-- dim_item once keyed on `row_number() over (order by variation_id)`. That
-- rank is recomputed on every build, so inserting a catalog item shifts the
-- key of everything sorting after it. With fact_order_line full-refresh that
-- was survivable: both sides were rebuilt together. Once the fact table became
-- incremental, rows merged on earlier runs kept their old numbers while the
-- dimension renumbered underneath them. One refresh that added 17 catalog
-- items left 135,534 of 153,314 rows pointing at the wrong item, and every
-- category breakdown silently wrong — SCRATCHER reported at $56k against an
-- actual $286k.
--
-- Every relationships test passed throughout, because every key still existed.
--
-- So this checks the *semantics*: resolve the item two independent ways —
-- through the surrogate key the fact carries, and through Square's own
-- catalog id — and require the same item back.
{% set checks = [
    ('fact_order_line', 'catalog_object_id'),
    ('fact_inventory_snapshot', 'square_catalog_object_id'),
] %}

{% for model, natural_key in checks %}
select
    '{{ model }}' as fact_model,
    f.{{ natural_key }} as square_catalog_object_id,
    f.item_key as key_on_the_fact,
    by_natural_key.item_key as key_the_dimension_assigns
from {{ ref(model) }} f
join {{ ref('dim_item') }} by_natural_key
    on f.{{ natural_key }} = by_natural_key.square_catalog_object_id
where f.item_key is distinct from by_natural_key.item_key
{% if not loop.last %}union all{% endif %}
{% endfor %}
