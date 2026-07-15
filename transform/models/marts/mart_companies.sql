-- Job counts per company across the three sources, feeding the "companies
-- hiring remote the most" chart.
-- Company names are grouped case-insensitively (lower + trim) but otherwise
-- taken as published: the same employer spelled differently across sources
-- ("Acme Inc" vs "Acme") counts as separate companies — known limitation,
-- no fuzzy matching in this phase.

with jobs as (

    select
        company,
        source,
        published_at
    from {{ ref('int_jobs_normalized') }}
    where company is not null
      and trim(company) <> ''

)

select
    lower(trim(company))                             as company_key,
    -- display variant: the alphabetically last spelling seen for this key
    max(trim(company))                               as company,
    count(*)                                         as job_count,
    count(distinct source)                           as source_count,
    string_agg(distinct source, ', ' order by source) as sources,
    min(published_at)                                as first_published_at,
    max(published_at)                                as last_published_at
from jobs
group by lower(trim(company))
