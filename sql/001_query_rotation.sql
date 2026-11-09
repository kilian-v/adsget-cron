-- Query rotation state.
-- Applied to project bdeyhzbjkcsodhsuckmz as migration `add_search_queries_rotation`.
--
-- Note: inspiration_images(image_url) and image_queries(image_id, query) were
-- ALREADY unique in this project (inspiration_images_image_url_key and the
-- image_queries_pkey), so this migration does not recreate them.

create table if not exists public.search_queries (
    id                bigserial primary key,
    category          text        not null,
    query             text        not null,
    last_used_at      timestamptz not null default now(),
    last_new_images   integer     not null default 0,
    created_at        timestamptz not null default now(),
    constraint search_queries_category_query_key unique (category, query)
);

create index if not exists search_queries_category_last_used_idx
    on public.search_queries (category, last_used_at);

-- Written only by the scraper via the service role key; no anon access.
alter table public.search_queries enable row level security;

-- fetch_known_image_urls() filters by source on every run.
create index if not exists inspiration_images_source_idx
    on public.inspiration_images (source);
