-- Weekly job counts, feeding the "postings over time" chart.
-- Buckets use published_at (when the source published the job), not
-- ingested_at: published_at carries real history even while the pipeline is
-- young and runs irregularly, whereas ingested_at would only mirror our own
-- run schedule. Jobs without published_at are excluded. Until the pipeline
-- runs on a schedule (Phase 7), volume here reflects the sources' listing
-- windows, not full market history — expect few weeks of data.
-- Two grain levels, labelled by grouping_level:
--   * 'source'       – jobs per week per source.
--   * 'source_skill' – jobs per week per source per skill (from
--                      mart_skills; a job counts once per skill it has).

with by_source as (

    select
        'source'                                as grouping_level,
        date_trunc('week', published_at)::date  as week_start,
        source,
        null::text                              as skill,
        count(*)                                as job_count
    from {{ ref('int_jobs_normalized') }}
    where published_at is not null
    group by date_trunc('week', published_at)::date, source

),

by_source_skill as (

    select
        'source_skill'                          as grouping_level,
        date_trunc('week', published_at)::date  as week_start,
        source,
        skill,
        count(*)                                as job_count
    from {{ ref('mart_skills') }}
    where published_at is not null
    group by date_trunc('week', published_at)::date, source, skill

)

select * from by_source
union all
select * from by_source_skill
