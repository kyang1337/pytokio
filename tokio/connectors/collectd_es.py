#!/usr/bin/env python
"""
Retrieve data generated by collectd and stored in ElasticSearch
"""

import json
import datetime
import gzip
from elasticsearch import Elasticsearch

class CollectdEs(object):
    def __init__(self, host, port, index=None, scroll_size='1m', page_size=10000, timeout=30):
        # retain state of ElasticSearch client
        self.client = None
        self.page = None
        self.scroll_pages = []
        self.index = index
        # connection info
        self.connect_host = host
        self.connect_port = port
        self.connect_timeout = timeout
        # for the scroll API
        self.page_size = page_size
        self.scroll_size = scroll_size
        self.scroll_id = None
        # for query_and_scroll
        self.num_flushes = 0
        # hidden parameters to refine how ElasticSearch queries are issued
        self.sort_by = ''

        self.connect()

    def __die__(self):
        self.close()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def connect(self):
        self.client = Elasticsearch(host=self.connect_host,
            port=self.connect_port,
            timeout=self.connect_timeout)

    def close(self):
        if self.client:
            self.client.close()
            self.client = None
               
    def save(self, bundle, output_file=None):
        self._save_bundle(bundle, output_file)

    def query(self, query):
        """
        Issue an ElasticSearch query 
        """
        self.page = self.client.search(body=query, index=self.index, sort=self.sort_by)
        if '_scroll_id' in self.page:
            self.scroll_id = self.page['_scroll_id']
        return self.page

    def scroll(self):
        """
        Issue a follow-up query for a query whose results didn't fall fit in a
        single return page
        """
        if self.scroll_id is None:
            raise Exception('no scroll id')
        self.page = self.client.scroll(scroll_id=self.scroll_id, scroll=self.scroll_size)
        return self.page
            
    def query_and_scroll(self, query, source_filter=True, filter_function=None, flush_every=None, flush_function=None):
        """
        Issue a query and retain all results.  Optional arguments:
            source_filter - True to return all fields contained in each
                            document's _source field; otherwise, a list of
                            _source fields to include
            filter_function - function to call before each set of results is
                              appended to self.scroll_pages; if specified,
                              return value of this function is what is appended
            flush_every - trigger the flush function once the number of docs
                          contained across all self.scroll_pages reaches this
                          value
            flush_function - function to call when self.flush_every docs are
                             retrieved
        """

        def process_page(scroll_state):
            # nonlocal is Python 3 only...
            # nonlocal total_hits
            # nonlocal num_hits_since_flush

            if len(self.page['hits']['hits']) == 0:
                return False

            self.scroll_id = self.page['_scroll_id']
            num_hits = len(self.page['hits']['hits'])
            scroll_state['total_hits'] += num_hits 

            # if this page will push us over flush_every, flush it first
            if flush_function is not None \
            and flush_every \
            and (scroll_state['num_hits_since_flush'] + num_hits) > flush_every:
                flush_function(self)
                scroll_state['num_hits_since_flush'] = 0
                self.num_flushes += 1

            # increment hits since flush only after we've (possibly) flushed
            scroll_state['num_hits_since_flush'] += num_hits

            # if a filter function exists, use its output as the page to append
            if filter_function is None:
                filtered_page = self.page
            else:
                filtered_page = filter_function(self.page)

            # finally append the page
            self.scroll_pages.append(filtered_page)
            return True

        # initialize the scroll state
        self.scroll_pages = []
        # note that we use a dict here because we need to manipulate these
        # values from within a nested function, and Python's scoping rules are
        # rather arbitrary and there's no way to pass individual variables by
        # reference
        scroll_state = {
            'total_hits': 0,
            'num_hits_since_flush': 0
        }

        # Get first set of results and a scroll id
        self.page = self.client.search(
            index=self.index,
            body=query,
            scroll=self.scroll_size,
            size=self.page_size,
            _source=source_filter,
        )

        more_results = process_page(scroll_state)

        while more_results:
            self.page = self.scroll()
            more_results = process_page(scroll_state)
