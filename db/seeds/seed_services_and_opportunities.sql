-- Seed services and opportunities for the public catalog.
--
-- Run this yourself in the Supabase SQL Editor (Dashboard → SQL Editor → paste → Run).
-- Do not apply it as a numbered migration, and do not run it from the API deploy.
-- Safe to re-run: fixed ids are updated in place, so it does not create duplicates.
--
-- Needs the categories table and products.listing_meta (migration 029) plus
-- shops.is_personal (migration 043). It does not require image_keys (045).
-- Listings are text-only: image_urls stays empty.
--
-- Account (not a real seller):
--   email seed-listings@example.invalid
--   user  aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1
--   shops midora-seed-services, midora-seed-opportunities
-- WhatsApp values are placeholders and are not real contacts.
-- If that email or phone already belongs to a different user id, stop. Do not
-- repoint these rows at a real account.

BEGIN;

INSERT INTO public.users (
    id, email, password_hash, full_name, user_role,
    email_verified, phone_number, phone_verified, status
) VALUES (
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1',
    'seed-listings@example.invalid',
    '!seed-not-a-login',
    'Midora Seed Listings',
    'merchant',
    true,
    '+256700000001',
    true,
    'active'
)
ON CONFLICT (id) DO UPDATE SET
    email = EXCLUDED.email,
    full_name = EXCLUDED.full_name,
    user_role = EXCLUDED.user_role,
    phone_number = EXCLUDED.phone_number,
    phone_verified = true,
    status = 'active',
    updated_at = now();

INSERT INTO public.profiles (id, full_name, phone_number, user_role)
VALUES (
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1',
    'Midora Seed Listings',
    '+256700000001',
    'merchant'
)
ON CONFLICT (id) DO UPDATE SET
    full_name = EXCLUDED.full_name,
    phone_number = EXCLUDED.phone_number,
    user_role = EXCLUDED.user_role;

INSERT INTO public.shops (
    id, owner_id, name, slug, description, whatsapp_number,
    location, shop_type, category, is_active, is_personal
) VALUES
(
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1',
    'Midora Seed Services',
    'midora-seed-services',
    'Sample services for the catalog. The WhatsApp number is a placeholder and is not a real contact.',
    '+256700000011',
    '{"city":"Kampala","country":"Uganda","display":"Kampala, Uganda"}'::jsonb,
    'service',
    'Services',
    true,
    false
),
(
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1',
    'Midora Seed Opportunities',
    'midora-seed-opportunities',
    'Sample jobs and opportunities for the catalog. The WhatsApp number is a placeholder and is not a real contact.',
    '+256700000012',
    '{"city":"Kampala","country":"Uganda","display":"Kampala, Uganda"}'::jsonb,
    'both',
    'Opportunities',
    true,
    false
)
ON CONFLICT (id) DO UPDATE SET
    owner_id = EXCLUDED.owner_id,
    name = EXCLUDED.name,
    slug = EXCLUDED.slug,
    description = EXCLUDED.description,
    whatsapp_number = EXCLUDED.whatsapp_number,
    location = EXCLUDED.location,
    shop_type = EXCLUDED.shop_type,
    category = EXCLUDED.category,
    is_active = true,
    is_personal = false,
    updated_at = now();

CREATE OR REPLACE FUNCTION pg_temp.field_key(entry jsonb)
RETURNS text
LANGUAGE sql
AS $func$
    SELECT left(trim(both '_' FROM regexp_replace(
        replace(replace(lower(trim(coalesce(
            NULLIF(entry->>'key', ''),
            NULLIF(entry->>'name', ''),
            NULLIF(entry->>'field', ''),
            NULLIF(entry->>'field_key', ''),
            NULLIF(entry->>'id', ''),
            ''
        ))), '-', '_'), ' ', '_'),
        '[^a-z0-9_]+', '_', 'g'
    )), 64);
$func$;

