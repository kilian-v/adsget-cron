-- Read path for the inspirations feature.
--
-- Until now the /retrieve service scraped Pinterest live on every user
-- request and only wrote to inspiration_images as a side effect: nothing
-- ever read the table back. These functions make it readable, with
-- relevance filtering, per-source weighting and randomized ordering inside
-- the relevant pool so repeated calls for the same keyword differ.

create extension if not exists pg_trgm;

create index if not exists image_queries_query_trgm_idx
  on public.image_queries using gin (query gin_trgm_ops);

create index if not exists image_queries_image_id_idx
  on public.image_queries (image_id);

create index if not exists inspiration_images_source_idx
  on public.inspiration_images (source);

-- Source weights: real ad creatives first, then design shots, then social pins.
create or replace function public.inspiration_source_weight(p_source text)
returns real
language sql
immutable
as $$
  select case p_source
    when 'adsoftheworld' then 1.8::real
    when 'dribbble'      then 1.4::real
    when 'behance'       then 1.15::real
    else 1.0::real
  end;
$$;

-- Normalize a free-text query into meaningful tokens.
create or replace function public.inspiration_tokens(p_query text)
returns text[]
language sql
immutable
as $$
  select coalesce(array_agg(t), '{}'::text[])
  from unnest(
    string_to_array(
      regexp_replace(lower(coalesce(p_query, '')), '[^a-z0-9]+', ' ', 'g'),
      ' '
    )
  ) as t
  where length(t) > 2
    and t not in (
      'ads', 'advertising', 'advertisement', 'campaign', 'poster', 'design',
      'the', 'and', 'for', 'with', 'from', 'that', 'this', 'your'
    );
$$;

-- Relevance-filtered, weighted-random pick. Random ordering inside the
-- relevant pool is what stops the same top rows coming back every call.
create or replace function public.search_inspirations(
  p_query text,
  p_limit int default 20,
  p_sources text[] default null,
  p_min_score real default 0.3
)
returns table (image_url text, source text, score real)
language sql
stable
security definer
set search_path = public
as $$
  with norm as (
    select regexp_replace(lower(coalesce(p_query, '')), '[^a-z0-9]+', ' ', 'g') as text,
           inspiration_tokens(p_query) as toks
  ),
  scored as (
    select i.image_url,
           i.source,
           max(
             greatest(
               similarity(iq.query, (select text from norm)),
               case
                 when cardinality((select toks from norm)) = 0 then 0::real
                 else (
                   select count(*)::real
                   from unnest((select toks from norm)) as t
                   where iq.query like '%' || t || '%'
                 ) / cardinality((select toks from norm))::real
               end
             )
           ) as score
    from image_queries iq
    join inspiration_images i on i.id = iq.image_id
    where (p_sources is null or i.source = any(p_sources))
    group by i.image_url, i.source
  ),
  pool as (
    select * from scored
    where score >= p_min_score
    order by score desc
    limit greatest(p_limit * 6, 60)
  )
  select image_url, source, score
  from pool
  order by random() * inspiration_source_weight(source) desc
  limit p_limit;
$$;

-- Padding when the relevant pool is thinner than the requested limit.
create or replace function public.random_inspirations(
  p_limit int default 20,
  p_sources text[] default null,
  p_exclude text[] default null
)
returns table (image_url text, source text)
language sql
stable
security definer
set search_path = public
as $$
  select i.image_url, i.source
  from inspiration_images i
  where (p_sources is null or i.source = any(p_sources))
    and (p_exclude is null or i.image_url <> all(p_exclude))
  order by random() * inspiration_source_weight(i.source) desc
  limit p_limit;
$$;

revoke all on function public.search_inspirations(text, int, text[], real) from anon, authenticated;
revoke all on function public.random_inspirations(int, text[], text[]) from anon, authenticated;
grant execute on function public.search_inspirations(text, int, text[], real) to service_role;
grant execute on function public.random_inspirations(int, text[], text[]) to service_role;
