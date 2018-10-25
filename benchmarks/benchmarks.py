# Write the benchmarking functions here.
# See "Writing benchmarks" in the asv docs for more information.
from .runner import *

class TimeSuite:
    """
    An example benchmark that times the performance of various kinds
    of iterating over dictionaries in Python.
    """
    def setup(self, nworkers =1):
        self.ports = random_ports(self.nworkers + 1)
        self.proxy_port = ports.pop()
        self.proxy = start_proxy(ConfigurableHTTPProxy, self.proxy_port)
        self.urls = []
        self.futures = [
            asyncio.ensure_future(add_worker(self.proxy, self.ports.pop())) for i in range(self.nworkers)
        ]
        print("submitted")
        for f in self.futures:
            self.prefix = f
            self.urls.append(self.proxy.public_url + self.prefix)
        self.routes = proxy.get_all_routes()
        return self.urls, self.routes

    def time_single_run_http(self):
        for url in self.urls:
           single_run_http(url)
    
    def time_single_run_ws(self):
        for url in self.urls:
            single_run_ws(url)