CREATE OR REPLACE FUNCTION pg_temp.canonical_type(entry jsonb)
RETURNS text
LANGUAGE sql
AS $func$
    SELECT CASE lower(trim(coalesce(
        NULLIF(entry->>'type', ''),
        NULLIF(entry->>'kind', ''),
        NULLIF(entry->>'field_type', ''),
        NULLIF(entry->>'input_type', ''),
        'text'
    )))
        WHEN 'string' THEN 'text'
        WHEN 'input' THEN 'text'
        WHEN 'textarea' THEN 'text'
        WHEN 'integer' THEN 'number'
        WHEN 'int' THEN 'number'
        WHEN 'float' THEN 'number'
        WHEN 'decimal' THEN 'number'
        WHEN 'enum' THEN 'select'
        WHEN 'dropdown' THEN 'select'
        WHEN 'choice' THEN 'select'
        WHEN 'choices' THEN 'select'
        WHEN 'bool' THEN 'boolean'
        WHEN 'checkbox' THEN 'boolean'
        WHEN 'datetime' THEN 'date'
        WHEN 'text' THEN 'text'
        WHEN 'number' THEN 'number'
        WHEN 'select' THEN 'select'
        WHEN 'date' THEN 'date'
        WHEN 'boolean' THEN 'boolean'
        ELSE 'text'
    END;
$func$;

CREATE OR REPLACE FUNCTION pg_temp.is_required(entry jsonb)
RETURNS boolean
LANGUAGE sql
AS $func$
    SELECT CASE jsonb_typeof(entry->'required')
        WHEN 'boolean' THEN (entry->>'required')::boolean
        WHEN 'number' THEN (entry->>'required')::numeric <> 0
        ELSE lower(trim(coalesce(entry->>'required', ''))) IN ('1', 'true', 'yes', 'y', 'required', 't')
    END;
$func$;

CREATE OR REPLACE FUNCTION pg_temp.is_partial(entry jsonb)
RETURNS boolean
LANGUAGE sql
AS $func$
    SELECT CASE
        WHEN coalesce(entry->>'partial', '') IN ('true', 't', '1', 'yes')
            OR entry->'partial' = 'true'::jsonb
            THEN true
        ELSE
            coalesce(entry->>'label', '') = ''
            AND coalesce(entry->>'title', '') = ''
            AND coalesce(entry->>'type', '') = ''
            AND coalesce(entry->>'kind', '') = ''
            AND coalesce(entry->>'field_type', '') = ''
            AND coalesce(entry->>'input_type', '') = ''
    END;
$func$;

