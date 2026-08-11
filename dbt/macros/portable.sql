{#
  The places where warehouses genuinely disagree, isolated behind
  adapter-dispatched macros.

  Everything else in this project's 33 models is portable SQL that compiles
  unchanged on DuckDB, BigQuery and Snowflake. These are not, and every one was
  found the only way such things are found — by running the build against a
  second warehouse and reading the errors.

  Keeping them here rather than inline means the fact models stay readable and
  there is exactly one place to add a third dialect.

  Two kinds of disagreement live here, and the second kind is the dangerous one:

    * Spellings that fail loudly. `isodow`, `strftime`, `interval 30 day` —
      the build stops and tells you exactly where. Annoying, not risky.

    * Spellings that succeed and mean something different. `dayname` returns
      "Monday" on DuckDB and "Mon" on Snowflake; `monthname` returns "August"
      and "Aug". Both compile everywhere. The dashboard orders weekdays against
      a hard-coded ["Monday", ...], so on Snowflake the popular-times chart
      would have quietly rendered an empty grid rather than raised anything.
      Macros in this file therefore normalise *values*, not just syntax.
#}

{#-
  UTC timestamp -> wall-clock time in a given zone, as a naive local timestamp.

  This is the single most important expression in the project. Square records
  every sale in UTC; a store on America/Los_Angeles does its heaviest trade in
  the evening, which is already the next day in UTC. Reading the date straight
  off the UTC timestamp put a year of Friday-evening sales on Saturday, and
  every total still reconciled — so it was invisible until someone noticed the
  hour-of-day chart peaking at 2am.

  All three implementations must be DST-correct: -7 in July and -8 in January,
  not a fixed offset.
-#}
{% macro to_local_time(timezone_expr, utc_timestamp) %}
  {{ return(adapter.dispatch('to_local_time', 'retailpulse')(timezone_expr, utc_timestamp)) }}
{% endmacro %}

{% macro default__to_local_time(timezone_expr, utc_timestamp) %}
    {#- DuckDB/Postgres: the inner call reads the naive timestamp as UTC and
        yields an instant; the outer renders that instant in the target zone. -#}
    timezone({{ timezone_expr }}, timezone('UTC', {{ utc_timestamp }}))
{% endmacro %}

{% macro snowflake__to_local_time(timezone_expr, utc_timestamp) %}
    {#- Snowflake has no `timezone()` at all — the build fails with
        "Unknown functions TIMEZONE". Its three-argument CONVERT_TIMEZONE does
        the whole job in one call: source zone, target zone, naive timestamp. -#}
    convert_timezone('UTC', {{ timezone_expr }}, cast({{ utc_timestamp }} as timestamp_ntz))
{% endmacro %}

{% macro bigquery__to_local_time(timezone_expr, utc_timestamp) %}
    {#- BigQuery's DATETIME(timestamp, zone) renders an instant as local wall
        clock, which is the same operation under a different name. -#}
    datetime(cast({{ utc_timestamp }} as timestamp), {{ timezone_expr }})
{% endmacro %}


{#-
  Replace every match of a regex, not just the first.

  "Every" is the default on Snowflake and BigQuery, and is opt-in via a flags
  argument on DuckDB/Postgres. Passing that flag where it isn't expected is not
  a no-op: Snowflake's fourth argument is a numeric *position*, so `'g'` fails
  with the genuinely baffling "Numeric value 'g' is not recognized".
-#}
{% macro regexp_replace_all(column, pattern, replacement) %}
  {{ return(adapter.dispatch('regexp_replace_all', 'retailpulse')(column, pattern, replacement)) }}
{% endmacro %}

{% macro default__regexp_replace_all(column, pattern, replacement) %}
    regexp_replace({{ column }}, '{{ pattern }}', '{{ replacement }}', 'g')
{% endmacro %}

{% macro snowflake__regexp_replace_all(column, pattern, replacement) %}
    regexp_replace({{ column }}, '{{ pattern }}', '{{ replacement }}')
{% endmacro %}

{% macro bigquery__regexp_replace_all(column, pattern, replacement) %}
    regexp_replace({{ column }}, r'{{ pattern }}', '{{ replacement }}')
{% endmacro %}


{#-
  Difference between two dates/timestamps in whole units of `part`.

  Three spellings, three signatures:
    DuckDB/Postgres  date_diff('day', start, end)   -> end - start
    Snowflake        datediff(day, start, end)      -> end - start, no underscore
    BigQuery         date_diff(end, start, DAY)     -> arguments reversed

  The BigQuery ordering is the trap: it compiles fine and returns the negative
  of what every other adapter returns, so the failure is a sign flip in a
  number that still looks plausible rather than an error.
-#}
{% macro date_diff_in(part, start_date, end_date) %}
  {{ return(adapter.dispatch('date_diff_in', 'retailpulse')(part, start_date, end_date)) }}
{% endmacro %}

{% macro default__date_diff_in(part, start_date, end_date) %}
    date_diff('{{ part }}', {{ start_date }}, {{ end_date }})
{% endmacro %}

{% macro snowflake__date_diff_in(part, start_date, end_date) %}
    datediff({{ part }}, {{ start_date }}, {{ end_date }})
{% endmacro %}

{% macro bigquery__date_diff_in(part, start_date, end_date) %}
    date_diff({{ end_date }}, {{ start_date }}, {{ part }})
{% endmacro %}


{#-
  ISO day of week: 1 = Monday ... 7 = Sunday.

  DuckDB spells it `isodow`. Snowflake has `dayofweekiso`. BigQuery has neither
  and its `extract(dayofweek ...)` is 1 = Sunday ... 7 = Saturday, which is not
  a renaming but a different numbering — rotating it is the only honest fix.

  ISO rather than either vendor's default because the whole project treats the
  week as starting on Monday (`date_trunc('week', ...)` agrees on Monday across
  all three), and `is_weekend` is `in (6, 7)`.
-#}
{% macro day_of_week_iso(date_expr) %}
  {{ return(adapter.dispatch('day_of_week_iso', 'retailpulse')(date_expr)) }}
{% endmacro %}

{% macro default__day_of_week_iso(date_expr) %}
    isodow({{ date_expr }})
{% endmacro %}

{% macro snowflake__day_of_week_iso(date_expr) %}
    dayofweekiso({{ date_expr }})
{% endmacro %}

{% macro bigquery__day_of_week_iso(date_expr) %}
    {#- 1=Sun..7=Sat -> 1=Mon..7=Sun. Sunday: (1+5) mod 7 + 1 = 7. -#}
    mod(extract(dayofweek from {{ date_expr }}) + 5, 7) + 1
{% endmacro %}


{#-
  Full English weekday name: "Monday", not "Mon".

  This is a value normaliser, not a spelling fix. `dayname` exists on both
  DuckDB and Snowflake and compiles happily on each; DuckDB returns "Monday"
  and Snowflake returns "Mon". Nothing fails — the dashboard just stops
  matching its weekday ordering and draws an empty chart.

  The long form wins because it is what reaches the screen unmodified.
-#}
{% macro day_name(date_expr) %}
  {{ return(adapter.dispatch('day_name', 'retailpulse')(date_expr)) }}
{% endmacro %}

{% macro default__day_name(date_expr) %}
    dayname({{ date_expr }})
{% endmacro %}

{% macro snowflake__day_name(date_expr) %}
    {#- Snowflake's TO_CHAR has no full-weekday format element, so the mapping
        is explicit. Driven off the ISO number so it cannot disagree with
        day_of_week_iso. -#}
    decode({{ snowflake__day_of_week_iso(date_expr) }},
        1, 'Monday', 2, 'Tuesday', 3, 'Wednesday', 4, 'Thursday',
        5, 'Friday', 6, 'Saturday', 7, 'Sunday')
{% endmacro %}

{% macro bigquery__day_name(date_expr) %}
    format_date('%A', cast({{ date_expr }} as date))
{% endmacro %}


{#-
  Full English month name: "August", not "Aug". Same trap as day_name —
  `monthname` compiles on DuckDB and Snowflake and returns different strings.
-#}
{% macro month_name(date_expr) %}
  {{ return(adapter.dispatch('month_name', 'retailpulse')(date_expr)) }}
{% endmacro %}

{% macro default__month_name(date_expr) %}
    monthname({{ date_expr }})
{% endmacro %}

{% macro snowflake__month_name(date_expr) %}
    decode(month({{ date_expr }}),
        1, 'January',   2, 'February', 3, 'March',     4, 'April',
        5, 'May',       6, 'June',     7, 'July',      8, 'August',
        9, 'September', 10, 'October', 11, 'November', 12, 'December')
{% endmacro %}

{% macro bigquery__month_name(date_expr) %}
    format_date('%B', cast({{ date_expr }} as date))
{% endmacro %}


{#-
  Shift a date by a whole number of days, returning a date.

  Every date shift in this project is expressible in days — `interval 8 week`
  is 56 days and `h * interval 7 day` is `h * 7` days — so one macro covers all
  of them and there is no `part` argument to get wrong.

  A bare `interval N day` literal is DuckDB/Postgres syntax and is a hard
  failure on Snowflake ("syntax error ... unexpected '30'"). Plain `date + int`
  happens to work on both DuckDB and Snowflake but not on BigQuery, so the
  macro is the spelling that holds on all three.
-#}
{% macro add_days(date_expr, n_days) %}
  {{ return(adapter.dispatch('add_days', 'retailpulse')(date_expr, n_days)) }}
{% endmacro %}

{% macro default__add_days(date_expr, n_days) %}
    {#- DuckDB has no DATE + BIGINT operator and date arithmetic yields BIGINT,
        so the offset is cast to INTEGER rather than left to infer. -#}
    (cast({{ date_expr }} as date) + cast({{ n_days }} as integer))
{% endmacro %}

{% macro snowflake__add_days(date_expr, n_days) %}
    dateadd(day, {{ n_days }}, cast({{ date_expr }} as date))
{% endmacro %}

{% macro bigquery__add_days(date_expr, n_days) %}
    date_add(cast({{ date_expr }} as date), interval cast({{ n_days }} as int64) day)
{% endmacro %}


{#-
  Is this floating-point value NaN?

  Reachable because `regr_slope` over a single point is undefined, and the
  forecast has to tell "no trend could be fitted" apart from "the trend is
  zero" — `greatest(0, NULL)` collapses to 0, so a missing guard silently
  forecasts nothing for every brand-new item.

  The warehouses disagree twice over. DuckDB returns NaN from that regression
  and offers `isnan`. Snowflake returns NULL instead, has no `isnan` at all
  ("Unknown function ISNAN"), and — unlike IEEE 754 and unlike DuckDB —
  evaluates `NaN = NaN` as TRUE, which is what makes the equality test below a
  valid probe there and an invalid one anywhere else.
-#}
{% macro is_nan(numeric_expr) %}
  {{ return(adapter.dispatch('is_nan', 'retailpulse')(numeric_expr)) }}
{% endmacro %}

{% macro default__is_nan(numeric_expr) %}
    isnan({{ numeric_expr }})
{% endmacro %}

{% macro snowflake__is_nan(numeric_expr) %}
    ({{ numeric_expr }} = cast('NaN' as double))
{% endmacro %}

{% macro bigquery__is_nan(numeric_expr) %}
    is_nan({{ numeric_expr }})
{% endmacro %}


{#-
  A gap-free sequence of integers, `low`..`high` inclusive, as column `n`.

  Row generation is the one thing here with no common spelling at all. DuckDB
  has `generate_series`/`range` returning a list to `unnest`; Snowflake has
  `table(generator(rowcount => N))`, whose N must be a literal constant and so
  cannot be driven by a subquery; BigQuery has `unnest(generate_array(...))`.
  Three incompatible shapes, not three names for one shape.

  So this is the exception to the file: rather than dispatch three
  implementations, it uses one spelling that is ordinary SQL everywhere — a
  cross join of digit tables, summed positionally into a counter. Slower in
  principle than a native generator, irrelevant in practice at these sizes, and
  it removes a dialect from the problem instead of adding a branch to it.

  `high` and `low` must be Jinja integers, because the number of digit levels
  is chosen at compile time from their span. Callers wanting data-driven bounds
  (dim_date's spine) generate a generous fixed range and filter it.
-#}
{% macro integers(low, high) -%}
{%- set span = high - low + 1 -%}
{%- set ns = namespace(levels=1, capacity=10) -%}
{%- for _ in range(9) -%}
  {%- if ns.capacity < span -%}
    {%- set ns.levels = ns.levels + 1 -%}
    {%- set ns.capacity = ns.capacity * 10 -%}
  {%- endif -%}
{%- endfor -%}
select n
from (
    select {{ low }}{% for i in range(ns.levels) %} + {% if i > 0 %}{{ 10 ** i }} * {% endif %}d{{ i }}.n{% endfor %} as n
    from ({{ retailpulse.digits() }}) d0
    {%- for i in range(1, ns.levels) %}
    cross join ({{ retailpulse.digits() }}) d{{ i }}
    {%- endfor %}
) seq
where n <= {{ high }}
{%- endmacro %}


{#- One base-10 digit, 0..9. The building block `integers` crosses with itself. -#}
{% macro digits() -%}
select 0 as n union all select 1 union all select 2 union all select 3 union all
select 4 union all select 5 union all select 6 union all select 7 union all
select 8 union all select 9
{%- endmacro %}
