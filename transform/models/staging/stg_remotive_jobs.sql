-- One row per job posting from the Remotive raw payloads.
-- The raw table holds one consolidated payload per extraction run, so the
-- same job shows up in every run while the posting stays live; the final
-- step keeps only the most recent ingestion of each job.

with raw_payloads as (

    select
        id as raw_id,
        ingested_at,
        source,
        payload
    from {{ source('raw', 'remotive_jobs') }}

),

jobs as (

    select
        raw_id,
        ingested_at,
        source,
        jsonb_array_elements(payload -> 'jobs') as job
    from raw_payloads

),

parsed as (

    select
        job ->> 'id'                                            as job_id,
        source,
        job ->> 'title'                                         as title,
        job ->> 'company_name'                                  as company,
        job ->> 'category'                                      as category,
        job -> 'tags'                                           as tags,
        job ->> 'candidate_required_location'                   as location,
        null::numeric                                           as salary_min,
        null::numeric                                           as salary_max,
        nullif(job ->> 'salary', '')                            as salary_text,
        -- publication_date has no timezone; Remotive publishes in UTC
        (job ->> 'publication_date')::timestamp at time zone 'utc' as published_at,
        job ->> 'url'                                           as url,
        ingested_at,
        raw_id
    from jobs

),

deduped as (

    select
        *,
        row_number() over (
            partition by job_id
            order by ingested_at desc, raw_id desc
        ) as row_num
    from parsed

)

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
    salary_text,
    published_at,
    url,
    ingested_at,
    raw_id
from deduped
where row_num = 1
