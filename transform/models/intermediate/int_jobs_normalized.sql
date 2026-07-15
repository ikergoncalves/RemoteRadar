-- Unifies the three staging models into one row per job posting, with
-- salaries normalized to USD:
--   * RemoteOK already reports USD, so values pass through unchanged.
--   * Adzuna reports the currency of the search country; values are
--     converted with the FIXED approximate rates in seeds/exchange_rates.csv
--     (see that seed's schema.yml for the caveats).
--   * Remotive only provides free-text salary, so the structured USD
--     columns stay null and salary_text is kept for future parsing.
-- salary_source labels how trustworthy the USD columns are, so the
-- dashboard can filter or warn. job_id is only unique within a source;
-- job_key (source + job_id) is the cross-source primary key.

with remotive as (

    select
        job_id,
        source,
        title,
        company,
        category,
        tags,
        location,
        salary_min,
        salary_max,
        null::text                                  as salary_currency,
        null::numeric                               as salary_min_usd,
        null::numeric                               as salary_max_usd,
        salary_text,
        case
            when salary_text is not null then 'text_only'
            else 'missing'
        end                                         as salary_source,
        null::boolean                               as salary_is_predicted,
        published_at,
        url,
        ingested_at
    from {{ ref('stg_remotive_jobs') }}

),

remoteok as (

    select
        job_id,
        source,
        title,
        company,
        category,
        tags,
        location,
        salary_min,
        salary_max,
        case
            when salary_min is not null or salary_max is not null then 'USD'
        end                                         as salary_currency,
        salary_min                                  as salary_min_usd,
        salary_max                                  as salary_max_usd,
        salary_text,
        case
            when salary_min is not null or salary_max is not null
                then 'structured_usd'
            else 'missing'
        end                                         as salary_source,
        null::boolean                               as salary_is_predicted,
        published_at,
        url,
        ingested_at
    from {{ ref('stg_remoteok_jobs') }}

),

adzuna as (

    select
        jobs.job_id,
        jobs.source,
        jobs.title,
        jobs.company,
        jobs.category,
        jobs.tags,
        jobs.location,
        jobs.salary_min,
        jobs.salary_max,
        case
            when jobs.salary_min is not null or jobs.salary_max is not null
                then rates.currency_code
        end                                         as salary_currency,
        round(jobs.salary_min * rates.usd_rate)     as salary_min_usd,
        round(jobs.salary_max * rates.usd_rate)     as salary_max_usd,
        jobs.salary_text,
        case
            when jobs.salary_min is null and jobs.salary_max is null
                then 'missing'
            -- search_country missing from the seed: values exist but cannot
            -- be converted, so the USD columns are null (documented caveat)
            when rates.usd_rate is null
                then 'missing'
            when rates.currency_code = 'USD'
                then 'structured_usd'
            else 'structured_converted'
        end                                         as salary_source,
        jobs.salary_is_predicted,
        jobs.published_at,
        jobs.url,
        jobs.ingested_at
    from {{ ref('stg_adzuna_jobs') }} as jobs
    left join {{ ref('exchange_rates') }} as rates
        on jobs.search_country = rates.country_code

),

unioned as (

    select * from remotive
    union all
    select * from remoteok
    union all
    select * from adzuna

)

select
    source || ':' || job_id as job_key,
    job_id,
    source,
    title,
    company,
    category,
    tags,
    location,
    salary_min,
    salary_max,
    salary_currency,
    salary_min_usd,
    salary_max_usd,
    salary_text,
    salary_source,
    salary_is_predicted,
    published_at,
    url,
    ingested_at
from unioned