CREATE OR REPLACE FUNCTION pg_temp.meta_entries(raw jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $func$
DECLARE
    item jsonb;
    spec jsonb;
    obj_key text;
    out jsonb := '[]'::jsonb;
BEGIN
    IF raw IS NULL OR raw = 'null'::jsonb THEN
        RETURN out;
    END IF;
    IF jsonb_typeof(raw) = 'string' THEN
        BEGIN
            raw := (raw #>> '{}')::jsonb;
        EXCEPTION WHEN others THEN
            RETURN out;
        END;
    END IF;
    IF jsonb_typeof(raw) = 'object' AND jsonb_typeof(raw->'fields') = 'array' THEN
        raw := raw->'fields';
    END IF;
    IF jsonb_typeof(raw) = 'array' THEN
        FOR item IN SELECT value FROM jsonb_array_elements(raw) LOOP
            IF jsonb_typeof(item) = 'object' THEN
                out := out || jsonb_build_array(item);
            END IF;
        END LOOP;
        RETURN out;
    END IF;
    IF jsonb_typeof(raw) = 'object' THEN
        FOR obj_key, spec IN SELECT * FROM jsonb_each(raw) LOOP
            IF obj_key IN ('fields', 'version') THEN
                CONTINUE;
            END IF;
            IF jsonb_typeof(spec) = 'object' THEN
                out := out || jsonb_build_array(jsonb_build_object('key', obj_key) || spec);
            ELSIF jsonb_typeof(spec) = 'string' AND length(trim(spec #>> '{}')) > 0 THEN
                out := out || jsonb_build_array(jsonb_build_object('key', obj_key, 'label', spec #>> '{}'));
            END IF;
        END LOOP;
    END IF;
    RETURN out;
END;
$func$;

CREATE OR REPLACE FUNCTION pg_temp.first_option(raw jsonb)
RETURNS text
LANGUAGE plpgsql
AS $func$
DECLARE
    item jsonb;
    part text;
BEGIN
    IF raw IS NULL OR raw = 'null'::jsonb THEN
        RETURN NULL;
    END IF;
    IF jsonb_typeof(raw) = 'string' THEN
        part := trim(split_part(raw #>> '{}', ',', 1));
        RETURN NULLIF(part, '');
    END IF;
    IF jsonb_typeof(raw) = 'array' THEN
        SELECT value INTO item FROM jsonb_array_elements(raw) LIMIT 1;
        IF item IS NULL THEN
            RETURN NULL;
        END IF;
        IF jsonb_typeof(item) = 'string' THEN
            RETURN NULLIF(trim(item #>> '{}'), '');
        END IF;
        IF jsonb_typeof(item) = 'object' THEN
            RETURN NULLIF(trim(coalesce(item->>'value', item->>'id', item->>'label', item->>'name', '')), '');
        END IF;
        RETURN NULL;
    END IF;
    IF jsonb_typeof(raw) = 'object' THEN
        SELECT e.key INTO part FROM jsonb_each(raw) AS e LIMIT 1;
        RETURN NULLIF(trim(coalesce(part, '')), '');
    END IF;
    RETURN NULL;
END;
$func$;

CREATE OR REPLACE FUNCTION pg_temp.remember_field(fields jsonb, entry jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $func$
DECLARE
    fkey text;
BEGIN
    fkey := pg_temp.field_key(entry);
    IF fkey IS NULL OR fkey = '' THEN
        RETURN fields;
    END IF;
    IF pg_temp.is_partial(entry) THEN
        IF fields ? fkey AND entry ? 'required' THEN
            fields := jsonb_set(
                fields,
                ARRAY[fkey, 'required'],
                to_jsonb(pg_temp.is_required(entry)),
                true
            );
        END IF;
        RETURN fields;
    END IF;
    RETURN fields || jsonb_build_object(
        fkey,
        jsonb_build_object(
            'type', pg_temp.canonical_type(entry),
            'required', pg_temp.is_required(entry),
            'options', COALESCE(entry->'options', 'null'::jsonb)
        )
    );
END;
$func$;

-- Fills any required category field that the row left blank. Explicit
-- listing_meta (pricing, area, deadline, and so on) is left as written.
CREATE OR REPLACE FUNCTION pg_temp.fill_required(p_label text, p_meta jsonb)
RETURNS jsonb
LANGUAGE plpgsql
AS $func$
DECLARE
    child_meta jsonb;
    parent_meta jsonb;
    parent_slug text;
    entry jsonb;
    fields jsonb := '{}'::jsonb;
    result jsonb := COALESCE(p_meta, '{}'::jsonb);
    fkey text;
    spec jsonb;
    existing text;
    filler text;
    ftype text;
BEGIN
    SELECT c.metadata, c.parent_slug
      INTO child_meta, parent_slug
      FROM public.categories c
     WHERE c.label = p_label
     LIMIT 1;
    IF NOT FOUND THEN
        RETURN result;
    END IF;
    IF parent_slug IS NOT NULL THEN
        SELECT metadata INTO parent_meta
          FROM public.categories
         WHERE slug = parent_slug
         LIMIT 1;
    END IF;
    FOR entry IN SELECT value FROM jsonb_array_elements(pg_temp.meta_entries(parent_meta)) LOOP
        fields := pg_temp.remember_field(fields, entry);
    END LOOP;
    FOR entry IN SELECT value FROM jsonb_array_elements(pg_temp.meta_entries(child_meta)) LOOP
        fields := pg_temp.remember_field(fields, entry);
    END LOOP;
    FOR fkey, spec IN SELECT * FROM jsonb_each(fields) LOOP
        IF NOT pg_temp.is_required(spec) THEN
            CONTINUE;
        END IF;
        existing := result->>fkey;
        IF existing IS NOT NULL AND length(trim(existing)) > 0 THEN
            CONTINUE;
        END IF;
        ftype := coalesce(spec->>'type', 'text');
        IF ftype = 'select' THEN
            filler := pg_temp.first_option(spec->'options');
        ELSIF ftype = 'number' THEN
            filler := '1';
        ELSIF ftype = 'boolean' THEN
            filler := 'true';
        ELSIF ftype = 'date' THEN
            filler := to_char(current_date + 21, 'YYYY-MM-DD');
        ELSE
            filler := NULL;
        END IF;
        IF filler IS NULL OR filler = '' THEN
            filler := 'Listed in the description';
        END IF;
        result := result || jsonb_build_object(fkey, filler);
    END LOOP;
    RETURN result;
END;
$func$;

DROP TABLE IF EXISTS pg_temp.seed_listings;
CREATE TEMP TABLE seed_listings (
    id uuid PRIMARY KEY,
    shop_id uuid NOT NULL,
    item_type text NOT NULL,
    title text NOT NULL,
    description text NOT NULL,
    price_ugx numeric NOT NULL,
    category text NOT NULL,
    location_name text NOT NULL,
    listing_meta jsonb NOT NULL
);

INSERT INTO pg_temp.seed_listings (
    id, shop_id, item_type, title, description, price_ugx, category, location_name, listing_meta
) VALUES
(
    '11111111-1111-4111-8111-000000000001',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Phone screen replacement in Kampala',
    $d$Cracked screens on common Android phones are replaced the same day when the part is in stock. You get a short warranty on the new glass and a clear quote before any work starts. WhatsApp 0700 000 101. This number is a placeholder and is not a real contact.$d$,
    80000,
    'Repair & Maintenance',
    'Kampala',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'Weekdays 9am to 6pm, Saturday mornings',
        'service_area', 'Kampala'
    )
),
(
    '11111111-1111-4111-8111-000000000002',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Generator servicing in Entebbe',
    $d$Small petrol generators used in homes and shops are serviced on site, including oil, plugs, and filters. We explain the fault and the cost before the unit is opened. WhatsApp 0700 000 102. This number is a placeholder and is not a real contact.$d$,
    150000,
    'Repair & Maintenance',
    'Entebbe',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'By appointment, including weekends',
        'service_area', 'Entebbe'
    )
),
(
    '11111111-1111-4111-8111-000000000003',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Home cleaning in Ntinda and Naguru',
    $d$A two-person team cleans floors, kitchens, and bathrooms for flats and family homes. You can book a one-off visit or a weekly slot, and the team brings basic supplies. WhatsApp 0700 000 103. This number is a placeholder and is not a real contact.$d$,
    25000,
    'Cleaning Services',
    'Kampala',
    jsonb_build_object(
        'pricing_model', 'hourly',
        'availability', 'Monday to Saturday, 8am to 5pm',
        'service_area', 'Ntinda, Naguru, and Kololo'
    )
),
(
    '11111111-1111-4111-8111-000000000004',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Office cleaning in Jinja town',
    $d$Shops and small offices can book an evening clean so the floor is ready before opening. The quote covers sweeping, mopping, dusting, and emptying bins. WhatsApp 0700 000 104. This number is a placeholder and is not a real contact.$d$,
    120000,
    'Cleaning Services',
    'Jinja',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'Early mornings and after 5pm',
        'service_area', 'Jinja town'
    )
),
(
    '11111111-1111-4111-8111-000000000005',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Same-day parcel delivery in Kampala',
    $d$Documents and small parcels move across Kampala by boda the same day you book. You receive a pickup time and a short note when the parcel is delivered. WhatsApp 0700 000 105. This number is a placeholder and is not a real contact.$d$,
    15000,
    'Delivery & Logistics',
    'Kampala',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', '8am to 7pm, every day',
        'service_area', 'Kampala'
    )
),
(
    '11111111-1111-4111-8111-000000000006',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Upcountry parcel runs from Mbarara',
    $d$Boxed goods and student luggage are collected in Mbarara and handed over in Kampala on the next scheduled run. Fragile items are packed separately, and you are told if something cannot travel. WhatsApp 0700 000 106. This number is a placeholder and is not a real contact.$d$,
    40000,
    'Delivery & Logistics',
    'Mbarara',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'Departures on Monday, Wednesday, and Friday',
        'service_area', 'Mbarara to Kampala'
    )
),
(
    '11111111-1111-4111-8111-000000000007',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Business name registration help',
    $d$New traders get help reserving a business name and preparing the URSB forms. Each official fee is explained before you pay, and documents are returned as soon as the filing is done. WhatsApp 0700 000 107. This number is a placeholder and is not a real contact.$d$,
    150000,
    'Professional & Consulting',
    'Kampala',
    jsonb_build_object(
        'pricing_model', 'fixed',
        'availability', 'Weekday appointments, in town or online',
        'service_area', 'Kampala'
    )
),
(
    '11111111-1111-4111-8111-000000000008',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Monthly bookkeeping for small shops',
    $d$Shop owners send mobile-money statements and photos of receipts, and a simple monthly summary comes back. The pack shows sales, expenses, and what to set aside, written in plain language. WhatsApp 0700 000 108. This number is a placeholder and is not a real contact.$d$,
    100000,
    'Professional & Consulting',
    'Online',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'Reports in the first week of each month',
        'service_area', 'Online'
    )
),
(
    '11111111-1111-4111-8111-000000000009',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Bridal makeup in Kampala',
    $d$Bridal makeup and a trial sitting are done at your home or the venue. The look is agreed at the trial, and a small touch-up kit is left with you for the reception. WhatsApp 0700 000 109. This number is a placeholder and is not a real contact.$d$,
    250000,
    'Beauty & Personal Care Services',
    'Kampala',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'Book at least two weeks ahead',
        'service_area', 'Kampala'
    )
),
(
    '11111111-1111-4111-8111-000000000010',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Barber appointments in Gulu',
    $d$Haircuts and beard trims are done by appointment so you are not left waiting in a full shop. Children and adults are welcome, and the price is confirmed when you book. WhatsApp 0700 000 110. This number is a placeholder and is not a real contact.$d$,
    15000,
    'Beauty & Personal Care Services',
    'Gulu',
    jsonb_build_object(
        'pricing_model', 'fixed',
        'availability', 'Tuesday to Sunday, 9am to 8pm',
        'service_area', 'Gulu town'
    )
),
(
    '11111111-1111-4111-8111-000000000011',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'PLE maths tutoring in Kampala',
    $d$Primary Seven pupils get focused maths practice on topics that usually come up in PLE. Each session ends with a short homework set and a note for the parent on what improved. WhatsApp 0700 000 111. This number is a placeholder and is not a real contact.$d$,
    40000,
    'Education & Tutoring',
    'Kampala',
    jsonb_build_object(
        'pricing_model', 'hourly',
        'availability', 'Weekday evenings and Saturday mornings',
        'service_area', 'Kampala'
    )
),
(
    '11111111-1111-4111-8111-000000000012',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'IELTS speaking practice online',
    $d$Candidates practise IELTS speaking tasks on a video call, with timed answers and direct feedback. You leave with notes on fluency, vocabulary, and what to drill before the next session. WhatsApp 0700 000 112. This number is a placeholder and is not a real contact.$d$,
    50000,
    'Education & Tutoring',
    'Online',
    jsonb_build_object(
        'pricing_model', 'hourly',
        'availability', 'Evenings, seven days',
        'service_area', 'Online'
    )
),
(
    '11111111-1111-4111-8111-000000000013',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Small business website setup',
    $d$Shops that need a simple site get pages for services, prices, and a WhatsApp button. The first version is ready for review within two weeks of receiving your text and logo. WhatsApp 0700 000 113. This number is a placeholder and is not a real contact.$d$,
    600000,
    'Tech & IT Services',
    'Kampala',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'Kickoff calls on weekdays',
        'service_area', 'Kampala and online'
    )
),
(
    '11111111-1111-4111-8111-000000000014',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Laptop repair in Jinja',
    $d$Slow laptops, failing chargers, and broken hinges are diagnosed before any part is ordered. You approve the repair cost, and files stay on the machine unless you ask for a backup. WhatsApp 0700 000 114. This number is a placeholder and is not a real contact.$d$,
    80000,
    'Tech & IT Services',
    'Jinja',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'Drop-off Monday to Saturday',
        'service_area', 'Jinja'
    )
),
(
    '11111111-1111-4111-8111-000000000015',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'MC and DJ for Kampala events',
    $d$Music and a clear programme are handled for kwanjula, introductions, and office parties. A planning call locks the run of show and the sound needs a week before the date. WhatsApp 0700 000 115. This number is a placeholder and is not a real contact.$d$,
    500000,
    'Events & Entertainment',
    'Kampala',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'Friday to Sunday, weekday bookings on request',
        'service_area', 'Kampala'
    )
),
(
    '11111111-1111-4111-8111-000000000016',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Wedding photography in Entebbe',
    $d$Coverage runs from the preparations through the reception, and edited photos are delivered within two weeks. You receive a short set of highlights first, then the full gallery. WhatsApp 0700 000 116. This number is a placeholder and is not a real contact.$d$,
    800000,
    'Photography & Videography',
    'Entebbe',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'Weekend dates book out early',
        'service_area', 'Entebbe and Kampala'
    )
),
(
    '11111111-1111-4111-8111-000000000017',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Product photos for Kampala shops',
    $d$Up to fifteen products are photographed on a plain background for WhatsApp catalogues and online listings. You collect the edited files the next day, sized for a phone screen. WhatsApp 0700 000 117. This number is a placeholder and is not a real contact.$d$,
    150000,
    'Photography & Videography',
    'Kampala',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'Studio days on Tuesday and Thursday',
        'service_area', 'Kampala'
    )
),
(
    '11111111-1111-4111-8111-000000000018',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Tenancy agreement drafting',
    $d$Landlords and tenants get a plain tenancy agreement that covers rent, the deposit, and notice. The draft is explained in a short meeting before anyone signs. WhatsApp 0700 000 118. This number is a placeholder and is not a real contact.$d$,
    120000,
    'Legal & Financial Services',
    'Kampala',
    jsonb_build_object(
        'pricing_model', 'fixed',
        'availability', 'Weekday consultations',
        'service_area', 'Kampala'
    )
),
(
    '11111111-1111-4111-8111-000000000019',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Interior painting in Mbarara',
    $d$Rooms in family houses are prepared and painted, including filling small cracks before the finish coat. The quote follows a site visit and names the paint you will pay for. WhatsApp 0700 000 119. This number is a placeholder and is not a real contact.$d$,
    400000,
    'Home Improvement Services',
    'Mbarara',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'Site visits on weekdays',
        'service_area', 'Mbarara'
    )
),
(
    '11111111-1111-4111-8111-000000000020',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11',
    'service',
    'Home physiotherapy visits in Gulu',
    $d$Adults recovering from an injury can book a home visit for guided exercises and a simple plan to follow. The first session checks what you can already do, and you are referred on if a hospital is the right next step. WhatsApp 0700 000 120. This number is a placeholder and is not a real contact.$d$,
    80000,
    'Health & Wellness Services',
    'Gulu',
    jsonb_build_object(
        'pricing_model', 'starting_at',
        'availability', 'Morning visits, Monday to Friday',
        'service_area', 'Gulu'
    )
),
(
    '22222222-2222-4222-8222-000000000001',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Full-time shop attendant in Nakasero',
    $d$A provisions shop in Nakasero is hiring one attendant for a six-day week. Duties are serving customers, keeping shelves tidy, and recording the day's cash. WhatsApp 0700 000 201. This number is a placeholder and is not a real contact.$d$,
    600000,
    'Full-time Jobs',
    'Kampala',
    jsonb_build_object(
        'opportunity_kind', 'job',
        'compensation', 'paid',
        'deadline', '2026-10-31',
        'requirements', 'Comfortable on your feet, and able to write a simple cash summary.',
        'urgency', 'normal',
        'employment_type', 'Full-time'
    )
),
(
    '22222222-2222-4222-8222-000000000002',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Accounts assistant in Jinja',
    $d$A family trading company in Jinja needs an accounts assistant to post daily sales and prepare a monthly summary. The role is based at the office, with occasional stock counts at the store. WhatsApp 0700 000 202. This number is a placeholder and is not a real contact.$d$,
    800000,
    'Full-time Jobs',
    'Jinja',
    jsonb_build_object(
        'opportunity_kind', 'job',
        'compensation', 'paid',
        'deadline', '2026-11-15',
        'requirements', 'A diploma or working experience in accounts, plus careful mobile-money reconciliation.',
        'urgency', 'normal',
        'employment_type', 'Full-time'
    )
),
(
    '22222222-2222-4222-8222-000000000003',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Weekend waiter in Entebbe',
    $d$A restaurant near Entebbe town needs waiters for Friday and Saturday evenings. You will take orders, serve, and clear tables, and a meal during the shift is provided. WhatsApp 0700 000 203. This number is a placeholder and is not a real contact.$d$,
    20000,
    'Part-time Jobs',
    'Entebbe',
    jsonb_build_object(
        'opportunity_kind', 'job',
        'compensation', 'paid',
        'deadline', '2026-10-30',
        'requirements', 'Available on Friday and Saturday evenings.',
        'urgency', 'urgent',
        'employment_type', 'Part-time'
    )
),
(
    '22222222-2222-4222-8222-000000000004',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Evening receptionist in Mbarara',
    $d$A clinic in Mbarara is hiring a receptionist for weekday evenings. You will book appointments, greet patients, and keep the front desk records in order. WhatsApp 0700 000 204. This number is a placeholder and is not a real contact.$d$,
    450000,
    'Part-time Jobs',
    'Mbarara',
    jsonb_build_object(
        'opportunity_kind', 'job',
        'compensation', 'paid',
        'deadline', '2026-11-30',
        'requirements', 'Clear spoken English and a tidy handwriting for the appointment book.',
        'urgency', 'normal',
        'employment_type', 'Part-time'
    )
),
(
    '22222222-2222-4222-8222-000000000005',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Logo and menu design gig',
    $d$A new cafe needs a simple logo and a one-page menu that prints clearly in black and white. The brief is shared on WhatsApp, and one round of revisions is included. WhatsApp 0700 000 205. This number is a placeholder and is not a real contact.$d$,
    150000,
    'Gigs & Freelance',
    'Online',
    jsonb_build_object(
        'opportunity_kind', 'gig',
        'compensation', 'paid',
        'deadline', '2026-11-15',
        'requirements', 'A portfolio of two or three logo samples.',
        'urgency', 'normal',
        'employment_type', 'Freelance'
    )
),
(
    '22222222-2222-4222-8222-000000000006',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Photographer for a Saturday introduction',
    $d$A family in Kampala needs a photographer for a Saturday introduction ceremony. Coverage is about five hours, and the edited photos should be ready within ten days. WhatsApp 0700 000 206. This number is a placeholder and is not a real contact.$d$,
    350000,
    'Gigs & Freelance',
    'Kampala',
    jsonb_build_object(
        'opportunity_kind', 'gig',
        'compensation', 'paid',
        'deadline', '2026-10-31',
        'requirements', 'Your own camera, and samples from at least one family event.',
        'urgency', 'urgent',
        'employment_type', 'Freelance'
    )
),
(
    '22222222-2222-4222-8222-000000000007',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Marketing intern for a retail shop',
    $d$A clothing shop in Kampala is taking one intern to help with product photos and weekly WhatsApp broadcasts. You will see how the shop prices stock and replies to customers. WhatsApp 0700 000 207. This number is a placeholder and is not a real contact.$d$,
    300000,
    'Internships',
    'Kampala',
    jsonb_build_object(
        'opportunity_kind', 'internship',
        'compensation', 'paid',
        'deadline', '2026-11-30',
        'requirements', 'Available three days a week for three months.',
        'urgency', 'normal',
        'employment_type', 'Internship'
    )
),
(
    '22222222-2222-4222-8222-000000000008',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Remote software internship',
    $d$A software studio is offering a three-month remote internship on an internal tool. Interns join stand-ups online and ship one small feature with a mentor reviewing the work. WhatsApp 0700 000 208. This number is a placeholder and is not a real contact.$d$,
    400000,
    'Internships',
    'Online',
    jsonb_build_object(
        'opportunity_kind', 'internship',
        'compensation', 'paid',
        'deadline', '2026-12-15',
        'requirements', 'You can build a small web page and explain the code you wrote.',
        'urgency', 'normal',
        'employment_type', 'Internship'
    )
),
(
    '22222222-2222-4222-8222-000000000009',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Live-out house help in Kampala',
    $d$A family in Ntinda needs live-out help on weekdays for cleaning, laundry, and a simple lunch. The role does not include childcare, and transport within Ntinda is discussed at the interview. WhatsApp 0700 000 209. This number is a placeholder and is not a real contact.$d$,
    250000,
    'Maids & Domestic Work',
    'Kampala',
    jsonb_build_object(
        'opportunity_kind', 'maids',
        'compensation', 'paid',
        'deadline', '2026-11-15',
        'requirements', 'Experience in a family home, and a reference from a previous employer.',
        'urgency', 'normal',
        'employment_type', 'Full-time'
    )
),
(
    '22222222-2222-4222-8222-000000000010',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Weekday nanny in Entebbe',
    $d$A household in Entebbe is hiring a nanny for one preschool child during the workday. Duties include meals, play, and the school run, with weekends off. WhatsApp 0700 000 210. This number is a placeholder and is not a real contact.$d$,
    400000,
    'Maids & Domestic Work',
    'Entebbe',
    jsonb_build_object(
        'opportunity_kind', 'maids',
        'compensation', 'paid',
        'deadline', '2026-11-30',
        'requirements', 'Experience with children under five, and a light cooking routine.',
        'urgency', 'normal',
        'employment_type', 'Full-time'
    )
),
(
    '22222222-2222-4222-8222-000000000011',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Office cleaning contract in Kampala',
    $d$A three-floor office in Kampala is inviting quotes for weekday evening cleaning. Bidders should state the team size, the supplies included, and a monthly fee in UGX. WhatsApp 0700 000 211. This number is a placeholder and is not a real contact.$d$,
    0,
    'Tenders & Contracts',
    'Kampala',
    jsonb_build_object(
        'opportunity_kind', 'other',
        'compensation', 'negotiable',
        'deadline', '2026-12-01',
        'requirements', 'A registered business and two references from offices you already clean.',
        'urgency', 'normal',
        'employment_type', 'Contract'
    )
),
(
    '22222222-2222-4222-8222-000000000012',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Stationery supply tender in Gulu',
    $d$A training centre in Gulu wants a supplier for paper, pens, and printer toner over six months. Send a price list and your delivery time so the centre can compare quotes before it awards the contract. WhatsApp 0700 000 212. This number is a placeholder and is not a real contact.$d$,
    0,
    'Tenders & Contracts',
    'Gulu',
    jsonb_build_object(
        'opportunity_kind', 'other',
        'compensation', 'negotiable',
        'deadline', '2026-12-15',
        'requirements', 'You can deliver within Gulu town and share a price list for common office items.',
        'urgency', 'normal',
        'employment_type', 'Contract'
    )
),
(
    '22222222-2222-4222-8222-000000000013',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Boutique collaboration in Kampala',
    $d$A boutique in Kampala is looking for a tailor to place a small rack of ready-to-wear pieces on commission. The split and the display space are agreed in writing before any clothes are delivered. WhatsApp 0700 000 213. This number is a placeholder and is not a real contact.$d$,
    0,
    'Partnerships & Collaborations',
    'Kampala',
    jsonb_build_object(
        'opportunity_kind', 'collaboration',
        'compensation', 'commission',
        'deadline', '2026-11-30',
        'requirements', 'A ready-to-wear line you can display, plus photos of recent work.',
        'urgency', 'normal',
        'employment_type', 'Partnership'
    )
),
(
    '22222222-2222-4222-8222-000000000014',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Weekly matooke offtake in Mbarara',
    $d$A buyer in Mbarara wants a regular offtake arrangement with farmers who can deliver matooke each week. Volumes and the buying price are set for a month at a time, not only for a single market day. WhatsApp 0700 000 214. This number is a placeholder and is not a real contact.$d$,
    0,
    'Partnerships & Collaborations',
    'Mbarara',
    jsonb_build_object(
        'opportunity_kind', 'collaboration',
        'compensation', 'negotiable',
        'deadline', '2026-12-01',
        'requirements', 'A steady weekly volume, and the ability to meet at the collection point.',
        'urgency', 'normal',
        'employment_type', 'Partnership'
    )
),
(
    '22222222-2222-4222-8222-000000000015',
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12',
    'opportunity',
    'Saturday reading volunteers in Gulu',
    $d$A community library in Gulu needs volunteers to read with children on Saturday mornings. There is no pay, transport is not covered, and a short orientation is held the week before you start. WhatsApp 0700 000 215. This number is a placeholder and is not a real contact.$d$,
    0,
    'Volunteer & Unpaid',
    'Gulu',
    jsonb_build_object(
        'opportunity_kind', 'other',
        'compensation', 'unpaid',
        'deadline', '2026-11-15',
        'requirements', 'Available on Saturday mornings and comfortable reading aloud to children.',
        'urgency', 'normal',
        'employment_type', 'Volunteer'
    )
);

