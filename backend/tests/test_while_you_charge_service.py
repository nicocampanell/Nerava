"""
Comprehensive tests for While You Charge service

Tests find_chargers_near, find_and_link_merchants, rank_merchants, and helper functions.
"""

from unittest.mock import AsyncMock, patch

import pytest
from app.models_while_you_charge import Charger, ChargerMerchant, Merchant, MerchantPerk
from app.services.geo import haversine_m as haversine_distance
from app.services.while_you_charge import (
    find_and_link_merchants,
    find_chargers_near,
    normalize_query_to_category,
    rank_merchants,
)
from sqlalchemy.orm import Session


class TestHaversineDistance:
    """Test haversine_distance function"""

    def test_haversine_distance_same_point(self):
        """Test haversine_distance returns 0 for same point"""
        result = haversine_distance(30.2672, -97.7431, 30.2672, -97.7431)
        assert result == 0.0

    def test_haversine_distance_different_points(self):
        """Test haversine_distance calculates distance"""
        result = haversine_distance(30.2672, -97.7431, 30.4021, -97.7265)
        assert result > 0
        assert 14000 < result < 16000


class TestNormalizeQueryToCategory:
    """Test normalize_query_to_category function"""

    def test_normalize_query_coffee(self):
        """Test normalize_query_to_category recognizes coffee"""
        category, name = normalize_query_to_category("coffee")
        assert category == "coffee"
        assert name is None

    def test_normalize_query_food(self):
        """Test normalize_query_to_category recognizes food"""
        category, name = normalize_query_to_category("restaurant")
        assert category == "food"
        assert name is None

    def test_normalize_query_merchant_name(self):
        """Test normalize_query_to_category treats unknown as merchant name"""
        category, name = normalize_query_to_category("Starbucks")
        assert category is None
        assert name == "Starbucks"

    def test_normalize_query_groceries(self):
        """Test normalize_query_to_category recognizes groceries"""
        category, name = normalize_query_to_category("grocery store")
        assert category == "groceries"
        assert name is None


class TestFindChargersNear:
    """Test find_chargers_near function"""

    @pytest.mark.asyncio
    async def test_find_chargers_near_in_db(self, db: Session):
        """Test find_chargers_near finds chargers in DB"""
        # Create charger in DB
        charger = Charger(
            id="ch1",
            name="Test Charger",
            network_name="Tesla",
            lat=30.2672,
            lng=-97.7431,
            is_public=True,
            status="available",
        )
        db.add(charger)
        db.commit()

        chargers = await find_chargers_near(
            db=db, user_lat=30.2672, user_lng=-97.7431, radius_m=10000
        )

        assert len(chargers) > 0
        assert any(c.id == "ch1" for c in chargers)

    @pytest.mark.asyncio
    async def test_find_chargers_near_filters_by_drive_time(self, db: Session):
        """Test find_chargers_near filters by drive time"""
        # Create charger far away
        charger = Charger(
            id="ch_far",
            name="Far Charger",
            network_name="Tesla",
            lat=30.5,  # Far from user
            lng=-97.5,
            is_public=True,
            status="available",
        )
        db.add(charger)
        db.commit()

        chargers = await find_chargers_near(
            db=db,
            user_lat=30.2672,
            user_lng=-97.7431,
            radius_m=10000,
            max_drive_minutes=5,  # Very short drive time
        )

        # Far charger should be filtered out
        assert not any(c.id == "ch_far" for c in chargers)

    @pytest.mark.asyncio
    async def test_find_chargers_near_fetches_from_api(self, db: Session):
        """Test find_chargers_near fetches from API when DB empty"""
        # Mock NREL API
        from app.integrations.nrel_client import ChargerData

        mock_charger_data = ChargerData(
            {
                "id": "nrel_123",
                "station_name": "NREL Charger",
                "ev_network": "Tesla",
                "latitude": 30.2672,
                "longitude": -97.7431,
                "street_address": "123 Test St",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
                "ev_connector_types": ["J1772"],
                "access_code": None,
                "status_code": "P",
            }
        )

        with patch(
            "app.services.while_you_charge.fetch_chargers_in_bbox", new_callable=AsyncMock
        ) as mock_fetch:
            mock_fetch.return_value = [mock_charger_data]

            chargers = await find_chargers_near(
                db=db, user_lat=30.2672, user_lng=-97.7431, radius_m=10000
            )

            assert len(chargers) > 0
            mock_fetch.assert_called_once()


