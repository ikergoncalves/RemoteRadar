-- One row per job per skill, feeding the "most requested skills" chart.
-- Skills come in two shapes, labelled by skill_type:
--   * 'tag'      – free-form tags exploded from the JSONB array
--                  (RemoteOK and Remotive).
--   * 'category' – the single category label (Remotive and Adzuna; Adzuna
--                  has no free tags, so its only signal is the category,
--                  which for this pipeline is always broad, e.g. "IT Jobs").
-- Values are lowercased/trimmed so counts group cleanly across sources, and
-- deduplicated so a job contributes at most once per (skill, skill_type).

with jobs as (

    select
        job_key,
        source,
        category,
        tags,
        published_at
    from {{ ref('int_jobs_normalized') }}

),

tag_skills as (

    -- the jsonb_typeof filter is applied in a subquery so the lateral call
    -- never sees a non-array value (a JSON null scalar would make it error)
    select
        jobs_with_tags.job_key,
        jobs_with_tags.source,
        lower(trim(tag.value)) as skill,
        'tag'                  as skill_type,
        jobs_with_tags.published_at
    from (
        select job_key, source, tags, published_at
        from jobs
        where jsonb_typeof(tags) = 'array'
    ) as jobs_with_tags
    cross join lateral jsonb_array_elements_text(jobs_with_tags.tags) as tag(value)

),

category_skills as (

    select
        job_key,
        source,
        lower(trim(category)) as skill,
        'category'            as skill_type,
        published_at
    from jobs
    where category is not null

),

unioned as (

    select * from tag_skills
    union all
    select * from category_skills

)

select distinct
    job_key,
    source,
    skill,
    skill_type,
    published_at
from unioned
where skill <> ''
