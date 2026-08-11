{#
  The two places where warehouses genuinely disagree, isolated behind
  adapter-dispatched macros.

  Everything else in this project's 33 models is portable SQL that compiles
  unchanged on DuckDB, BigQuery and Snowflake. These two are not, and both were
  found the only way such things are found — by running the build against a
  second warehouse and reading the errors.

  Keeping them here rather than inline means the fact models stay readable and
  there is exactly one place to add a third dialect.
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
