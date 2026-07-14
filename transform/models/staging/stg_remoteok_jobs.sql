-- One row per job posting from the RemoteOK raw payloads.
-- The raw table holds one consolidated payload per extraction run, so the
-- same job shows up in every run while the posting stays live; the final
-- step keeps only the most recent ingestion of each job.
-- RemoteOK serializes numbers as strings ("id", "salary_min", ...), and
-- "0" in the salary fields means "not provided".

with raw_payloads as (

    select
        id as raw_id,
        ingested_at,
        source,
        payload
    from {{ source('raw', 'remoteok_jobs') }}

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
        job ->> 'id'                                  as job_id,
        source,
        job ->> 'position'                            as title,
        job ->> 'company'                             as company,
        null::text                                    as category,
        job -> 'tags'                                 as tags,
        nullif(job ->> 'location', '')                as location,
        nullif(job ->> 'salary_min', '0')::numeric    as salary_min,
        nullif(job ->> 'salary_max', '0')::numeric    as salary_max,
        null::text                                    as salary_text,
        (job ->> 'date')::timestamptz                 as published_at,
        job ->> 'url'                                 as url,
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
