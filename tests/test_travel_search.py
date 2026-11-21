import unittest
from datetime import date,timedelta
import httpx
from app.travel_search import TravelSearch


class TravelSearchTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        def respond(request):
            self.calls.append(request)
            path=request.url.path
            if path.endswith('/token'):
                return httpx.Response(200,json={"access_token":"fixture","expires_in":1800})
            if path.endswith('/locations'):
                keyword=request.url.params['keyword'].upper()
                cities={"SINGAPORE":("SIN","SG"),"TORONTO":("YTO","CA")}
                found=cities.get(keyword)
                data=[] if not found else [{"subType":"CITY","name":keyword,"iataCode":found[0],"address":{"cityName":keyword,"countryCode":found[1]}}]
                return httpx.Response(200,json={"data":data})
            if path.endswith('/api.php'):
                return httpx.Response(200,json={"query":{"pages":{"1":{"title":"Toronto","fullurl":"https://en.wikipedia.org/wiki/Toronto","pageimage":"Toronto.jpg","thumbnail":{"source":"https://upload.wikimedia.org/toronto.jpg"}}}}})
            offer={"id":"1","price":{"currency":"SGD","grandTotal":"1234.56"},"itineraries":[]}
            if path.endswith('/pricing'):
                return httpx.Response(200,json={"data":{"flightOffers":[offer]}})
            if path.endswith('/flight-offers'):
                return httpx.Response(200,json={"data":[offer]})
            raise AssertionError(str(request.url))
        self.client=httpx.Client(transport=httpx.MockTransport(respond))
        self.addCleanup(self.client.close)
        self.search=TravelSearch({"AMADEUS_CLIENT_ID":"fixture","AMADEUS_CLIENT_SECRET":"fixture","AMADEUS_ENV":"production"},self.client)
        start=date.today()+timedelta(days=30)
        self.draft=dict(origin="Singapore",destination="Toronto",departure=str(start),return_date=str(start+timedelta(days=7)),travellers=2,cabin="BUSINESS",lead_name="Raj")

    def test_validates_locations_reprices_fare_and_fetches_destination_photo(self):
        result,errors=self.search.enrich(self.draft)
        self.assertEqual(errors,[])
        self.assertEqual(result['origin_location']['iata'],'SIN')
        self.assertEqual(result['destination_location']['iata'],'YTO')
        self.assertEqual(result['fare']['amount_cents'],123456)
        self.assertTrue(result['fare']['live'])
        self.assertEqual(result['photo']['title'],'Toronto')
        fare_call=next(r for r in self.calls if r.url.path.endswith('/flight-offers'))
        self.assertEqual(fare_call.url.params['travelClass'],'BUSINESS')
        self.assertEqual(fare_call.url.params['adults'],'2')
        self.assertTrue(any(r.method=='POST' and r.url.path.endswith('/pricing') for r in self.calls))

    def test_country_or_person_name_has_no_validated_destination_or_price(self):
        for value in ('Canada','Raj','Narnia'):
            result,errors=self.search.enrich(dict(self.draft,destination=value))
            self.assertTrue(errors)
            self.assertNotIn('destination_location',result)
            self.assertNotIn('fare',result)

    def test_missing_credentials_cannot_produce_a_price(self):
        result,errors=TravelSearch({},self.client).enrich(self.draft)
        self.assertTrue(any('credentials' in e for e in errors))
        self.assertNotIn('fare',result)

    def test_route_correction_invalidates_existing_price_and_photo(self):
        quoted,_=self.search.enrich(self.draft)
        changed,errors=self.search.enrich(dict(quoted,destination='Narnia'))
        self.assertTrue(errors)
        self.assertNotIn('fare',changed)
        self.assertNotIn('photo',changed)

    def test_cabin_is_required_and_test_fares_are_labelled(self):
        missing=dict(self.draft);missing.pop('cabin')
        draft,errors=self.search.enrich(missing)
        self.assertNotIn('fare',draft)
        self.search.env['AMADEUS_ENV']='test'
        quoted,errors=self.search.enrich(self.draft)
        self.assertFalse(quoted['fare']['live'])
