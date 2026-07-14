-- One row per job posting from the Adzuna raw payloads.
-- The raw table holds one consolidated payload per extraction run, so the
-- same job shows up in every run while the posting stays live; the final
-- step keeps only the most recent ingestion of each job.
-- Adzuna nests company, location and category as objects with display
-- labels; salaries are numeric and may be model-predicted
-- (salary_is_predicted).

with raw_payloads as (

    select
        id as raw_id,
        ingested_at,
        source,
        payload
    from {{ source('raw', 'adzuna_jobs') }}

),

jobs as (

    select
        raw_id,
        ingested_at,
        source,
        payload ->> 'country'                    as search_country,
        jsonb_array_elements(payload -> 'jobs')  as job
    from raw_payloads

),

parsed as (

    select
        job ->> 'id'                                  as job_id,
        source,
        job ->> 'title'                               as title,
        job -> 'company' ->> 'display_name'           as company,
        job -> 'category' ->> 'label'                 as category,
        null::jsonb                                   as tags,
        job -> 'location' ->> 'display_name'          as location,
        (job ->> 'salary_min')::numeric               as salary_min,
        (job ->> 'salary_max')::numeric               as salary_max,
        null::text                                    as salary_text,
        -- serialized as the strings "0"/"1"; tolerate JSON booleans as well
        (job ->> 'salary_is_predicted') in ('1', 'true') as salary_is_predicted,
        (job ->> 'created')::timestamptz              as published_at,
        job ->> 'redirect_url'                        as url,
        search_country,
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
    salary_is_predicted,
    published_at,
    url,
    search_country,
    ingested_at,
    raw_id
from deduped
where row_num = 1