INSERT INTO public.products (
    id, shop_id, item_type, title, description, price_ugx,
    stock_quantity, image_urls, category, is_published, status,
    location_name, is_negotiable, listing_meta, reviewed_at
)
SELECT
    s.id,
    s.shop_id,
    s.item_type,
    s.title,
    s.description,
    s.price_ugx,
    0,
    '{}'::text[],
    s.category,
    true,
    'active',
    s.location_name,
    true,
    pg_temp.fill_required(s.category, s.listing_meta),
    now()
FROM pg_temp.seed_listings s
ON CONFLICT (id) DO UPDATE SET
    shop_id = EXCLUDED.shop_id,
    item_type = EXCLUDED.item_type,
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    price_ugx = EXCLUDED.price_ugx,
    stock_quantity = 0,
    image_urls = '{}'::text[],
    category = EXCLUDED.category,
    is_published = true,
    status = 'active',
    location_name = EXCLUDED.location_name,
    is_negotiable = true,
    listing_meta = EXCLUDED.listing_meta;

DROP TABLE IF EXISTS pg_temp.seed_listings;

DO $check$
DECLARE
    n_services int;
    n_opps int;
BEGIN
    SELECT count(*) INTO n_services
      FROM public.products
     WHERE shop_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa11'
       AND item_type = 'service'
       AND status = 'active'
       AND is_published
       AND image_urls = '{}'::text[];
    SELECT count(*) INTO n_opps
      FROM public.products
     WHERE shop_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaa12'
       AND item_type = 'opportunity'
       AND status = 'active'
       AND is_published
       AND image_urls = '{}'::text[];
    IF n_services <> 20 OR n_opps <> 15 THEN
        RAISE EXCEPTION 'Seed incomplete: % services, % opportunities', n_services, n_opps;
    END IF;
END
$check$;

COMMIT;
