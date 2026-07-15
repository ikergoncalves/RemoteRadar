-- Salary statistics in USD over jobs with structured salary data
-- (salary_source in 'structured_usd'/'structured_converted'; text_only and
-- missing jobs are excluded because they have no USD values).
-- Statistics are computed on salary_mid_usd — the midpoint of the posted
-- range, falling back to whichever bound exists — so every salaried job
-- contributes one number.
-- Three grain levels are unioned and labelled by grouping_level, so the
-- dashboard picks one level instead of re-aggregating averages:
--   * 'source'                – per source.
--   * 'source_salary_source'  – per source per confidence label, making it
--                               explicit which numbers come from fixed-rate
--                               conversion.
--   * 'source_category'       – per source per category label (sources
--                               without categories, i.e. RemoteOK, do not
--                               appear at this level).

with salaried_jobs as (

    select
        source,
        salary_source,
        category,
        salary_min_usd,
        salary_max_usd,
        coalesce(
            (salary_min_usd + salary_max_usd) / 2.0,
            salary_min_usd,
            salary_max_usd
        ) as salary_mid_usd
    from {{ ref('int_jobs_normalized') }}
    where salary_source in ('structured_usd', 'structured_converted')

),

by_source as (

    select
        'source'          as grouping_level,
        source,
        null::text        as salary_source,
        null::text        as category,
        count(*)          as job_count,
        round(avg(salary_mid_usd))                                                  as avg_salary_usd,
        round((percentile_cont(0.5) within group (order by salary_mid_usd))::numeric) as median_salary_usd,
        min(salary_min_usd)                                                         as min_salary_usd,
        max(salary_max_usd)                                                         as max_salary_usd
    from salaried_jobs
    group by source

),

by_source_salary_source as (

    select
        'source_salary_source' as grouping_level,
        source,
        salary_source,
        null::text             as category,
        count(*)               as job_count,
        round(avg(salary_mid_usd))                                                  as avg_salary_usd,
        round((percentile_cont(0.5) within group (order by salary_mid_usd))::numeric) as median_salary_usd,
        min(salary_min_usd)                                                         as min_salary_usd,
        max(salary_max_usd)                                                         as max_salary_usd
    from salaried_jobs
    group by source, salary_source

),

by_source_category as (

    select
        'source_category' as grouping_level,
        source,
        null::text        as salary_source,
        category,
        count(*)          as job_count,
        round(avg(salary_mid_usd))                                                  as avg_salary_usd,
        round((percentile_cont(0.5) within group (order by salary_mid_usd))::numeric) as median_salary_usd,
        min(salary_min_usd)                                                         as min_salary_usd,
        max(salary_max_usd)                                                         as max_salary_usd
    from salaried_jobs
    where category is not null
    group by source, category

)

select * from by_source
union all
select * from by_source_salary_source
union all
select * from by_source_category
