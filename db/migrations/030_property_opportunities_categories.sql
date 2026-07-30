-- 030_property_opportunities_categories.sql
-- Property & Land + Opportunities parents (for services/opportunities listing types).
-- sort_order slots between Services (6) and Agriculture (7): 61, 62.

INSERT INTO public.categories (slug, label, sort_order, parent_slug) VALUES
  ('property-land', 'Property & Land', 61, NULL),
  ('land-and-plots', 'Land & Plots', 6101, 'property-land'),
  ('houses-for-sale', 'Houses for Sale', 6102, 'property-land'),
  ('houses-for-rent', 'Houses for Rent', 6103, 'property-land'),
  ('apartments-and-flats', 'Apartments & Flats', 6104, 'property-land'),
  ('rooms-and-hostels', 'Rooms & Hostels', 6105, 'property-land'),
  ('commercial-property', 'Commercial Property', 6106, 'property-land'),
  ('warehouses-and-storage', 'Warehouses & Storage', 6107, 'property-land'),
  ('short-stay-and-airbnb', 'Short Stay & Airbnb', 6108, 'property-land'),
  ('opportunities', 'Opportunities', 62, NULL),
  ('full-time-jobs', 'Full-time Jobs', 6201, 'opportunities'),
  ('part-time-jobs', 'Part-time Jobs', 6202, 'opportunities'),
  ('gigs-and-freelance', 'Gigs & Freelance', 6203, 'opportunities'),
  ('internships', 'Internships', 6204, 'opportunities'),
  ('tenders-and-contracts', 'Tenders & Contracts', 6205, 'opportunities'),
  ('partnerships-and-collaborations', 'Partnerships & Collaborations', 6206, 'opportunities'),
  ('volunteer-and-unpaid', 'Volunteer & Unpaid', 6207, 'opportunities')
ON CONFLICT (slug) DO NOTHING;
