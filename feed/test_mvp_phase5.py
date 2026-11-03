from feed import location as L
from feed.stages import StagePlan


def _geo(**kw):
    return L.geo_of({"location_name": kw.pop("place", None), "listing_meta": kw})


def test_area_names_and_suburbs_resolve():
    assert L.normalize_area("Kampala") == "kampala"
    assert L.normalize_area("Ntinda") == "kampala"
    assert L.normalize_area("Kira") == "wakiso"
    assert L.normalize_area("All Uganda") == L.ALL_UGANDA
    assert L.normalize_area("") is None


def test_product_location_fit_table():
    assert L.fit("product", _geo(place="Ntinda"), "kampala")[:2] == (100, "local")
    assert L.fit("product", _geo(place="Kira"), "kampala")[:2] == (80, "nearby")
    assert L.fit("product", _geo(place="Mbarara", delivery_areas=["Kampala"]), "kampala") == (75, "nearby", "Delivers to Kampala")
    assert L.fit("product", _geo(place="Gulu", nationwide_delivery=True), "kampala") == (70, "wider", "Delivers nationwide")
    assert L.fit("product", _geo(place="Gulu"), "kampala")[0] == 25
    assert L.fit("product", _geo(), "kampala")[0] == 10


def test_service_location_fit_table():
    assert L.fit("service", _geo(place="Gulu", service_areas=["Wakiso"]), "wakiso") == (100, "local", "Serves Wakiso")
    assert L.fit("service", _geo(place="Kira", travels_to_customer=True), "kampala")[:2] == (80, "nearby")
    assert L.fit("service", _geo(place="Gulu", remote_service=True), "kampala") == (90, "wider", "Remote service")
    assert L.fit("service", _geo(place="Gulu", travels_to_customer=True), "kampala")[0] == 40
    assert L.fit("service", _geo(place="Gulu", service_areas=["Gulu"]), "kampala")[0] == 0


def test_opportunity_location_fit_table():
    assert L.fit("opportunity", _geo(place="Gulu", work_mode="remote"), "kampala") == (100, "wider", "Remote opportunity")
    assert L.fit("opportunity", _geo(place="Kampala"), "kampala")[0] == 100
    assert L.fit("opportunity", _geo(place="Entebbe"), "kampala")[0] == 80
    assert L.fit("opportunity", _geo(place="Gulu", relocation_supported=True), "kampala") == (60, "wider", "Relocation supported")
    assert L.fit("opportunity", _geo(place="Gulu"), "kampala")[0] == 20


def _entry(pid, place, score, lane="product", **meta):
    return {"id": pid, "shop": pid, "lane": lane, "cat": "", "score": score, "quality": 80, "fresh": 0,
            "impressions": 0, "geo": _geo(place=place, **meta)}


def test_location_barely_matters_below_50_listings():
    pool = [_entry("far", "Gulu", 90), _entry("near", "Kampala", 80)]
    groups = L.ring_order("kampala", StagePlan(stage=1, eligible=30))(pool)
    assert groups == [pool] and pool[0]["score"] == 90


def test_rings_order_local_nearby_wider_from_100_listings():
    pool = [_entry("wide", "Gulu", 99), _entry("near", "Kira", 50), _entry("local", "Ntinda", 40)]
    groups = L.ring_order("kampala", StagePlan(stage=2, eligible=150))(pool)
    assert [[e["id"] for e in g] for g in groups] == [["local"], ["near"], ["wide"]]


def test_local_good_card_wins_narrowly_but_distant_excellent_card_stays_close():
    a = _entry("local_a", "Ntinda", 82.0)
    b = _entry("deliverable_b", "Gulu", 85.0, delivery_areas=["Kampala"])
    L.ring_order("kampala", StagePlan(stage=2, eligible=120))([a, b])
    assert a["score"] > b["score"] and a["score"] - b["score"] < 5


def test_query_place_overrides_saved_location():
    assert L.split_query_place("plumber in Masaka") == ("plumber", "Masaka")
    assert L.split_query_place("Toyota Premio near Mbarara") == ("Toyota Premio", "Mbarara")
    assert L.split_query_place("phones ntinda") == ("phones", "ntinda")
    assert L.split_query_place("phones") == ("phones", None)


def test_expanded_cards_say_why_they_are_shown():
    item = {"lane": "service", "geo": _geo(place="Gulu", service_areas=["Wakiso"])}
    assert L.reason_for(item, "wakiso") == "Serves Wakiso"
    assert L.reason_for(item, None) is None and L.reason_for(item, L.ALL_UGANDA) is None


def test_saved_default_area_is_validated():
    from auth.preferences import UserPreferences, UserPreferencesPatch, merge_preferences

    assert UserPreferences.model_validate({"default_area": "Kira"}).default_area == "wakiso"
    assert UserPreferences.model_validate({"default_area": "Narnia"}).default_area is None
    merged = merge_preferences({}, UserPreferencesPatch.model_validate({"default_area": "Entebbe"}))
    assert merged["default_area"] == "entebbe"
    cleared = merge_preferences(merged, UserPreferencesPatch.model_validate({"default_area": None}))
    assert cleared["default_area"] is None
