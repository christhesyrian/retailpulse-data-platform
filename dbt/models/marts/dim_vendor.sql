-- One row per distinct vendor named in the operator-maintained cost file.
select
    row_number() over (order by vendor_name) as vendor_key,
    vendor_name,
    count(*) as variation_count
from {{ ref('stg_vendor_costs') }}
where vendor_name is not null
group by vendor_name
