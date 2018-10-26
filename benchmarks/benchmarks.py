# Write the benchmarking functions here.
# See "Writing benchmarks" in the asv docs for more information.
import asyncio
from .runner import *

class TimeSuite:
    """
    An example benchmark that times the performance of various kinds
    of iterating over dictionaries in Python.
    """
    def setup(self, nworkers =1):
        ports = random_ports(nworkers + 1)
        proxy_port = ports.pop()
        proxy = start_proxy(ConfigurableHTTPProxy, proxy_port)
        asyncio.get_event_loop.run_until_complete(start_proxy)
        self.urls = []
        futures = [
            asyncio.ensure_future(add_worker(proxy, ports.pop())) for i in range(nworkers)
        ]
        print("submitted")

        for f in futures:
            prefix = f
            result = asyncio.get_event_loop().run_until_complete(f)
            self.urls.append(proxy.public_url + prefix)
        self.routes = proxy.get_all_routes()
        asyncio.get_event_loop.run_until_complete(get_all_routes)
        return self.urls, self.routes

    def time_single_run_http(self):
        for url in self.urls:
           single_run_http(url)
    
    def time_single_run_ws(self):
        for url in self.urls:
            single_run_ws(url)