class TestFindAndLinkMerchants:
    """Test find_and_link_merchants function"""

    @pytest.mark.asyncio
    async def test_find_and_link_merchants_existing(self, db: Session):
        """Test find_and_link_merchants returns existing merchants"""
        # Create charger and merchant with link
        charger = Charger(
            id="ch1",
            name="Test Charger",
            network_name="Tesla",
            lat=30.2672,
            lng=-97.7431,
            is_public=True,
            status="available",
        )
        db.add(charger)

        merchant = Merchant(
            id="m1", name="Test Merchant", category="coffee", lat=30.2672, lng=-97.7431
        )
        db.add(merchant)

        link = ChargerMerchant(
            charger_id=charger.id,
            merchant_id=merchant.id,
            distance_m=100,
            walk_duration_s=120,
            walk_distance_m=100,
        )
        db.add(link)
        db.commit()

        merchants = await find_and_link_merchants(
            db=db, chargers=[charger], category=None, merchant_name=None, max_walk_minutes=10
        )

        assert len(merchants) > 0
        assert any(m.id == "m1" for m in merchants)

    @pytest.mark.asyncio
    async def test_find_and_link_merchants_filters_by_category(self, db: Session):
        """Test find_and_link_merchants filters by category"""
        charger = Charger(
            id="ch1",
            name="Test Charger",
            network_name="Tesla",
            lat=30.2672,
            lng=-97.7431,
            is_public=True,
            status="available",
        )
        db.add(charger)

        merchant_coffee = Merchant(
            id="m_coffee", name="Coffee Shop", category="coffee", lat=30.2672, lng=-97.7431
        )
        db.add(merchant_coffee)

        merchant_food = Merchant(
            id="m_food", name="Restaurant", category="food", lat=30.2672, lng=-97.7431
        )
        db.add(merchant_food)

        for m in [merchant_coffee, merchant_food]:
            link = ChargerMerchant(
                charger_id=charger.id,
                merchant_id=m.id,
                distance_m=100,
                walk_duration_s=120,
                walk_distance_m=100,
            )
            db.add(link)
        db.commit()

        merchants = await find_and_link_merchants(
            db=db, chargers=[charger], category="coffee", merchant_name=None, max_walk_minutes=10
        )

        assert all(m.category == "coffee" for m in merchants)
        assert not any(m.id == "m_food" for m in merchants)

    @pytest.mark.asyncio
    async def test_find_and_link_merchants_fetches_from_google(self, db: Session):
        """Test find_and_link_merchants fetches from Google when needed"""
        charger = Charger(
            id="ch1",
            name="Test Charger",
            network_name="Tesla",
            lat=30.2672,
            lng=-97.7431,
            is_public=True,
            status="available",
        )
        db.add(charger)
        db.commit()

        # Mock Google Places API
        from app.integrations.google_places_client import PlaceData

        mock_place = PlaceData(
            {
                "place_id": "google_123",
                "name": "Google Place",
                "geometry": {"location": {"lat": 30.2672, "lng": -97.7431}},
                "formatted_address": "123 Test St",
                "rating": 4.5,
                "price_level": 2,
                "types": ["cafe"],
                "icon": "http://icon.url",
            }
        )

        mock_walk_times = {
            ((charger.lat, charger.lng), (mock_place.lat, mock_place.lng)): {
                "status": "OK",
                "duration_s": 300,
                "distance_m": 500,
            }
        }

        with (
            patch(
                "app.services.while_you_charge.search_places_near", new_callable=AsyncMock
            ) as mock_search,
            patch(
                "app.services.while_you_charge.get_walk_times", new_callable=AsyncMock
            ) as mock_walk,
            patch(
                "app.services.while_you_charge.get_place_details", new_callable=AsyncMock
            ) as mock_details,
        ):

            mock_search.return_value = [mock_place]
            mock_walk.return_value = mock_walk_times
            mock_details.return_value = {}

            merchants = await find_and_link_merchants(
                db=db,
                chargers=[charger],
                category="coffee",
                merchant_name=None,
                max_walk_minutes=10,
            )

            assert len(merchants) > 0
            mock_search.assert_called()


