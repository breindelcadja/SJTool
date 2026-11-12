-- SJTool's hosted database, for Supabase (https://supabase.com).
-- One-time setup: in your Supabase project, open SQL Editor, paste this whole
-- file and click Run. It's safe to run again; it won't erase data.
--
-- The tables match sjtool.db (database.py), so supabase_sync.py can copy data
-- between them.
--
-- Privacy rules (Row Level Security):
--   * The website (using the public "anon" key) can read pitches, their sources
--     and fact-checks, which are meant to be public.
--   * It can NEVER read the journalists table directly, so emails stay private.
--   * Sign-ups go through save_profile(), and each journalist's list comes from
--     get_feed(). Both are below.
--   * Your Python scripts use the secret service_role key, which skips these
--     rules. Never put that key in the website or on GitHub.

create table if not exists journalists (
    id                   text primary key,
    name                 text not null,
    email                text unique,            -- null when skipped
    beat                 text not null,
    area                 text not null,
    area_type            text,
    area_country         text,
    area_lat             double precision,
    area_lon             double precision,
    area_verified        integer not null default 0,
    publication          text not null,
    publication_source   text,                   -- wikidata, website, freelance
    publication_verified integer not null default 0,
    added_on             text,
    updated_on           text
);

create table if not exists pitches (
    pitch_id           text primary key,
    status             text,
    headline           text not null,
    summary            text,
    problem            text,
    solution           text,
    response           text,
    location           text,
    beat               text,
    why_now            text,
    usefulness_rating  text,
    usefulness_points  integer,
    full_pitch         jsonb not null            -- the whole pitch
);

create table if not exists pitch_sources (
    pitch_id    text not null references pitches(pitch_id) on delete cascade,
    url         text not null,
    author      text,
    posted_on   text,
    text        text,
    link_url    text,
    link_title  text,
    primary key (pitch_id, url)
);

create table if not exists fact_checks (
    pitch_id              text primary key references pitches(pitch_id) on delete cascade,
    confidence            text not null,         -- high, medium, low
    reason                text,
    solution_verdict      text,
    solution_explanation  text,
    solution_sources      jsonb,                 -- list of {title, url}
    checked_on            text
);

create table if not exists fact_check_claims (
    pitch_id     text not null references fact_checks(pitch_id) on delete cascade,
    position     integer not null,
    claim        text not null,
    verdict      text not null,                  -- confirmed, partly_confirmed, contradicted, not_found
    explanation  text,
    sources      jsonb,                          -- list of {title, url}
    primary key (pitch_id, position)
);

create table if not exists rankings (
    journalist_id  text not null references journalists(id) on delete cascade,
    pitch_id       text not null references pitches(pitch_id) on delete cascade,
    rank           integer not null,
    relevance      text not null,                -- high, medium, low, not_relevant
    reason         text,
    ranked_on      text,
    primary key (journalist_id, pitch_id)
);
create index if not exists rankings_by_journalist on rankings (journalist_id, rank);

-- ---------- Privacy rules ----------

alter table journalists       enable row level security;
alter table pitches           enable row level security;
alter table pitch_sources     enable row level security;
alter table fact_checks       enable row level security;
alter table fact_check_claims enable row level security;
alter table rankings          enable row level security;

drop policy if exists "Anyone can read pitches" on pitches;
create policy "Anyone can read pitches" on pitches for select using (true);
drop policy if exists "Anyone can read pitch sources" on pitch_sources;
create policy "Anyone can read pitch sources" on pitch_sources for select using (true);
drop policy if exists "Anyone can read fact-checks" on fact_checks;
create policy "Anyone can read fact-checks" on fact_checks for select using (true);
drop policy if exists "Anyone can read fact-check claims" on fact_check_claims;
create policy "Anyone can read fact-check claims" on fact_check_claims for select using (true);
-- journalists and rankings get no policies, so the website can't read them
-- directly. It uses the two functions below instead.

