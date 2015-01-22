from . import session
from . import resources
from . import error

from types import ModuleType
from numbers import Number
import requests
import json
import time

RESOURCE_CLASSES = {}
for name, module in resources.__dict__.items():
    if isinstance(module, ModuleType) and name.capitalize() in module.__dict__:
        RESOURCE_CLASSES[name] = module.__dict__[name.capitalize()]

STATUS_MAP = {}
for name, Klass in error.__dict__.items():
    if isinstance(Klass, type) and issubclass(Klass, error.AsanaError):
        STATUS_MAP[Klass().status] = Klass

class Client:

    DEFAULT_LIMIT = 100

    DEFAULTS = {
        'base_url': 'https://app.asana.com/api/1.0',
        'limit': DEFAULT_LIMIT,
        'poll_interval': 5,
        'retries': 5,
        'retry_delay': 1.0,
        'retry_backoff': 2.0,
        'full_payload': False,
        'iterator_type': 'pages'
    }

    def __init__(self, session=None, auth=None, **options):
        self.session = session or requests.Session()
        self.auth = auth
        self.options = _merge(self.DEFAULTS, options)
        for name, Klass in RESOURCE_CLASSES.items():
            setattr(self, name, Klass(self))

    def request(self, method, path, **options):
        options = self._merge_options(options)
        url = options['base_url'] + path
        retries = float('inf') if options['retries'] == True else options['retries']
        retry_delay = options['retry_delay']
        request_options = self._parse_request_options(options)
        while True:
            try:
                response = getattr(self.session, method)(url, auth=self.auth, **request_options)
                if response.status_code in STATUS_MAP:
                    raise STATUS_MAP[response.status_code](response)
                else:
                    if options['full_payload']:
                        return response.json()
                    else:
                        return response.json()['data']
            except error.RetryableAsanaError as e:
                if retries > 0:
                    retries -= 1
                    if isinstance(e, error.RateLimitEnforcedError):
                        time.sleep(e.retry_after)
                    else:
                        time.sleep(retry_delay)
                        # equivalent to (retry_delay * retry_backoff ^ retry_number):
                        retry_delay = retry_delay * options['retry_backoff']
                else:
                    raise e

    def get(self, path, query, **options):
        api_options = self._parse_api_options(options, query_string=True)
        query_options = self._parse_query_options(options)
        query = _merge(query_options, api_options, query) # options in the query takes precendence
        return self.request('get', path, params=query, **options)

    def get_collection(self, path, query, **options):
        options = self._merge_options(options)
        if options['iterator_type'] == 'pages':
            return self._get_page_iterator(path, query, **options)
        if options['iterator_type'] == 'items':
            return self._get_item_iterator(path, query, **options)
        if options['iterator_type'] == None:
            return self.get(path, query, **options)
        raise Error('Unknown value for "iterator_type" option: ' + str(options['iterator_type']))

    def post(self, path, data, **options):
        body = { 'data': data }
        api_options = self._parse_api_options(options)
        if len(api_options) > 0:
            body['options'] = api_options
        return self.request('post', path, data=json.dumps(body), headers={'content-type': 'application/json'}, **options)

    def put(self, path, data, **options):
        body = { 'data': data }
        api_options = self._parse_api_options(options)
        if len(api_options) > 0:
            body['options'] = api_options
        return self.request('put', path, data=json.dumps(body), headers={'content-type': 'application/json'}, **options)

    def delete(self, path, data, **options):
        return self.request('delete', path, **options)

    def _get_page_iterator(self, path, query, **options):
        return _PageIterator(self, path, query, options)

    def _get_item_iterator(self, path, query, **options):
        for page in self._get_page_iterator(path, query, **options):
            for item in page:
                yield item
        raise StopIteration

    def _merge_options(self, *objects):
        return _merge(self.options, *objects)

    def _parse_query_options(self, options):
        return self._select_options(options, ['limit', 'offset', 'sync'])

    def _parse_api_options(self, options, query_string=False):
        api_options = self._select_options(options, ['pretty', 'fields', 'expand'])
        if query_string:
            query_api_options = {}
            for key in api_options:
                if isinstance(api_options[key], (list, tuple)):
                    query_api_options['opt_'+key] = ','.join(api_options[key])
                else:
                    query_api_options['opt_'+key] = api_options[key]
            return query_api_options
        else:
            return api_options

    def _parse_request_options(self, options):
        request_options = self._select_options(options, ['headers', 'params', 'data'])
        if 'params' in request_options:
            params = request_options['params']
            for key in params:
                if isinstance(params[key], bool):
                    params[key] = json.dumps(params[key])
        return request_options

    def _select_options(self, options, keys):
        options = self._merge_options(options)
        return { key: options[key] for key in keys if key in options }

    @classmethod
    def basic_auth(Klass, apiKey):
        return Klass(auth=requests.auth.HTTPBasicAuth(apiKey, ''))

    @classmethod
    def oauth(Klass, **kwargs):
        return Klass(session.AsanaOAuth2Session(**kwargs))

class _PageIterator:
    def __init__(self, client, path, query, options):
        self.client = client
        self.path = path
        self.query = query
        self.options = _merge(options, { 'full_payload': True })
        self.next_page = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.next_page != None:
            if self.next_page == False:
                result = self.client.get(self.path, self.query, **self.options)
            else:
                self.options.pop('offset', None) # if offset was set delete it because it will conflict
                result = self.client.get(self.next_page['path'], {}, **self.options)
            self.next_page = result.get('next_page', None)
            return result['data']
        else:
            raise StopIteration

    def next(self):
        return self.__next__()

def _merge(*objects):
    result = {}
    [result.update(obj) for obj in objects]
    return result