class TestRankMerchants:
    """Test rank_merchants function"""

    def test_rank_merchants_basic(self, db: Session):
        """Test rank_merchants ranks merchants correctly"""
        # Create charger
        charger = Charger(
            id="ch1",
            name="Test Charger",
            network_name="Tesla",
            lat=30.2672,
            lng=-97.7431,
            is_public=True,
            status="available",
        )
        db.add(charger)

        # Create merchants
        merchant1 = Merchant(
            id="m1", name="Merchant 1", category="coffee", lat=30.2672, lng=-97.7431, rating=4.5
        )
        merchant2 = Merchant(
            id="m2", name="Merchant 2", category="coffee", lat=30.2673, lng=-97.7432, rating=4.0
        )
        db.add_all([merchant1, merchant2])

        # Create links
        link1 = ChargerMerchant(
            charger_id=charger.id,
            merchant_id=merchant1.id,
            distance_m=100,
            walk_duration_s=120,
            walk_distance_m=100,
        )
        link2 = ChargerMerchant(
            charger_id=charger.id,
            merchant_id=merchant2.id,
            distance_m=200,
            walk_duration_s=240,
            walk_distance_m=200,
        )
        db.add_all([link1, link2])

        # Create perks
        perk1 = MerchantPerk(
            merchant_id=merchant1.id, title="Perk 1", nova_reward=50, is_active=True
        )
        perk2 = MerchantPerk(
            merchant_id=merchant2.id, title="Perk 2", nova_reward=30, is_active=True
        )
        db.add_all([perk1, perk2])
        db.commit()

        ranked = rank_merchants(
            db=db,
            merchants=[merchant1, merchant2],
            chargers=[charger],
            user_lat=30.2672,
            user_lng=-97.7431,
        )

        assert len(ranked) == 2
        # First merchant should rank better (closer, better rating, better perk)
        assert ranked[0]["merchant"].id == "m1"

    def test_rank_merchants_skips_no_link(self, db: Session):
        """Test rank_merchants skips merchants without charger links"""
        charger = Charger(
            id="ch1",
            name="Test Charger",
            network_name="Tesla",
            lat=30.2672,
            lng=-97.7431,
            is_public=True,
            status="available",
        )
        db.add(charger)

        merchant = Merchant(
            id="m1", name="Merchant 1", category="coffee", lat=30.2672, lng=-97.7431
        )
        db.add(merchant)
        db.commit()

        # No link created
        ranked = rank_merchants(
            db=db, merchants=[merchant], chargers=[charger], user_lat=30.2672, user_lng=-97.7431
        )

        assert len(ranked) == 0

    def test_rank_merchants_no_perk_defaults(self, db: Session):
        """Test rank_merchants uses default Nova reward when no perk"""
        charger = Charger(
            id="ch1",
            name="Test Charger",
            network_name="Tesla",
            lat=30.2672,
            lng=-97.7431,
            is_public=True,
            status="available",
        )
        db.add(charger)

        merchant = Merchant(
            id="m1", name="Merchant 1", category="coffee", lat=30.2672, lng=-97.7431
        )
        db.add(merchant)

        link = ChargerMerchant(
            charger_id=charger.id,
            merchant_id=merchant.id,
            distance_m=100,
            walk_duration_s=120,
            walk_distance_m=100,
        )
        db.add(link)
        db.commit()

        ranked = rank_merchants(
            db=db, merchants=[merchant], chargers=[charger], user_lat=30.2672, user_lng=-97.7431
        )

        assert len(ranked) == 1
        assert ranked[0]["nova_reward"] == 10  # Default