-- ---------- What the website calls ----------

-- Sign-up and profile updates. Returns the journalist's ID.
-- From the website:  supabase.rpc('save_profile', {p_name, p_email, ...})
-- Same email = update that profile. No email = a new profile each time.
create or replace function save_profile(
    p_name text, p_email text, p_beat text, p_area text, p_publication text,
    p_area_details jsonb default '{}'::jsonb,
    p_publication_details jsonb default '{}'::jsonb
) returns text
language plpgsql security definer set search_path = public as $$
declare
    clean_email text := nullif(lower(trim(coalesce(p_email, ''))), '');
    today text := to_char(now() at time zone 'utc', 'YYYY-MM-DD');
    profile_id text;
begin
    if coalesce(trim(p_name), '') = '' or coalesce(trim(p_beat), '') = ''
       or coalesce(trim(p_area), '') = '' or coalesce(trim(p_publication), '') = '' then
        raise exception 'Name, beat, area and publication are required.';
    end if;
    if clean_email is not null and clean_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' then
        raise exception 'That email doesn''t look right.';
    end if;

    if clean_email is not null then
        select id into profile_id from journalists where email = clean_email;
    end if;

    if profile_id is null then
        profile_id := coalesce(nullif(trim(both '-' from lower(
                          regexp_replace(p_name, '[^a-zA-Z0-9]+', '-', 'g'))), ''), 'journalist')
                      || '-' || substr(md5(random()::text), 1, 6);
        insert into journalists (id, name, email, beat, area, publication, added_on)
        values (profile_id, trim(p_name), clean_email, trim(p_beat), trim(p_area),
                trim(p_publication), today);
    end if;

    update journalists set
        name = trim(p_name), email = clean_email, beat = trim(p_beat),
        area = trim(p_area), publication = trim(p_publication),
        area_type = p_area_details->>'type',
        area_country = p_area_details->>'country',
        area_lat = (p_area_details->>'lat')::double precision,
        area_lon = (p_area_details->>'lon')::double precision,
        area_verified = case when coalesce((p_area_details->>'verified')::boolean, false) then 1 else 0 end,
        publication_source = p_publication_details->>'source',
        publication_verified = case when coalesce((p_publication_details->>'verified')::boolean, false) then 1 else 0 end,
        updated_on = today
    where id = profile_id;
    return profile_id;
end $$;

-- The pitches one journalist sees, best first. Same rules as database.py's
-- feed(): hides pitches rated not_relevant, and pitches whose fact-check came
-- back low. Pitches not fact-checked yet are shown as 'unchecked'.
-- From the website:  supabase.rpc('get_feed', {who: 'jane@example.com'})
create or replace function get_feed(who text, max_pitches integer default 10)
returns table (
    rank integer, relevance text, reason text, pitch_id text, headline text,
    summary text, solution text, location text, beat text,
    usefulness_points integer, fact_check_confidence text
)
language sql stable security definer set search_path = public as $$
    select r.rank, r.relevance, r.reason, p.pitch_id, p.headline, p.summary,
           p.solution, p.location, p.beat, p.usefulness_points,
           coalesce(f.confidence, 'unchecked')
    from rankings r
    join journalists j on j.id = r.journalist_id
    join pitches p on p.pitch_id = r.pitch_id
    left join fact_checks f on f.pitch_id = r.pitch_id
    where (j.id = who or j.email = lower(trim(who)))
      and r.relevance <> 'not_relevant'
      and coalesce(f.confidence, 'unchecked') <> 'low'
    order by r.rank
    limit least(greatest(max_pitches, 1), 50)
$$;

revoke all on function save_profile(text, text, text, text, text, jsonb, jsonb) from public;
revoke all on function get_feed(text, integer) from public;
grant execute on function save_profile(text, text, text, text, text, jsonb, jsonb) to anon, authenticated;
grant execute on function get_feed(text, integer) to anon, authenticated;
