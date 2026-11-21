import unittest
from datetime import date,timedelta
import httpx
from app.web_travel_search import WebTravelSearch,SearchResults
from app.flight_links import fare_search_url
from app.trip_intake import patch_from_text,quote_text


class FreeTravelSearchTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        self.html='''<div class="result"><a class="result__a" href="https://fares.example/singapore-quebec">Singapore to Québec flights</a><a class="result__snippet">Economy return from S$1,500 per adult from Singapore to Québec.</a></div>'''
        def respond(request):
            self.calls.append(request)
            if request.url.host=='geocoding-api.open-meteo.com':
                name=request.url.params['name']
                city='Singapore' if name.casefold()=='singapore' else 'Québec'
                return httpx.Response(200,json={'results':[{'id':1 if city=='Singapore' else 2,'name':city,'feature_code':'PPLC' if city=='Singapore' else 'PPLA','latitude':1,'longitude':2,'country_code':'SG' if city=='Singapore' else 'CA','country':'Singapore' if city=='Singapore' else 'Canada','admin1':'Québec'}]})
            if request.url.host=='html.duckduckgo.com':return httpx.Response(200,text=self.html)
            if request.url.host=='en.wikipedia.org':return httpx.Response(200,json={'query':{'pages':{}}})
            raise AssertionError(str(request.url))
        client=httpx.Client(transport=httpx.MockTransport(respond));self.addCleanup(client.close)
        self.search=WebTravelSearch({},client)
        start=date.today()+timedelta(days=30)
        self.draft=dict(origin='Singapore',destination='Québec',departure=str(start),return_date=str(start+timedelta(days=7)),travellers=2,cabin='ECONOMY',lead_name='Raj')

    def test_no_credentials_needed_and_estimate_is_sourced(self):
        result,errors=self.search.enrich(self.draft)
        self.assertEqual(errors,[])
        self.assertEqual(result['fare']['amount_cents'],300000)
        self.assertTrue(result['fare']['estimate']);self.assertFalse(result['fare']['live'])
        self.assertEqual(result['fare']['source_url'],'https://fares.example/singapore-quebec')
        self.assertIn('estimate',quote_text(result))
        self.assertNotIn('amadeus',' '.join(str(r.url) for r in self.calls))

    def test_typo_proposes_city_without_accepting_it(self):
        result,errors=self.search.enrich(dict(self.draft,destination='Qubec'))
        self.assertNotIn('destination_location',result)
        self.assertIn('Québec',errors[-1]);self.assertIn('Did you mean',errors[-1])

    def test_explicit_destination_without_separator(self):
        fields,errors=patch_from_text('From Singapore, destination Qubec',{},'destination',date.today())
        self.assertEqual(fields['origin'],'Singapore');self.assertEqual(fields['destination'],'Qubec')

    def test_does_not_turn_a_one_way_price_into_a_return_price(self):
        self.html='''<a class="result__a" href="https://fares.example/singapore-quebec">Singapore to Québec S$800</a><a class="result__snippet">Economy one way fares. Find return flights.</a>'''
        result,errors=self.search.enrich(self.draft)
        self.assertNotIn('fare',result);self.assertTrue(errors)

    def test_does_not_invent_a_business_fare_from_economy(self):
        result,errors=self.search.enrich(dict(self.draft,cabin='BUSINESS'))
        self.assertNotIn('fare',result);self.assertTrue(errors)

    def test_challenge_does_not_become_a_price(self):
        self.html='<form id="anomaly-form">challenge</form>'
        result,errors=self.search.enrich(self.draft)
        self.assertNotIn('fare',result)
        self.assertIn('Compare current fares',errors[-1])
        self.assertEqual(httpx.URL(fare_search_url(result)).path, '/search')

    def test_montreal_comparison_matches_google_flights_round_trip_url(self):
        draft = dict(self.draft, origin_location={'name':'Singapore', 'city':'Singapore', 'iata':'SIN'},
                     destination_location={'name':'Montréal', 'city':'Montréal', 'country':'CA'},
                     cabin='BUSINESS', travellers=1, departure='2026-12-05', return_date='2026-12-19')
        self.assertEqual(fare_search_url(draft),
                         'https://www.google.com/travel/flights/search?'
                         'tfs=CBwQAhoeEgoyMDI2LTEyLTA1agcIARIDU0lOcgcIARIDWVVMGh4SCjIwMjYtMTItMTlqBwgBEgNZVUxyBwgBEgNTSU5AAUgDcAGCAQsI____________AZgBAQ&curr=SGD')

    def test_montreal_falls_back_to_compatible_airline_fare_table(self):
        def respond(request):
            if request.url.host == 'html.duckduckgo.com':
                return httpx.Response(202, text='anomaly-form')
            if request.url.host == 'en.wikipedia.org':
                return httpx.Response(200, json={'query': {'pages': {}}})
            if request.url.host == 'www.swiss.com':
                return httpx.Response(200, text='<tr><td>Singapore Montréal Round-trip Economy SGD 1,500</td></tr>')
            if request.url.host == 'www.qatarairways.com':
                return httpx.Response(200, text='<tr><td>Singapore Montréal Round-trip Business SGD 8,000</td></tr>')
            raise AssertionError(str(request.url))

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            search = WebTravelSearch({}, client)
            draft = dict(self.draft, destination='Montréal', cabin='BUSINESS',
                         destination_location={'name':'Montréal', 'city':'Montréal', 'country':'CA'})
            result, errors = search.enrich(draft)
        self.assertEqual(errors, [])
        self.assertEqual(result['fare']['amount_cents'], 1600000)
        self.assertEqual(result['fare']['airlines'], ['Qatar Airways'])

    def test_serpapi_uses_requested_dates_and_the_cheapest_eligible_sgd_return_fare(self):
        def flight(price, cabin, kind='Round trip'):
            return {'price': price, 'type': kind, 'flights': [{'travel_class': cabin, 'airline': 'SWISS'}]}

        def respond(request):
            self.assertEqual(request.url.host, 'serpapi.com')
            params = request.url.params
            self.assertEqual(params['engine'], 'google_flights')
            self.assertEqual(params['departure_id'], 'SIN')
            self.assertEqual(params['arrival_id'], 'YUL')
            self.assertEqual(params['type'], '1')
            self.assertEqual(params['outbound_date'], self.draft['departure'])
            self.assertEqual(params['return_date'], self.draft['return_date'])
            self.assertEqual(params['currency'], 'SGD')
            self.assertEqual(params['travel_class'], '3')
            self.assertEqual(params['adults'], '1')
            return httpx.Response(200, json={
                'search_parameters': {'currency': 'SGD'},
                'best_flights': [flight(3000, 'Business', 'One way'), flight(1000, 'Economy')],
                'other_flights': [flight(7200, 'Business'), flight(6800, 'Business')],
            })

        draft = dict(self.draft, origin_location={'city': 'Singapore', 'iata': 'SIN'},
                     destination_location={'city': 'Montreal', 'country': 'CA'}, cabin='BUSINESS')
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            fare = WebTravelSearch({'SERPAPI_API_KEY': 'demo-key'}, client, allow_demo_fares=True).fare(draft)
        self.assertEqual(fare['amount_cents'], 1360000)
        self.assertEqual(fare['airlines'], ['SWISS'])
        self.assertTrue(fare['outbound_airlines_only'])
        self.assertTrue(fare['estimate'])
        self.assertNotIn('demo-key', fare['source_url'])

    def test_preserves_country_and_region_qualifiers(self):
        fields,errors=patch_from_text('Leaving from Singapore. Destination: Montreal, Canada',{},'origin',date.today())
        self.assertEqual(fields['origin'],'Singapore')
        self.assertEqual(fields['destination'],'Montreal, Canada')
        fields,errors=patch_from_text('To Montreal, Quebec, Canada for two people',{},'destination',date.today())
        self.assertEqual(fields['destination'],'Montreal, Quebec, Canada')
        self.assertEqual(fields['travellers'],2)

    def test_new_trip_is_a_command_not_a_destination(self):
        for text in ('Book another trip','Plan another trip','Book a new itinerary'):
            self.assertEqual(patch_from_text(text,{},'destination',date.today())[0],{})

    def test_matches_country_and_prefers_capital_over_namesake(self):
        def respond(request):
            name=request.url.params['name']
            base={'latitude':1,'longitude':2,'feature_code':'PPL'}
            rows=([
                dict(base,id=1,name='Singapore',country='Singapore',country_code='SG',feature_code='PPLC',population=5000000),
                dict(base,id=2,name='Singapore',country='South Africa',country_code='ZA',admin1='Limpopo',population=50),
            ] if name=='Singapore' else [
                dict(base,id=3,name='Montreal',country='Canada',country_code='CA',admin1='Quebec',population=1700000),
                dict(base,id=4,name='Montréal',country='France',country_code='FR',admin1='Occitanie',population=1000),
                dict(base,id=5,name='Montreal',country='United States',country_code='US',admin1='Wisconsin',population=100),
            ])
            return httpx.Response(200,json={'results':rows})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            search=WebTravelSearch({},client)
            self.assertEqual(search.location('Singapore')['country'],'SG')
            self.assertEqual(search.location('Montreal, Canada')['geonames_id'],3)
            self.assertEqual(search.location('Montréal, Quebec, CA')['geonames_id'],3)
            self.assertEqual(search.location('Montreal, France')['geonames_id'],4)

    def test_similar_sized_cities_still_need_a_country(self):
        def respond(request):
            return httpx.Response(200,json={'results':[dict(id=i,name='London',country=country,country_code=code,feature_code='PPL',population=200000,latitude=1,longitude=2) for i,country,code in [(1,'Canada','CA'),(2,'United Kingdom','GB')]]})
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            search=WebTravelSearch({},client)
            with self.assertRaisesRegex(ValueError,'city and country'):search.location('London')
